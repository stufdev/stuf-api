"""
normalize_odds_snapshots.py — STUF Odds Snapshot Normalizer

Reads fixture_odds_snapshots JSONB payloads, resolves each bet value via
api_bet_to_market_map, and writes to four target tables:

  fixture_market_odds                  resolved, clean prices (canonical form)
  fixture_odds_unmapped                audit table — bets that could not be resolved
  market_bookmaker_coverage_observed   tracks what coverage was actually seen
  fixture_market_decision_cards        per-fixture × market × selection decision state

Resolution modes (per api_bet_to_market_map row):
  exact      extract_line_from_match=false; value_pattern is a regex applied to bet_value;
             market_key is set explicitly in the map row.
  inferred   extract_line_from_match=true; capture group 1 of value_pattern is the numeric
             line; market_key is resolved at runtime via market_definitions lookup:
               SELECT key FROM market_definitions
               WHERE category = target_category
                 AND operator = canonical_selection
                 AND line = <extracted_line>
                 AND is_active = true
             If zero or >1 keys match → bet routes to fixture_odds_unmapped.

Unmapped reasons:
  no_pattern_match         No active rule in api_bet_to_market_map matched
  line_not_in_definitions  Line extracted but (category, operator, line) not in
                           market_definitions, OR found multiple matches (ambiguous)
  market_key_inactive      Resolved key exists but market_definitions.is_active = false
  policy_disabled          Key active but market_price_policy.decision_mode = 'disabled'

Idempotency:
  For each snapshot processed, existing rows in fixture_market_odds and
  fixture_odds_unmapped for that (fixture_id, bookmaker_id, market_scope,
  captured_at) are deleted before re-inserting.

Decision cards:
  All existing fixture_market_decision_cards for each processed fixture are
  deleted and rebuilt from scratch at the end of each run.

Usage:
  python normalize_odds_snapshots.py --season 2026 --leagues 1
  python normalize_odds_snapshots.py --season 2026 --leagues 1 --fixture-ids 12345,12346
  python normalize_odds_snapshots.py --season 2026 --leagues 1 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from pipeline_core import (
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_iso_datetime,
    parse_target_leagues,
    utcnow,
)

LOGGER = configure_logging("stuf.normalizer")

# ─── Repository extension ────────────────────────────────────────────────────


class NormalizerRepository(StufRepository):
    """Extends StufRepository with normalizer-specific read/write methods."""

    # ── Reference data loaders ────────────────────────────────────────────

    def load_api_bet_map(self) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("api_bet_to_market_map")
            .select(
                "id,api_bet_id,api_bet_scope,value_pattern,extract_line_from_match,"
                "canonical_selection,market_key,target_category,target_family,"
                "mapping_confidence,bookmaker_id,league_id,active"
            )
            .eq("active", True),
            "load api_bet_to_market_map",
        )
        return response.data or []

    def load_market_definitions(self) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("market_definitions")
            .select("key,category,family,operator,line,subject,is_active"),
            "load market_definitions",
        )
        return response.data or []

    def load_market_price_policy(self) -> list[dict[str, Any]]:
        response = self._execute(
            lambda: self.supabase.table("market_price_policy")
            .select(
                "id,market_key,priceable_tier,decision_mode,"
                "reference_bookmaker_names,fallback_bookmaker_names,"
                "requires_lineup_confirmation,requires_snapshot_history_min,"
                "freshness_window_hours,active"
            )
            .eq("active", True),
            "load market_price_policy",
        )
        return response.data or []

    def load_fixtures_for_scope(
        self,
        league_ids: tuple[int, ...],
        season: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch in chunked(list(league_ids), 50):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixtures")
                .select("id,league_id,season,home_team_id,away_team_id,date")
                .in_("league_id", list(batch))
                .eq("season", season),
                f"load fixtures leagues={batch} season={season}",
            )
            rows.extend(response.data or [])
        return rows

    def load_snapshots_for_fixtures(
        self,
        fixture_ids: list[int],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for batch in chunked(fixture_ids, 100):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixture_odds_snapshots")
                .select("id,fixture_id,bookmaker_id,bookmaker_name,market_scope,captured_at,payload")
                .in_("fixture_id", list(batch))
                .order("captured_at", desc=False),
                f"load snapshots batch={len(batch)}",
            )
            rows.extend(response.data or [])
        return rows

    def load_normalized_odds_for_fixtures(
        self,
        fixture_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        """Returns fixture_market_odds rows grouped by fixture_id."""
        rows_by_fixture: dict[int, list[dict[str, Any]]] = {}
        for batch in chunked(fixture_ids, 100):
            response = self._execute(
                lambda batch=batch: self.supabase.table("fixture_market_odds")
                .select(
                    "fixture_id,market_key,selection,line,price,bookmaker_name,"
                    "market_scope,captured_at,mapping_confidence"
                )
                .in_("fixture_id", list(batch)),
                f"load normalized odds batch={len(batch)}",
            )
            for row in response.data or []:
                fid = row["fixture_id"]
                rows_by_fixture.setdefault(fid, []).append(row)
        return rows_by_fixture

    # ── Snapshot-scoped deletes (idempotency) ─────────────────────────────

    def delete_normalized_odds_for_snapshot(
        self,
        fixture_id: int,
        bookmaker_id: int | None,
        market_scope: str,
        captured_at: str,
    ) -> None:
        def request():
            q = (
                self.supabase.table("fixture_market_odds")
                .delete()
                .eq("fixture_id", fixture_id)
                .eq("market_scope", market_scope)
                .eq("captured_at", captured_at)
            )
            if bookmaker_id is not None:
                q = q.eq("bookmaker_id", bookmaker_id)
            return q

        self._execute(
            request,
            f"delete market_odds fixture={fixture_id} scope={market_scope} at={captured_at}",
        )

    def delete_unmapped_for_snapshot(
        self,
        fixture_id: int,
        bookmaker_id: int | None,
        market_scope: str,
        captured_at: str,
    ) -> None:
        def request():
            q = (
                self.supabase.table("fixture_odds_unmapped")
                .delete()
                .eq("fixture_id", fixture_id)
                .eq("market_scope", market_scope)
                .eq("captured_at", captured_at)
            )
            if bookmaker_id is not None:
                q = q.eq("bookmaker_id", bookmaker_id)
            return q

        self._execute(
            request,
            f"delete unmapped fixture={fixture_id} scope={market_scope} at={captured_at}",
        )

    # ── Batch inserts ─────────────────────────────────────────────────────

    def insert_market_odds_batch(self, rows: list[dict[str, Any]]) -> None:
        for batch in chunked(rows, 500):
            self._execute(
                lambda batch=batch: self.supabase.table("fixture_market_odds").insert(list(batch)),
                f"insert market_odds batch={len(batch)}",
            )

    def insert_unmapped_batch(self, rows: list[dict[str, Any]]) -> None:
        for batch in chunked(rows, 500):
            self._execute(
                lambda batch=batch: self.supabase.table("fixture_odds_unmapped").insert(list(batch)),
                f"insert unmapped batch={len(batch)}",
            )

    # ── Coverage tracking ─────────────────────────────────────────────────

    def upsert_coverage_observed(
        self,
        market_key: str,
        bookmaker_name: str,
        league_id: int,
        season: int,
        market_scope: str,
        fixture_count_with_price: int,
        fixture_count_sampled: int,
    ) -> None:
        """
        Sets (not increments) coverage counts for a full-scope run.
        On conflict, updates counts and last_updated_at but preserves first_observed_at.
        """
        if fixture_count_sampled <= 0:
            return

        # Read first to preserve first_observed_at on update.
        existing = self._execute(
            lambda: self.supabase.table("market_bookmaker_coverage_observed")
            .select("id,first_observed_at")
            .eq("market_key", market_key)
            .eq("bookmaker_name", bookmaker_name)
            .eq("league_id", league_id)
            .eq("season", season)
            .eq("market_scope", market_scope)
            .limit(1),
            f"load coverage key={market_key} bm={bookmaker_name}",
        )
        existing_rows = existing.data or []

        now_iso = utcnow().isoformat()
        if existing_rows:
            row_id = existing_rows[0]["id"]
            self._execute(
                lambda: self.supabase.table("market_bookmaker_coverage_observed")
                .update({
                    "fixture_count_with_price": fixture_count_with_price,
                    "fixture_count_sampled": fixture_count_sampled,
                    "last_updated_at": now_iso,
                })
                .eq("id", row_id),
                f"update coverage id={row_id}",
            )
        else:
            self._execute(
                lambda: self.supabase.table("market_bookmaker_coverage_observed").insert({
                    "market_key": market_key,
                    "bookmaker_name": bookmaker_name,
                    "league_id": league_id,
                    "season": season,
                    "market_scope": market_scope,
                    "fixture_count_with_price": fixture_count_with_price,
                    "fixture_count_sampled": fixture_count_sampled,
                    "first_observed_at": now_iso,
                    "last_updated_at": now_iso,
                }),
                f"insert coverage key={market_key} bm={bookmaker_name}",
            )

    # ── Decision cards ────────────────────────────────────────────────────

    def delete_decision_cards_for_fixtures(self, fixture_ids: list[int]) -> None:
        for batch in chunked(fixture_ids, 100):
            self._execute(
                lambda batch=batch: self.supabase.table("fixture_market_decision_cards")
                .delete()
                .in_("fixture_id", list(batch)),
                f"delete decision_cards batch={len(batch)}",
            )

    def insert_decision_cards_batch(self, rows: list[dict[str, Any]]) -> None:
        for batch in chunked(rows, 500):
            self._execute(
                lambda batch=batch: self.supabase.table("fixture_market_decision_cards").insert(list(batch)),
                f"insert decision_cards batch={len(batch)}",
            )


# ─── Context builders ─────────────────────────────────────────────────────────


def build_market_def_index(
    market_defs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[tuple, list[str]], dict[tuple, list[str]]]:
    """
    Returns three indexes:
    1. by_key           {market_key: definition_row}
    2. by_cat_op_line   {(category, operator, line_as_str): [market_key, ...]}
                        e.g. ("goals", "over", "2.5") → ["MATCH_OVER_2_5_GOALS"]
    3. by_family_op_line {(family, operator, line_as_str): [market_key, ...]}
                        Used when api_bet_to_market_map.target_family is set.
                        Avoids ambiguity where multiple market families share the
                        same category at overlapping lines (e.g. match_cards vs
                        team_cards, both category='cards').
    """
    by_key: dict[str, dict[str, Any]] = {}
    by_cat_op_line: dict[tuple, list[str]] = {}
    by_family_op_line: dict[tuple, list[str]] = {}

    for row in market_defs:
        key = row["key"]
        by_key[key] = row

        cat = row.get("category")
        op = row.get("operator")
        line_raw = row.get("line")

        if not cat or not op or line_raw is None:
            continue

        line_str = _normalize_line_str(line_raw)

        by_cat_op_line.setdefault((cat, op, line_str), []).append(key)

        family = row.get("family")
        if family:
            by_family_op_line.setdefault((family, op, line_str), []).append(key)

    return by_key, by_cat_op_line, by_family_op_line


def build_policy_index(
    policy_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Returns {market_key: policy_row} for the global (league_id_scope IS NULL) policy."""
    # The 020 seeds only have global policies. League overrides can be added later.
    return {row["market_key"]: row for row in policy_rows}


def build_bet_map_index(
    bet_map_rows: list[dict[str, Any]],
) -> dict[tuple, list[dict[str, Any]]]:
    """
    Returns {(api_bet_id, api_bet_scope): [rule, ...]} sorted so bookmaker-specific
    rules are tried before universal (null bookmaker_id) rules.
    """
    index: dict[tuple, list[dict[str, Any]]] = {}
    for row in bet_map_rows:
        key = (row["api_bet_id"], row["api_bet_scope"])
        index.setdefault(key, []).append(row)

    for rules in index.values():
        # Bookmaker-specific rules first (bookmaker_id IS NOT NULL)
        rules.sort(key=lambda r: (r["bookmaker_id"] is None, r["id"]))

    return index


# ─── Resolution logic ─────────────────────────────────────────────────────────


def _normalize_line_str(value: Any) -> str:
    """
    Normalises a numeric line to a canonical string key.
    2.5 → "2.5", 2 → "2.0", "2.50" → "2.5"
    """
    try:
        f = float(value)
        # Remove trailing zeros: 2.50 → "2.5", 8.00 → "8.0"
        return f"{f:g}" if f != int(f) else f"{f:.1f}"
    except (TypeError, ValueError):
        return str(value)


def _parse_price(value: Any) -> float | None:
    """Parse an odds string/number. Returns None if invalid or <= 1."""
    try:
        price = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if price <= 1.0:
        return None
    return price


def resolve_bet_value(
    api_bet_id: int,
    api_bet_scope: str,
    bet_value: str,
    bookmaker_id: int | None,
    bet_map_index: dict[tuple, list[dict[str, Any]]],
    market_def_by_cat_op_line: dict[tuple, list[str]],
    market_def_by_key: dict[str, dict[str, Any]],
    market_def_by_family_op_line: dict[tuple, list[str]] | None = None,
) -> dict[str, Any] | None:
    """
    Attempts to resolve (api_bet_id, bet_value) to a canonical STUF
    (market_key, selection, line, mapping_confidence).

    Returns a dict with resolution fields, or None if no match.
    Callers that receive None should route to fixture_odds_unmapped.

    Resolution priority for inferred (line-extraction) rules:
      1. Explicit market_key on the rule → direct lookup.
      2. target_family → by_family_op_line[(family, op, line)] → unambiguous when
         multiple market families share category at overlapping lines.
      3. target_category → by_cat_op_line[(category, op, line)] → fails if >1 match.
    """
    rules = bet_map_index.get((api_bet_id, api_bet_scope), [])
    if not rules:
        return None

    if market_def_by_family_op_line is None:
        market_def_by_family_op_line = {}

    for rule in rules:
        pattern = rule["value_pattern"]

        if rule["extract_line_from_match"]:
            # Regex mode: capture group 1 = numeric line
            m = re.fullmatch(pattern, bet_value, re.IGNORECASE)
            if not m:
                continue
            try:
                extracted_line = float(m.group(1))
            except (IndexError, ValueError):
                LOGGER.warning(
                    "Regex group 1 not numeric: pattern=%s value=%s bet_id=%s",
                    pattern, bet_value, api_bet_id,
                )
                continue

            line_str = _normalize_line_str(extracted_line)

            # Priority 1: explicit market_key on the rule
            if rule.get("market_key"):
                mdef = market_def_by_key.get(rule["market_key"])
                if not mdef or not mdef.get("is_active"):
                    return {"_unmapped_reason": "market_key_inactive"}
                return {
                    "market_key": rule["market_key"],
                    "canonical_selection": rule["canonical_selection"],
                    "line": extracted_line,
                    "mapping_confidence": rule["mapping_confidence"],
                }

            # Priority 2: family-scoped lookup (avoids category-level ambiguity)
            if rule.get("target_family"):
                index_key = (rule["target_family"], rule["canonical_selection"], line_str)
                matching_keys = market_def_by_family_op_line.get(index_key, [])

                if len(matching_keys) == 1:
                    mkey = matching_keys[0]
                    mdef = market_def_by_key.get(mkey)
                    if not mdef or not mdef.get("is_active"):
                        return {"_unmapped_reason": "market_key_inactive"}
                    return {
                        "market_key": mkey,
                        "canonical_selection": rule["canonical_selection"],
                        "line": extracted_line,
                        "mapping_confidence": rule["mapping_confidence"],
                    }
                elif len(matching_keys) > 1:
                    LOGGER.debug(
                        "Ambiguous family lookup: family=%s op=%s line=%s "
                        "candidates=%s bet_id=%s value=%s",
                        rule["target_family"], rule["canonical_selection"],
                        line_str, matching_keys, api_bet_id, bet_value,
                    )
                    return {"_unmapped_reason": "line_not_in_definitions"}
                else:
                    LOGGER.debug(
                        "No market_key for family=%s op=%s line=%s bet_id=%s value=%s",
                        rule["target_family"], rule["canonical_selection"],
                        line_str, api_bet_id, bet_value,
                    )
                    return {"_unmapped_reason": "line_not_in_definitions"}

            # Priority 3: category lookup (only safe when no ambiguity at this line)
            if rule.get("target_category"):
                index_key = (rule["target_category"], rule["canonical_selection"], line_str)
                matching_keys = market_def_by_cat_op_line.get(index_key, [])

                if len(matching_keys) == 1:
                    mkey = matching_keys[0]
                    mdef = market_def_by_key.get(mkey)
                    if not mdef or not mdef.get("is_active"):
                        return {"_unmapped_reason": "market_key_inactive"}
                    return {
                        "market_key": mkey,
                        "canonical_selection": rule["canonical_selection"],
                        "line": extracted_line,
                        "mapping_confidence": rule["mapping_confidence"],
                    }
                elif len(matching_keys) > 1:
                    LOGGER.debug(
                        "Ambiguous market_key lookup: category=%s op=%s line=%s "
                        "candidates=%s bet_id=%s value=%s",
                        rule["target_category"], rule["canonical_selection"],
                        line_str, matching_keys, api_bet_id, bet_value,
                    )
                    return {"_unmapped_reason": "line_not_in_definitions"}
                else:
                    LOGGER.debug(
                        "No market_key for category=%s op=%s line=%s bet_id=%s value=%s",
                        rule["target_category"], rule["canonical_selection"],
                        line_str, api_bet_id, bet_value,
                    )
                    return {"_unmapped_reason": "line_not_in_definitions"}

        else:
            # Exact / pattern match (no line extraction)
            m = re.fullmatch(pattern, bet_value, re.IGNORECASE)
            if not m:
                continue
            if not rule.get("market_key"):
                continue
            mdef = market_def_by_key.get(rule["market_key"])
            if not mdef or not mdef.get("is_active"):
                return {"_unmapped_reason": "market_key_inactive"}
            return {
                "market_key": rule["market_key"],
                "canonical_selection": rule["canonical_selection"],
                "line": None,
                "mapping_confidence": rule["mapping_confidence"],
            }

    return None  # no rule matched


def normalize_snapshot(
    snapshot: dict[str, Any],
    fixture_row: dict[str, Any],
    bet_map_index: dict[tuple, list[dict[str, Any]]],
    market_def_by_cat_op_line: dict[tuple, list[str]],
    market_def_by_key: dict[str, dict[str, Any]],
    policy_index: dict[str, dict[str, Any]],
    market_def_by_family_op_line: dict[tuple, list[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Processes one fixture_odds_snapshots row.

    Returns (resolved_rows, unresolved_rows).
    resolved_rows   → insert into fixture_market_odds
    unresolved_rows → insert into fixture_odds_unmapped
    """
    fixture_id = snapshot["fixture_id"]
    snapshot_bookmaker_id = snapshot.get("bookmaker_id")
    snapshot_bookmaker_name = snapshot.get("bookmaker_name", "")
    market_scope = snapshot.get("market_scope", "prematch")
    captured_at = snapshot["captured_at"]
    payload = snapshot.get("payload") or {}

    league_id = fixture_row.get("league_id")
    season = fixture_row.get("season")

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    bookmakers_in_payload = payload.get("bookmakers") or []

    for bm_entry in bookmakers_in_payload:
        bm_id = bm_entry.get("id")
        bm_name = bm_entry.get("name") or snapshot_bookmaker_name
        bets = bm_entry.get("bets") or []

        # Use bookmaker_id from snapshot if not in payload entry
        effective_bm_id = bm_id if bm_id is not None else snapshot_bookmaker_id
        effective_bm_name = bm_name or snapshot_bookmaker_name

        for bet in bets:
            api_bet_id = bet.get("id")
            api_bet_name = bet.get("name", "")
            if api_bet_id is None:
                continue

            values = bet.get("values") or []

            for val_entry in values:
                bet_value = str(val_entry.get("value") or "").strip()
                price_raw = val_entry.get("odd")
                price = _parse_price(price_raw)

                if not bet_value:
                    continue

                resolution = resolve_bet_value(
                    api_bet_id=api_bet_id,
                    api_bet_scope=market_scope,
                    bet_value=bet_value,
                    bookmaker_id=effective_bm_id,
                    bet_map_index=bet_map_index,
                    market_def_by_cat_op_line=market_def_by_cat_op_line,
                    market_def_by_key=market_def_by_key,
                    market_def_by_family_op_line=market_def_by_family_op_line,
                )

                if resolution is None:
                    # No pattern matched at all
                    unresolved.append({
                        "fixture_id": fixture_id,
                        "bookmaker_id": effective_bm_id,
                        "bookmaker_name": effective_bm_name,
                        "api_bet_id": api_bet_id,
                        "api_bet_name": api_bet_name,
                        "bet_value": bet_value,
                        "price": price,
                        "market_scope": market_scope,
                        "captured_at": captured_at,
                        "unmapped_reason": "no_pattern_match",
                    })
                    continue

                unmapped_reason = resolution.get("_unmapped_reason")
                if unmapped_reason:
                    unresolved.append({
                        "fixture_id": fixture_id,
                        "bookmaker_id": effective_bm_id,
                        "bookmaker_name": effective_bm_name,
                        "api_bet_id": api_bet_id,
                        "api_bet_name": api_bet_name,
                        "bet_value": bet_value,
                        "price": price,
                        "market_scope": market_scope,
                        "captured_at": captured_at,
                        "unmapped_reason": unmapped_reason,
                    })
                    continue

                market_key = resolution["market_key"]
                selection = resolution["canonical_selection"]
                line = resolution.get("line")
                mapping_confidence = resolution.get("mapping_confidence", "exact")

                # Check policy: if decision_mode = 'disabled' → unmapped with policy_disabled
                policy = policy_index.get(market_key)
                if policy and policy.get("decision_mode") == "disabled":
                    unresolved.append({
                        "fixture_id": fixture_id,
                        "bookmaker_id": effective_bm_id,
                        "bookmaker_name": effective_bm_name,
                        "api_bet_id": api_bet_id,
                        "api_bet_name": api_bet_name,
                        "bet_value": bet_value,
                        "price": price,
                        "market_scope": market_scope,
                        "captured_at": captured_at,
                        "unmapped_reason": "policy_disabled",
                    })
                    continue

                if price is None:
                    LOGGER.debug(
                        "Skipping resolved bet with invalid price: fixture=%s "
                        "bet_id=%s value=%s price_raw=%s",
                        fixture_id, api_bet_id, bet_value, price_raw,
                    )
                    continue

                resolved.append({
                    "fixture_id": fixture_id,
                    "league_id": league_id,
                    "season": season,
                    "bookmaker_id": effective_bm_id,
                    "bookmaker_name": effective_bm_name,
                    "api_bet_id": api_bet_id,
                    "api_bet_scope": market_scope,
                    "api_bet_name": api_bet_name,
                    "market_key": market_key,
                    "selection": selection,
                    "line": line,
                    "price": price,
                    "is_main": True,
                    "market_scope": market_scope,
                    "captured_at": captured_at,
                    "raw_payload": val_entry,
                    "mapping_confidence": mapping_confidence,
                })

    return resolved, unresolved


# ─── Coverage tracking ────────────────────────────────────────────────────────


def compute_coverage_events(
    all_resolved: list[dict[str, Any]],
    fixture_ids_in_scope: set[int],
) -> dict[tuple, dict[str, Any]]:
    """
    Computes per-(market_key, bookmaker_name, league_id, season, market_scope)
    coverage counts.

    Returns a dict keyed by (market_key, bookmaker_name, league_id, season, market_scope)
    with fixture_count_with_price and fixture_count_sampled.
    """
    # Track which fixtures had a price for each (market_key, bookmaker_name) combo
    fixtures_with_price: dict[tuple, set[int]] = defaultdict(set)

    for row in all_resolved:
        cov_key = (
            row["market_key"],
            row["bookmaker_name"],
            row.get("league_id"),
            row.get("season"),
            row.get("market_scope", "prematch"),
        )
        fixtures_with_price[cov_key].add(row["fixture_id"])

    coverage: dict[tuple, dict[str, Any]] = {}
    for cov_key, fixture_set in fixtures_with_price.items():
        market_key, bm_name, league_id, season, market_scope = cov_key
        if league_id is None or season is None:
            continue
        coverage[cov_key] = {
            "market_key": market_key,
            "bookmaker_name": bm_name,
            "league_id": league_id,
            "season": season,
            "market_scope": market_scope,
            "fixture_count_with_price": len(fixture_set),
            "fixture_count_sampled": len(fixture_ids_in_scope),
        }

    return coverage


# ─── Decision card builder ────────────────────────────────────────────────────


def determine_decision_status(
    market_key: str,
    selection: str,
    line: float | None,
    policy: dict[str, Any],
    odds_rows: list[dict[str, Any]],
    now_iso: str,
) -> tuple[str, str, dict[str, Any]]:
    """
    Returns (decision_status, price_source_quality, price_info_dict).

    price_info_dict keys: reference_bookmaker, reference_price,
      reference_captured_at, mapping_confidence, snapshot_count,
      latest_snapshot_at.
    """
    tier = policy.get("priceable_tier", "stat_signal_only")
    mode = policy.get("decision_mode", "disabled")
    freshness_hours = policy.get("freshness_window_hours", 48)
    min_snapshots = policy.get("requires_snapshot_history_min", 1)
    ref_bookmakers: list[str] = policy.get("reference_bookmaker_names") or []
    fallback_bookmakers: list[str] = policy.get("fallback_bookmaker_names") or []

    if mode == "disabled":
        if tier == "stat_signal_only":
            return "stat_signal_only", "none", {}
        # context_only → no card (caller skips)
        return "stat_signal_only", "none", {}

    if not odds_rows:
        return "no_odds_available", "none", {
            "snapshot_count": 0,
        }

    now_dt = parse_iso_datetime(now_iso)
    freshness_cutoff = now_dt - timedelta(hours=freshness_hours) if now_dt else None

    snapshot_count = len(odds_rows)
    latest_captured = max(
        (r["captured_at"] for r in odds_rows if r.get("captured_at")),
        default=None,
    )

    # Filter to this specific market_key × selection × line
    market_rows = [
        r for r in odds_rows
        if r.get("market_key") == market_key
        and r.get("selection") == selection
        and _line_matches(r.get("line"), line)
    ]

    if not market_rows:
        return "no_odds_available", "none", {
            "snapshot_count": snapshot_count,
            "latest_snapshot_at": latest_captured,
        }

    # Freshness check
    fresh_rows = market_rows
    if freshness_cutoff is not None:
        fresh_rows = [
            r for r in market_rows
            if (parse_iso_datetime(r.get("captured_at")) or now_dt) >= freshness_cutoff
        ]

    if not fresh_rows:
        # All stale
        best = max(market_rows, key=lambda r: r.get("captured_at") or "")
        return "stale_price", "none", {
            "snapshot_count": snapshot_count,
            "latest_snapshot_at": latest_captured,
            "reference_bookmaker": best.get("bookmaker_name"),
            "reference_price": best.get("price"),
            "reference_captured_at": best.get("captured_at"),
        }

    if snapshot_count < min_snapshots:
        best = max(fresh_rows, key=lambda r: r.get("captured_at") or "")
        return "insufficient_data", "none", {
            "snapshot_count": snapshot_count,
            "latest_snapshot_at": latest_captured,
            "reference_bookmaker": best.get("bookmaker_name"),
            "reference_price": best.get("price"),
            "reference_captured_at": best.get("captured_at"),
        }

    # Try reference bookmakers first
    for ref_bm in ref_bookmakers:
        ref_rows = [
            r for r in fresh_rows
            if (r.get("bookmaker_name") or "").lower() == ref_bm.lower()
        ]
        if ref_rows:
            best = max(ref_rows, key=lambda r: r.get("captured_at") or "")
            return "priced_no_model", "reference", {
                "reference_bookmaker": best.get("bookmaker_name"),
                "reference_price": best.get("price"),
                "reference_captured_at": best.get("captured_at"),
                "mapping_confidence": best.get("mapping_confidence"),
                "snapshot_count": snapshot_count,
                "latest_snapshot_at": latest_captured,
            }

    # Try fallback bookmakers
    for fb_bm in fallback_bookmakers:
        fb_rows = [
            r for r in fresh_rows
            if (r.get("bookmaker_name") or "").lower() == fb_bm.lower()
        ]
        if fb_rows:
            best = max(fb_rows, key=lambda r: r.get("captured_at") or "")
            return "priced_no_model", "conditional", {
                "reference_bookmaker": best.get("bookmaker_name"),
                "reference_price": best.get("price"),
                "reference_captured_at": best.get("captured_at"),
                "mapping_confidence": best.get("mapping_confidence"),
                "snapshot_count": snapshot_count,
                "latest_snapshot_at": latest_captured,
            }

    # Odds exist but no matching bookmaker in policy
    return "no_odds_available", "none", {
        "snapshot_count": snapshot_count,
        "latest_snapshot_at": latest_captured,
    }


def _line_matches(row_line: Any, target_line: float | None) -> bool:
    """Compares nullable lines safely."""
    if target_line is None and row_line is None:
        return True
    if target_line is None or row_line is None:
        return False
    try:
        return abs(float(row_line) - target_line) < 0.001
    except (TypeError, ValueError):
        return False


def build_decision_cards_for_fixture(
    fixture_id: int,
    fixture_row: dict[str, Any],
    policy_index: dict[str, dict[str, Any]],
    market_def_by_key: dict[str, dict[str, Any]],
    all_odds_for_fixture: list[dict[str, Any]],
    bet_map_index: dict[tuple, list[dict[str, Any]]],
    now_iso: str,
) -> list[dict[str, Any]]:
    """
    Builds all decision card rows for one fixture.

    Iterates policy rows with decision_mode != 'disabled' (or stat_signal_only).
    For each, resolves what (market_key, selection, line) combos exist in the
    normalized odds, then determines decision_status.

    context_only markets are skipped (no card generated).
    stat_signal_only markets: one card per market_key with status='stat_signal_only'.
    priced markets: one card per distinct (market_key, selection, line) found in odds,
    plus 'no_odds_available' cards for exact-mapped markets where no odds were found.
    """
    league_id = fixture_row.get("league_id")
    season = fixture_row.get("season")

    cards: list[dict[str, Any]] = []

    # Collect distinct (market_key, selection, line) observed in normalized odds
    observed_market_selections: dict[str, set[tuple]] = defaultdict(set)
    for row in all_odds_for_fixture:
        mkey = row.get("market_key")
        sel = row.get("selection")
        line_raw = row.get("line")
        if mkey and sel:
            line_val = float(line_raw) if line_raw is not None else None
            observed_market_selections[mkey].add((sel, line_val))

    # Build a reverse index: market_key → expected (selection) from bet map
    # (for exact-mapped markets only, to generate no_odds_available cards)
    exact_mapped_selections: dict[str, set[str]] = defaultdict(set)
    for rules in bet_map_index.values():
        for rule in rules:
            if not rule.get("extract_line_from_match") and rule.get("market_key"):
                exact_mapped_selections[rule["market_key"]].add(rule["canonical_selection"])

    processed_markets: set[str] = set()

    for market_key, policy in policy_index.items():
        tier = policy.get("priceable_tier")
        mode = policy.get("decision_mode")
        policy_id = policy["id"]

        # Skip context_only — no product decision card needed
        if tier == "context_only":
            continue

        # stat_signal_only: always create one card per market_key with status=stat_signal_only
        if tier == "stat_signal_only":
            mdef = market_def_by_key.get(market_key)
            if not mdef:
                continue
            # Derive selection from operator
            selection = _operator_to_selection(mdef.get("operator", ""))
            if not selection:
                continue
            cards.append(_make_card(
                fixture_id=fixture_id,
                league_id=league_id,
                season=season,
                market_key=market_key,
                selection=selection,
                line=None,
                policy_id=policy_id,
                decision_status="stat_signal_only",
                price_source_quality="none",
                price_info={},
                requires_lineup=bool(policy.get("requires_lineup_confirmation")),
            ))
            processed_markets.add(market_key)
            continue

        # Priced markets (reference_priced or conditional_priced, mode=full or price_context)
        if mode == "disabled":
            continue

        observed_sels = observed_market_selections.get(market_key, set())

        # Build cards for actually observed (market_key, selection, line) pairs
        for (selection, line) in observed_sels:
            decision_status, price_source_quality, price_info = determine_decision_status(
                market_key=market_key,
                selection=selection,
                line=line,
                policy=policy,
                odds_rows=all_odds_for_fixture,
                now_iso=now_iso,
            )
            cards.append(_make_card(
                fixture_id=fixture_id,
                league_id=league_id,
                season=season,
                market_key=market_key,
                selection=selection,
                line=line,
                policy_id=policy_id,
                decision_status=decision_status,
                price_source_quality=price_source_quality,
                price_info=price_info,
                requires_lineup=bool(policy.get("requires_lineup_confirmation")),
            ))

        # For exact-mapped markets: also add no_odds_available if no observed odds
        exact_sels = exact_mapped_selections.get(market_key, set())
        for expected_sel in exact_sels:
            if (expected_sel, None) not in observed_sels:
                # No odds observed for this exact-mapped market → no_odds_available
                cards.append(_make_card(
                    fixture_id=fixture_id,
                    league_id=league_id,
                    season=season,
                    market_key=market_key,
                    selection=expected_sel,
                    line=None,
                    policy_id=policy_id,
                    decision_status="no_odds_available",
                    price_source_quality="none",
                    price_info={"snapshot_count": 0},
                    requires_lineup=bool(policy.get("requires_lineup_confirmation")),
                ))

        processed_markets.add(market_key)

    return cards


def _operator_to_selection(operator: str) -> str | None:
    """Maps market_definitions.operator to canonical_selection for card generation."""
    mapping = {
        "over": "over",
        "under": "under",
        "win": "win",
        "loss": "loss",
        "draw": "draw",
        "btts": "yes",
        "btts_no": "no",
        "most": "win",
    }
    return mapping.get(operator)


def _make_card(
    fixture_id: int,
    league_id: int | None,
    season: int | None,
    market_key: str,
    selection: str,
    line: float | None,
    policy_id: int,
    decision_status: str,
    price_source_quality: str,
    price_info: dict[str, Any],
    requires_lineup: bool,
) -> dict[str, Any]:
    return {
        "fixture_id": fixture_id,
        "market_scope": "prematch",
        "market_key": market_key,
        "selection": selection,
        "line": line,
        "policy_id": policy_id,
        "league_id": league_id,
        "season": season,
        "decision_status": decision_status,
        "price_source_quality": price_source_quality if price_source_quality != "none" else None,
        "reference_bookmaker": price_info.get("reference_bookmaker"),
        "reference_price": price_info.get("reference_price"),
        "reference_captured_at": price_info.get("reference_captured_at"),
        "mapping_confidence": price_info.get("mapping_confidence"),
        "snapshot_count": price_info.get("snapshot_count", 0),
        "latest_snapshot_at": price_info.get("latest_snapshot_at"),
        "stat_signal_score": None,
        "signal_band": None,
        "fair_probability": None,
        "edge_pct": None,
        "model_version_name": None,
        "requires_lineup": requires_lineup,
        "lineup_confirmed": None,
        "built_at": utcnow().isoformat(),
    }


# ─── CLI argument parsing ─────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="STUF Odds Snapshot Normalizer — resolves fixture_odds_snapshots "
                    "into fixture_market_odds + decision cards."
    )
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Season year (e.g. 2026).",
    )
    parser.add_argument(
        "--leagues",
        required=True,
        help="Comma-separated league IDs (e.g. 1 or 39,61,78,135,140).",
    )
    parser.add_argument(
        "--fixture-ids",
        dest="fixture_ids",
        help="Optional: comma-separated fixture IDs to process (overrides full scope).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and resolve without writing to the database.",
    )
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="Skip updating market_bookmaker_coverage_observed (useful for partial runs).",
    )
    parser.add_argument(
        "--skip-decision-cards",
        action="store_true",
        help="Skip rebuilding fixture_market_decision_cards.",
    )
    return parser.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = NormalizerRepository(supabase, LOGGER)

    now_iso = utcnow().isoformat()

    LOGGER.info(
        "Normalizer starting — season=%s leagues=%s dry_run=%s",
        args.season, args.leagues, args.dry_run,
    )

    # ── 1. Load reference data ────────────────────────────────────────────
    LOGGER.info("Loading reference data...")
    bet_map_rows = repository.load_api_bet_map()
    market_def_rows = repository.load_market_definitions()
    policy_rows = repository.load_market_price_policy()

    bet_map_index = build_bet_map_index(bet_map_rows)
    market_def_by_key, market_def_by_cat_op_line, market_def_by_family_op_line = build_market_def_index(market_def_rows)
    policy_index = build_policy_index(policy_rows)

    LOGGER.info(
        "Reference loaded: bet_map_rules=%d market_defs=%d policies=%d",
        len(bet_map_rows), len(market_def_rows), len(policy_rows),
    )

    # ── 2. Resolve target fixture IDs ────────────────────────────────────
    target_leagues = parse_target_leagues(args.leagues)
    if not target_leagues:
        LOGGER.error("No leagues specified. Use --leagues 1 or --leagues 39,61,78,135,140")
        return

    partial_run = bool(args.fixture_ids)

    if partial_run:
        raw_ids = [int(x.strip()) for x in args.fixture_ids.split(",") if x.strip()]
        # Load fixture context for these IDs
        fixture_rows_list: list[dict[str, Any]] = []
        for batch in chunked(raw_ids, 50):
            response = repository._execute(
                lambda batch=batch: supabase.table("fixtures")
                .select("id,league_id,season,home_team_id,away_team_id,date")
                .in_("id", list(batch)),
                f"load fixture rows batch={len(batch)}",
            )
            fixture_rows_list.extend(response.data or [])
        fixture_ids = [r["id"] for r in fixture_rows_list]
    else:
        fixture_rows_list = repository.load_fixtures_for_scope(target_leagues, args.season)
        fixture_ids = [r["id"] for r in fixture_rows_list]

    fixture_by_id = {r["id"]: r for r in fixture_rows_list}

    LOGGER.info("Found %d fixtures in scope.", len(fixture_ids))
    if not fixture_ids:
        LOGGER.info("No fixtures to process. Exiting.")
        return

    # ── 3. Load snapshots ─────────────────────────────────────────────────
    LOGGER.info("Loading snapshots for %d fixtures...", len(fixture_ids))
    snapshots = repository.load_snapshots_for_fixtures(fixture_ids)
    LOGGER.info("Loaded %d snapshots.", len(snapshots))

    if not snapshots:
        LOGGER.info("No snapshots to normalize. Has fetch_pre_match_odds.py been run?")
        return

    # ── 4. Normalize each snapshot ────────────────────────────────────────
    all_resolved: list[dict[str, Any]] = []
    all_unresolved: list[dict[str, Any]] = []
    fixtures_with_changes: set[int] = set()

    stats = {
        "snapshots_processed": 0,
        "resolved_rows": 0,
        "unresolved_rows": 0,
        "no_pattern": 0,
        "line_not_found": 0,
        "key_inactive": 0,
        "policy_disabled": 0,
    }

    for snapshot in snapshots:
        fixture_id = snapshot["fixture_id"]
        fixture_row = fixture_by_id.get(fixture_id)
        if not fixture_row:
            LOGGER.warning("Snapshot references unknown fixture_id=%d — skipping.", fixture_id)
            continue

        resolved, unresolved = normalize_snapshot(
            snapshot=snapshot,
            fixture_row=fixture_row,
            bet_map_index=bet_map_index,
            market_def_by_cat_op_line=market_def_by_cat_op_line,
            market_def_by_key=market_def_by_key,
            policy_index=policy_index,
            market_def_by_family_op_line=market_def_by_family_op_line,
        )

        if not args.dry_run:
            repository.delete_normalized_odds_for_snapshot(
                fixture_id=fixture_id,
                bookmaker_id=snapshot.get("bookmaker_id"),
                market_scope=snapshot.get("market_scope", "prematch"),
                captured_at=snapshot["captured_at"],
            )
            repository.delete_unmapped_for_snapshot(
                fixture_id=fixture_id,
                bookmaker_id=snapshot.get("bookmaker_id"),
                market_scope=snapshot.get("market_scope", "prematch"),
                captured_at=snapshot["captured_at"],
            )
            if resolved:
                repository.insert_market_odds_batch(resolved)
            if unresolved:
                repository.insert_unmapped_batch(unresolved)

        all_resolved.extend(resolved)
        all_unresolved.extend(unresolved)
        fixtures_with_changes.add(fixture_id)
        stats["snapshots_processed"] += 1
        stats["resolved_rows"] += len(resolved)
        stats["unresolved_rows"] += len(unresolved)

        for row in unresolved:
            reason = row.get("unmapped_reason")
            if reason == "no_pattern_match":
                stats["no_pattern"] += 1
            elif reason == "line_not_in_definitions":
                stats["line_not_found"] += 1
            elif reason == "market_key_inactive":
                stats["key_inactive"] += 1
            elif reason == "policy_disabled":
                stats["policy_disabled"] += 1

    LOGGER.info(
        "Normalization complete: snapshots=%d resolved=%d unresolved=%d "
        "(no_pattern=%d line_not_found=%d key_inactive=%d policy_disabled=%d)",
        stats["snapshots_processed"],
        stats["resolved_rows"],
        stats["unresolved_rows"],
        stats["no_pattern"],
        stats["line_not_found"],
        stats["key_inactive"],
        stats["policy_disabled"],
    )

    # ── 5. Coverage tracking (full runs only) ─────────────────────────────
    if not args.skip_coverage and not partial_run and not args.dry_run:
        LOGGER.info("Updating market_bookmaker_coverage_observed...")
        coverage = compute_coverage_events(all_resolved, set(fixture_ids))
        for cov_data in coverage.values():
            repository.upsert_coverage_observed(
                market_key=cov_data["market_key"],
                bookmaker_name=cov_data["bookmaker_name"],
                league_id=cov_data["league_id"],
                season=cov_data["season"],
                market_scope=cov_data["market_scope"],
                fixture_count_with_price=cov_data["fixture_count_with_price"],
                fixture_count_sampled=cov_data["fixture_count_sampled"],
            )
        LOGGER.info("Coverage updated: %d market × bookmaker combos.", len(coverage))
    elif partial_run and not args.skip_coverage:
        LOGGER.info(
            "Skipping coverage update for partial run (--fixture-ids). "
            "Run without --fixture-ids for accurate coverage counts."
        )

    # ── 6. Decision cards ─────────────────────────────────────────────────
    if not args.skip_decision_cards and fixtures_with_changes:
        LOGGER.info(
            "Rebuilding decision cards for %d fixtures...",
            len(fixtures_with_changes),
        )

        if not args.dry_run:
            repository.delete_decision_cards_for_fixtures(list(fixtures_with_changes))

        # Reload normalized odds for all processed fixtures (to include prior snapshots)
        odds_by_fixture = repository.load_normalized_odds_for_fixtures(
            list(fixtures_with_changes)
        ) if not args.dry_run else {}

        # For dry_run, use the in-memory resolved rows
        if args.dry_run:
            for row in all_resolved:
                fid = row["fixture_id"]
                odds_by_fixture.setdefault(fid, []).append(row)

        all_cards: list[dict[str, Any]] = []
        for fixture_id in fixtures_with_changes:
            fixture_row = fixture_by_id.get(fixture_id)
            if not fixture_row:
                continue
            odds_for_fixture = odds_by_fixture.get(fixture_id, [])
            cards = build_decision_cards_for_fixture(
                fixture_id=fixture_id,
                fixture_row=fixture_row,
                policy_index=policy_index,
                market_def_by_key=market_def_by_key,
                all_odds_for_fixture=odds_for_fixture,
                bet_map_index=bet_map_index,
                now_iso=now_iso,
            )
            all_cards.extend(cards)

        if not args.dry_run and all_cards:
            repository.insert_decision_cards_batch(all_cards)

        LOGGER.info(
            "Decision cards built: %d cards for %d fixtures.",
            len(all_cards), len(fixtures_with_changes),
        )

    # ── 7. Summary ────────────────────────────────────────────────────────
    if args.dry_run:
        LOGGER.info("DRY RUN — no database writes performed.")

    # Log unmapped breakdown by bet name (top 10)
    if all_unresolved:
        unmapped_by_bet: dict[str, int] = defaultdict(int)
        for row in all_unresolved:
            label = f"bet_id={row.get('api_bet_id')} name={row.get('api_bet_name')} reason={row.get('unmapped_reason')}"
            unmapped_by_bet[label] += 1

        LOGGER.info("Top unmapped bets:")
        for label, count in sorted(unmapped_by_bet.items(), key=lambda x: -x[1])[:10]:
            LOGGER.info("  %d × %s", count, label)

    LOGGER.info("Normalizer complete.")


if __name__ == "__main__":
    main()
