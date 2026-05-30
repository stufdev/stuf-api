from __future__ import annotations

"""
rebuild_player_prop_engine.py
─────────────────────────────
Builds the Player Prop Engine serving layer from canonical player_fixture_stats.

Does NOT call API-Football.  All reads/writes go to Supabase only.

Usage
─────
python rebuild_player_prop_engine.py --season 2025 --leagues 39,61,78,135,140
python rebuild_player_prop_engine.py --season 2025 --prop-key PLAYER_SCORED
python rebuild_player_prop_engine.py --season 2025 --category cards
python rebuild_player_prop_engine.py --season 2025 --full-refresh

NULL rule
─────────
Only appearances where minutes IS NOT NULL AND minutes > 0 count.
Player stats are NOT NULL DEFAULT 0.  Zero is a real measurement once
minutes > 0.  Do not convert null to 0.

Scope rules
───────────
  overall  → all qualifying appearances
  home     → appearances where is_home = TRUE
  away     → appearances where is_home = FALSE
  (no starts scope in P0 — substitute column is unreliable)
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from player_prop_catalog import (
    METRIC_COLUMN_MAP,
    PLAYER_PROP_DEFINITIONS,
    ensure_player_prop_definitions,
)
from pipeline_core import (
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_target_leagues,
    utcnow,
)

LOGGER = configure_logging("stuf.player_prop_engine")

V1_LEAGUES = (39, 61, 78, 135, 140)
SCOPES = ("overall", "home", "away")
UPCOMING_STATUSES = ("NS", "TBD")
NEXT_FIXTURE_WINDOW_DAYS = 6
PAGE_SIZE = 1000
MIN_MINUTES = 1  # minutes > 0 threshold

# --full-refresh deletes the whole (league, season) slice across all props.
# The two large fact tables (~850k rows each) cannot delete all props in one
# statement without exceeding the DB statement_timeout (Postgres 57014) — the
# evidence table is worst because of its wide covering index.  Deleting one
# prop_key per statement keeps each DELETE ~7k rows: index-friendly via the
# (prop_key, league_id, season) PK prefix and well under the timeout.
DELETE_PROP_CHUNK_LARGE = 1
# Smaller serving tables delete comfortably with all props in one statement.
DELETE_PROP_CHUNK_SMALL = 100


# ──────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild Player Prop Engine serving layer from player_fixture_stats.",
    )
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument(
        "--leagues",
        help="CSV league_id list, e.g. 39,61,78,135,140. Defaults to V1 scope.",
    )
    parser.add_argument(
        "--category",
        choices=["attacking", "shots", "cards", "fouls", "tackles", "fouled"],
        help="Restrict rebuild to one prop category.",
    )
    parser.add_argument(
        "--prop-key",
        help="Restrict rebuild to a single prop key, e.g. PLAYER_SCORED.",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Delete existing rows for the requested scope before rebuilding.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute results but do not write to Supabase.",
    )
    return parser.parse_args()


def resolve_target_leagues(args: argparse.Namespace) -> tuple[int, ...]:
    if args.leagues:
        return parse_target_leagues(args.leagues) or V1_LEAGUES
    return V1_LEAGUES


# ──────────────────────────────────────────────────────────────────────────────
# Paginated select helper
# ──────────────────────────────────────────────────────────────────────────────

def select_all(
    repository: StufRepository,
    table: str,
    columns: str,
    *,
    eq: dict[str, Any] | None = None,
    in_filters: dict[str, Sequence[Any]] | None = None,
    gte: dict[str, Any] | None = None,
    lt: dict[str, Any] | None = None,
    order: tuple[str, bool] | None = None,
) -> list[dict[str, Any]]:
    eq = eq or {}
    in_filters = {k: tuple(v) for k, v in (in_filters or {}).items()}
    gte = gte or {}
    lt = lt or {}
    if any(len(v) == 0 for v in in_filters.values()):
        return []

    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(columns)
            for k, v in eq.items():
                query = query.eq(k, v)
            for k, vals in in_filters.items():
                query = query.in_(k, list(vals))
            for k, v in gte.items():
                query = query.gte(k, v)
            for k, v in lt.items():
                query = query.lt(k, v)
            if order:
                col, asc = order
                query = query.order(col, desc=not asc)
            return query.range(offset, offset + PAGE_SIZE - 1)

        response = repository._execute(request, f"select {table} offset={offset}")
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def select_all_in_chunks(
    repository: StufRepository,
    table: str,
    columns: str,
    *,
    in_column: str,
    values: Iterable[Any],
    chunk_size: int = 100,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in chunked(tuple(values), chunk_size):
        kw = dict(kwargs)
        existing = dict(kw.pop("in_filters", {}) or {})
        existing[in_column] = batch
        rows.extend(select_all(repository, table, columns, in_filters=existing, **kw))
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PropDef:
    key: str
    category: str
    metric: str
    operator: str
    line: float | None
    family: str | None
    label: str


@dataclass
class PlayerAppearance:
    fixture_id: int
    player_id: int
    team_id: int
    opponent_team_id: int | None
    league_id: int
    season: int
    played_at: str
    is_home: bool
    minutes: int
    goals: int
    assists: int
    shots_on_target: int
    total_shots: int
    yellow_cards: int
    red_cards: int
    fouls_committed: int
    fouls_drawn: int
    tackles: int
    offsides: int


@dataclass(frozen=True)
class NextFixture:
    fixture_id: int
    opponent_team_id: int
    opponent_name: str
    venue_scope: str
    played_at: str


# ──────────────────────────────────────────────────────────────────────────────
# Prop evaluation
# ──────────────────────────────────────────────────────────────────────────────

def compute_numeric_value(appearance: PlayerAppearance, prop: PropDef) -> int | None:
    metric = prop.metric
    if metric == "goals":
        return appearance.goals
    if metric == "assists":
        return appearance.assists
    if metric == "goal_involvement":
        return appearance.goals + appearance.assists
    if metric == "shots_on_target":
        return appearance.shots_on_target
    if metric == "total_shots":
        return appearance.total_shots
    if metric == "carded":
        return appearance.yellow_cards + appearance.red_cards
    if metric == "fouls_committed":
        return appearance.fouls_committed
    if metric == "fouls_drawn":
        return appearance.fouls_drawn
    if metric == "tackles":
        return appearance.tackles
    if metric == "offsides":
        return appearance.offsides
    return None


def evaluate_result(numeric_value: int | None, prop: PropDef) -> bool | None:
    if numeric_value is None:
        return None
    line = prop.line
    operator = prop.operator
    if operator == "over":
        if line is None:
            return None
        return numeric_value > line
    if operator == "equals":
        # "Scored" = goals >= 1, "Carded" = cards >= 1
        if line is None:
            return None
        return numeric_value >= line
    if operator == "custom":
        # goal_involvement: goals + assists >= 1
        return numeric_value >= 1
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Streak helpers
# ──────────────────────────────────────────────────────────────────────────────

def compute_streaks(results: list[bool]) -> tuple[int, int]:
    """Return (current_streak, longest_streak).

    current_streak: length of the current consecutive HIT streak from the most
                    recent appearance.  Always >= 0.  Returns 0 when the last
                    result is a miss — there is no active hit streak.
    longest_streak: length of the longest consecutive HIT streak.  Always >= 0.

    Miss streaks are never stored.  Do not introduce negative values.
    """
    if not results:
        return 0, 0

    # current hit streak: walk back from most recent, stop on first miss.
    # If the last result is a miss, current_streak = 0 (no active hit streak).
    current = 0
    if results[-1]:  # last appearance was a hit — count backwards
        for result in reversed(results):
            if result:
                current += 1
            else:
                break

    # longest hit streak: scan forward
    longest = 0
    run = 0
    for result in results:
        if result:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    return current, longest


def window_stats(results: list[bool], n: int) -> tuple[int, int, float | None]:
    """Return (sample, hits, percentage) for last n results."""
    window = results[-n:]
    sample = len(window)
    hits = sum(window)
    pct = round((hits / sample) * 100, 2) if sample > 0 else None
    return sample, hits, pct


def validate_season_prop_stats_rows(rows: list[dict[str, Any]]) -> None:
    """Raise ValueError if any player_season_prop_stats row violates schema invariants.

    Called before upsert (and in dry-run) to catch bugs early — before hitting
    Supabase check constraints.

    Invariants checked:
      - sample >= 0
      - hits >= 0
      - hits <= sample
      - percentage in [0, 100] when not None
      - current_streak >= 0  (miss streaks must not be stored as negative)
      - longest_streak >= 0
      - last_5_hits <= last_5_sample
      - last_10_hits <= last_10_sample
    """
    violations: list[str] = []
    for row in rows:
        key = (
            f"player={row.get('player_id')} team={row.get('team_id')} "
            f"league={row.get('league_id')} scope={row.get('scope')} "
            f"prop={row.get('prop_key')}"
        )
        sample = int(row.get("sample") or 0)
        hits = int(row.get("hits") or 0)
        pct = row.get("percentage")
        current_streak = int(row.get("current_streak") or 0)
        longest_streak = int(row.get("longest_streak") or 0)
        l5_sample = int(row.get("last_5_sample") or 0)
        l5_hits = int(row.get("last_5_hits") or 0)
        l10_sample = int(row.get("last_10_sample") or 0)
        l10_hits = int(row.get("last_10_hits") or 0)

        if sample < 0:
            violations.append(f"{key}: sample={sample} < 0")
        if hits < 0:
            violations.append(f"{key}: hits={hits} < 0")
        if hits > sample:
            violations.append(f"{key}: hits={hits} > sample={sample}")
        if pct is not None and not (0.0 <= float(pct) <= 100.0):
            violations.append(f"{key}: percentage={pct} not in [0, 100]")
        if current_streak < 0:
            violations.append(f"{key}: current_streak={current_streak} < 0")
        if longest_streak < 0:
            violations.append(f"{key}: longest_streak={longest_streak} < 0")
        if l5_hits > l5_sample:
            violations.append(f"{key}: last_5_hits={l5_hits} > last_5_sample={l5_sample}")
        if l10_hits > l10_sample:
            violations.append(f"{key}: last_10_hits={l10_hits} > last_10_sample={l10_sample}")

    if violations:
        cap = 20
        shown = "\n  ".join(violations[:cap])
        total = len(violations)
        suffix = f"\n  ... and {total - cap} more." if total > cap else ""
        raise ValueError(
            f"player_season_prop_stats invariant violations ({total} rows):\n  {shown}{suffix}\n"
            "Fix the builder before writing to Supabase."
        )


def validate_rankings_rows(rows: list[dict[str, Any]]) -> None:
    """Raise ValueError if player_prop_rankings rows contain duplicate PKs.

    The rankings PK is (prop_key, league_id, season, scope, player_id, team_id).
    A transferred player may have multiple rows — one per team context — which is
    correct.  A true duplicate (same 6-column key twice) within one upsert batch
    causes Postgres error 21000.
    """
    seen: set[tuple[str, int, int, str, int, int]] = set()
    duplicates: list[str] = []
    for row in rows:
        key = (
            str(row.get("prop_key", "")),
            int(row.get("league_id", 0)),
            int(row.get("season", 0)),
            str(row.get("scope", "")),
            int(row.get("player_id", 0)),
            int(row.get("team_id", 0)),
        )
        if key in seen:
            duplicates.append(
                f"prop={key[0]} league={key[1]} season={key[2]} "
                f"scope={key[3]} player={key[4]} team={key[5]}"
            )
        seen.add(key)

    if duplicates:
        cap = 20
        shown = "\n  ".join(duplicates[:cap])
        total = len(duplicates)
        suffix = f"\n  ... and {total - cap} more." if total > cap else ""
        raise ValueError(
            f"player_prop_rankings duplicate PK violations ({total} rows):\n  {shown}{suffix}\n"
            "Duplicate (prop_key, league_id, season, scope, player_id, team_id) keys detected — "
            "this should not happen; investigate build_rankings()."
        )


def validate_match_prop_results_rows(rows: list[dict[str, Any]]) -> None:
    """Raise ValueError on duplicate player_match_prop_results PKs.

    PK is (fixture_id, player_id, team_id, prop_key).  A duplicate within one
    upsert batch causes Postgres 21000.  With source appearances already
    deduped, this should never fire — it is a defensive guard.
    """
    seen: set[tuple[int, int, int, str]] = set()
    duplicates: list[str] = []
    for row in rows:
        key = (
            int(row.get("fixture_id", 0)),
            int(row.get("player_id", 0)),
            int(row.get("team_id", 0)),
            str(row.get("prop_key", "")),
        )
        if key in seen:
            duplicates.append(
                f"fixture={key[0]} player={key[1]} team={key[2]} prop={key[3]}"
            )
        seen.add(key)

    if duplicates:
        cap = 20
        shown = "\n  ".join(duplicates[:cap])
        total = len(duplicates)
        suffix = f"\n  ... and {total - cap} more." if total > cap else ""
        raise ValueError(
            f"player_match_prop_results duplicate PK violations ({total} rows):\n  {shown}{suffix}\n"
            "Duplicate (fixture_id, player_id, team_id, prop_key) keys — "
            "source appearance dedup should have prevented this."
        )


def validate_evidence_rows(rows: list[dict[str, Any]]) -> None:
    """Raise ValueError on duplicate player_prop_match_evidence PKs.

    PK is (prop_key, league_id, season, player_id, team_id, fixture_id).
    Defensive guard against Postgres 21000.
    """
    seen: set[tuple[str, int, int, int, int, int]] = set()
    duplicates: list[str] = []
    for row in rows:
        key = (
            str(row.get("prop_key", "")),
            int(row.get("league_id", 0)),
            int(row.get("season", 0)),
            int(row.get("player_id", 0)),
            int(row.get("team_id", 0)),
            int(row.get("fixture_id", 0)),
        )
        if key in seen:
            duplicates.append(
                f"prop={key[0]} league={key[1]} season={key[2]} "
                f"player={key[3]} team={key[4]} fixture={key[5]}"
            )
        seen.add(key)

    if duplicates:
        cap = 20
        shown = "\n  ".join(duplicates[:cap])
        total = len(duplicates)
        suffix = f"\n  ... and {total - cap} more." if total > cap else ""
        raise ValueError(
            f"player_prop_match_evidence duplicate PK violations ({total} rows):\n  {shown}{suffix}\n"
            "Duplicate (prop_key, league_id, season, player_id, team_id, fixture_id) keys — "
            "source appearance dedup should have prevented this."
        )


# ──────────────────────────────────────────────────────────────────────────────
# Load helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_active_prop_defs(
    args: argparse.Namespace,
) -> tuple[PropDef, ...]:
    """Filter PLAYER_PROP_DEFINITIONS to requested scope."""
    defs = PLAYER_PROP_DEFINITIONS
    if args.prop_key:
        defs = tuple(d for d in defs if d["key"] == args.prop_key)
        if not defs:
            raise RuntimeError(f"No active prop definition found for key: {args.prop_key}")
    elif args.category:
        defs = tuple(d for d in defs if d["category"] == args.category)
        if not defs:
            raise RuntimeError(f"No active prop definitions for category: {args.category}")
    return tuple(
        PropDef(
            key=d["key"],
            category=d["category"],
            metric=d["metric"],
            operator=d["operator"],
            line=float(d["line"]) if d.get("line") is not None else None,
            family=d.get("family"),
            label=d["label"],
        )
        for d in defs
    )


def load_player_appearances(
    repository: StufRepository,
    league_ids: tuple[int, ...],
    season: int,
) -> list[PlayerAppearance]:
    LOGGER.info("Loading player_fixture_stats for leagues=%s season=%s", league_ids, season)

    rows = select_all_in_chunks(
        repository,
        "player_fixture_stats",
        (
            "fixture_id,player_id,team_id,league_id,season,"
            "minutes,goals,assists,total_shots,shots_on_target,"
            "yellow_cards,red_cards,fouls_committed,fouls_drawn,"
            "tackles,offsides,is_home,played_at"
        ),
        in_column="league_id",
        values=league_ids,
        eq={"season": season},
    )

    appearances: list[PlayerAppearance] = []
    skipped_minutes = 0
    for row in rows:
        minutes = row.get("minutes")
        if minutes is None or int(minutes) < MIN_MINUTES:
            skipped_minutes += 1
            continue

        appearances.append(PlayerAppearance(
            fixture_id=int(row["fixture_id"]),
            player_id=int(row["player_id"]),
            team_id=int(row["team_id"]),
            opponent_team_id=None,  # enriched below via fixture_teams
            league_id=int(row["league_id"]),
            season=int(row["season"]),
            played_at=str(row["played_at"]),
            is_home=bool(row.get("is_home", False)),
            minutes=int(minutes),
            goals=int(row.get("goals") or 0),
            assists=int(row.get("assists") or 0),
            shots_on_target=int(row.get("shots_on_target") or 0),
            total_shots=int(row.get("total_shots") or 0),
            yellow_cards=int(row.get("yellow_cards") or 0),
            red_cards=int(row.get("red_cards") or 0),
            fouls_committed=int(row.get("fouls_committed") or 0),
            fouls_drawn=int(row.get("fouls_drawn") or 0),
            tackles=int(row.get("tackles") or 0),
            offsides=int(row.get("offsides") or 0),
        ))

    LOGGER.info(
        "Loaded %s qualifying appearances (skipped %s with missing/zero minutes).",
        len(appearances),
        skipped_minutes,
    )
    return appearances


def _appearance_signature(a: PlayerAppearance) -> tuple[Any, ...]:
    """Source-metric fingerprint for a player_fixture_stats appearance.

    Excludes opponent_team_id (enriched later) — compares only the canonical
    metric columns that drive prop computation.
    """
    return (
        a.league_id,
        a.season,
        a.played_at,
        a.is_home,
        a.minutes,
        a.goals,
        a.assists,
        a.shots_on_target,
        a.total_shots,
        a.yellow_cards,
        a.red_cards,
        a.fouls_committed,
        a.fouls_drawn,
        a.tackles,
        a.offsides,
    )


def dedupe_appearances(appearances: list[PlayerAppearance]) -> list[PlayerAppearance]:
    """Collapse duplicate source rows by (fixture_id, player_id, team_id).

    player_fixture_stats is canonical, but it can contain duplicate rows for the
    same (fixture_id, player_id, team_id).  Left unhandled, duplicates both
    inflate season sample/hits and cause Postgres 21000 on upsert.

    Rules (no guessing — protect the product):
      * Exact-duplicate metric rows  → keep one, count collapsed, log INFO.
      * Conflicting metric rows       → raise ValueError locally before any
        write.  We do not pick a "winner"; the canonical source must be fixed.
    """
    by_key: dict[tuple[int, int, int], PlayerAppearance] = {}
    sigs: dict[tuple[int, int, int], tuple[Any, ...]] = {}
    collapsed = 0
    conflicts: list[str] = []

    for a in appearances:
        key = (a.fixture_id, a.player_id, a.team_id)
        sig = _appearance_signature(a)
        if key not in by_key:
            by_key[key] = a
            sigs[key] = sig
            continue
        if sigs[key] == sig:
            collapsed += 1
        else:
            conflicts.append(
                f"fixture_id={key[0]} player_id={key[1]} team_id={key[2]}"
            )

    if conflicts:
        cap = 20
        shown = "\n  ".join(conflicts[:cap])
        total = len(conflicts)
        suffix = f"\n  ... and {total - cap} more." if total > cap else ""
        raise ValueError(
            f"player_fixture_stats has CONFLICTING duplicate appearances "
            f"({total} key(s)) — differing metric values for the same "
            f"(fixture_id, player_id, team_id):\n  {shown}{suffix}\n"
            "The canonical source must be corrected; the builder will not guess."
        )

    if collapsed:
        LOGGER.info(
            "Collapsed %s exact-duplicate source appearance row(s) "
            "(identical metrics for same fixture_id+player_id+team_id).",
            collapsed,
        )

    return list(by_key.values())


def enrich_opponent_ids(
    repository: StufRepository,
    appearances: list[PlayerAppearance],
    league_ids: tuple[int, ...],
    season: int,
) -> None:
    """Fill opponent_team_id from fixture_teams table."""
    fixture_ids = list({a.fixture_id for a in appearances})
    if not fixture_ids:
        return

    LOGGER.info("Enriching opponent_team_id for %s fixtures...", len(fixture_ids))
    ft_rows = select_all_in_chunks(
        repository,
        "fixture_teams",
        "fixture_id,team_id,opponent_team_id",
        in_column="fixture_id",
        values=fixture_ids,
    )
    key_map: dict[tuple[int, int], int] = {}
    for row in ft_rows:
        fid = int(row["fixture_id"])
        tid = int(row["team_id"])
        opp = row.get("opponent_team_id")
        if opp is not None:
            key_map[(fid, tid)] = int(opp)

    for a in appearances:
        a.opponent_team_id = key_map.get((a.fixture_id, a.team_id))


def load_teams(repository: StufRepository, team_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(tid) for tid in team_ids if tid is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "teams", "id,name,logo_url", in_column="id", values=ids)
    return {int(row["id"]): row for row in rows}


def load_leagues(repository: StufRepository, league_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(lid) for lid in league_ids if lid is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "leagues", "id,name,logo_url", in_column="id", values=ids)
    return {int(row["id"]): row for row in rows}


def load_players(repository: StufRepository, player_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Load player identity from the canonical `players` table (id, name, photo_url).

    `players.name` is TEXT NOT NULL — canonical source for all player_name fields.
    `players.photo_url` is TEXT (nullable).
    Fallback for missing rows: caller should use f"Player #{player_id}".
    """
    ids = sorted({int(pid) for pid in player_ids if pid is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "players", "id,name,photo_url", in_column="id", values=ids)
    return {int(row["id"]): row for row in rows}


def load_next_fixtures(
    repository: StufRepository,
    league_ids: tuple[int, ...],
    season: int,
) -> dict[int, NextFixture]:
    """Load first upcoming fixture per team within NEXT_FIXTURE_WINDOW_DAYS."""
    start = utcnow()
    end = start + timedelta(days=NEXT_FIXTURE_WINDOW_DAYS)

    rows = select_all_in_chunks(
        repository,
        "fixture_teams",
        "fixture_id,team_id,opponent_team_id,is_home,played_at",
        in_column="league_id",
        values=league_ids,
        eq={"season": season},
        gte={"played_at": start.isoformat()},
        lt={"played_at": end.isoformat()},
        order=("played_at", True),
    )

    # load opponent names
    opp_ids = {int(row["opponent_team_id"]) for row in rows if row.get("opponent_team_id")}
    teams = load_teams(repository, opp_ids)

    by_team: dict[int, NextFixture] = {}
    for row in rows:
        tid = int(row["team_id"])
        if tid in by_team:
            continue
        opp_id = row.get("opponent_team_id")
        if opp_id is None:
            continue
        opp_id = int(opp_id)
        opp_name = teams.get(opp_id, {}).get("name") or f"Team {opp_id}"
        venue_scope = "home" if row.get("is_home") else "away"
        by_team[tid] = NextFixture(
            fixture_id=int(row["fixture_id"]),
            opponent_team_id=opp_id,
            opponent_name=opp_name,
            venue_scope=venue_scope,
            played_at=str(row["played_at"]),
        )
    LOGGER.info("Found upcoming fixtures for %s teams.", len(by_team))
    return by_team


# ──────────────────────────────────────────────────────────────────────────────
# Repository extensions (player prop tables)
# ──────────────────────────────────────────────────────────────────────────────

class PlayerPropRepository:
    def __init__(self, base: StufRepository):
        self._b = base

    def upsert_player_prop_definitions(self, rows: list[dict[str, Any]]) -> None:
        now = utcnow().isoformat()
        for row in rows:
            row.setdefault("created_at", now)
            row["updated_at"] = now
        self._b._upsert_rows("player_prop_definitions", rows, on_conflict="key", operation="upsert player_prop_definitions")

    def delete_match_prop_results(self, league_id: int, season: int, prop_keys: tuple[str, ...] | None = None) -> None:
        for batch in chunked(list(prop_keys or ()), DELETE_PROP_CHUNK_LARGE) if prop_keys else [None]:  # type: ignore[call-overload]
            def request(batch=batch):
                query = self._b.supabase.table("player_match_prop_results").delete()
                query = query.eq("league_id", league_id).eq("season", season)
                if batch is not None:
                    query = query.in_("prop_key", list(batch))
                return query
            self._b._execute(request, f"delete player_match_prop_results league={league_id} season={season}")

    def upsert_match_prop_results(self, rows: list[dict[str, Any]]) -> None:
        self._b._upsert_rows(
            "player_match_prop_results",
            rows,
            on_conflict="fixture_id,player_id,team_id,prop_key",
            operation="upsert player_match_prop_results",
        )

    def delete_season_prop_stats(self, league_id: int, season: int, prop_keys: tuple[str, ...] | None = None) -> None:
        for batch in chunked(list(prop_keys or ()), DELETE_PROP_CHUNK_SMALL) if prop_keys else [None]:
            def request(batch=batch):
                query = self._b.supabase.table("player_season_prop_stats").delete()
                query = query.eq("league_id", league_id).eq("season", season)
                if batch is not None:
                    query = query.in_("prop_key", list(batch))
                return query
            self._b._execute(request, f"delete player_season_prop_stats league={league_id} season={season}")

    def upsert_season_prop_stats(self, rows: list[dict[str, Any]]) -> None:
        self._b._upsert_rows(
            "player_season_prop_stats",
            rows,
            on_conflict="player_id,team_id,league_id,season,scope,prop_key",
            operation="upsert player_season_prop_stats",
        )

    def delete_rankings(self, league_id: int, season: int, prop_keys: tuple[str, ...] | None = None) -> None:
        for batch in chunked(list(prop_keys or ()), DELETE_PROP_CHUNK_SMALL) if prop_keys else [None]:
            def request(batch=batch):
                query = self._b.supabase.table("player_prop_rankings").delete()
                query = query.eq("league_id", league_id).eq("season", season)
                if batch is not None:
                    query = query.in_("prop_key", list(batch))
                return query
            self._b._execute(request, f"delete player_prop_rankings league={league_id} season={season}")

    def upsert_rankings(self, rows: list[dict[str, Any]]) -> None:
        self._b._upsert_rows(
            "player_prop_rankings",
            rows,
            on_conflict="prop_key,league_id,season,scope,player_id,team_id",
            operation="upsert player_prop_rankings",
        )

    def delete_evidence(self, league_id: int, season: int, prop_keys: tuple[str, ...] | None = None) -> None:
        for batch in chunked(list(prop_keys or ()), DELETE_PROP_CHUNK_LARGE) if prop_keys else [None]:
            def request(batch=batch):
                query = self._b.supabase.table("player_prop_match_evidence").delete()
                query = query.eq("league_id", league_id).eq("season", season)
                if batch is not None:
                    query = query.in_("prop_key", list(batch))
                return query
            self._b._execute(request, f"delete player_prop_match_evidence league={league_id} season={season}")

    def upsert_evidence(self, rows: list[dict[str, Any]]) -> None:
        self._b._upsert_rows(
            "player_prop_match_evidence",
            rows,
            on_conflict="prop_key,league_id,season,player_id,team_id,fixture_id",
            operation="upsert player_prop_match_evidence",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Build stages
# ──────────────────────────────────────────────────────────────────────────────

def build_match_prop_results(
    appearances: list[PlayerAppearance],
    props: tuple[PropDef, ...],
) -> list[dict[str, Any]]:
    now = utcnow().isoformat()
    rows: list[dict[str, Any]] = []
    for appearance in appearances:
        venue_scope = "home" if appearance.is_home else "away"
        for prop in props:
            numeric_value = compute_numeric_value(appearance, prop)
            result = evaluate_result(numeric_value, prop)
            if result is None:
                continue
            rows.append({
                "fixture_id": appearance.fixture_id,
                "player_id": appearance.player_id,
                "team_id": appearance.team_id,
                "opponent_team_id": appearance.opponent_team_id,
                "league_id": appearance.league_id,
                "season": appearance.season,
                "played_at": appearance.played_at,
                "venue_scope": venue_scope,
                "starter": None,  # substitute column unreliable in P0
                "minutes": appearance.minutes,
                "prop_key": prop.key,
                "numeric_value": numeric_value,
                "result": result,
                "computed_at": now,
            })
    LOGGER.info("Built %s player_match_prop_results rows.", len(rows))
    return rows


PlayerPropKey = tuple[int, int, int, int, str, str]  # player_id, team_id, league_id, season, scope, prop_key


def build_season_prop_stats(
    match_results: list[dict[str, Any]],
    season: int,
) -> list[dict[str, Any]]:
    """Aggregate per (player, team, league, season, scope, prop_key)."""
    now = utcnow().isoformat()

    # Group results: key → list of (played_at, result) sorted chronologically
    ResultEntry = tuple[str, bool]
    grouped: dict[PlayerPropKey, list[ResultEntry]] = defaultdict(list)
    for row in match_results:
        venue_scope = row["venue_scope"]
        prop_key = row["prop_key"]
        player_id = row["player_id"]
        team_id = row["team_id"]
        league_id = row["league_id"]
        result = bool(row["result"])
        played_at = str(row["played_at"])

        # overall scope
        grouped[(player_id, team_id, league_id, season, "overall", prop_key)].append((played_at, result))
        # venue scope
        grouped[(player_id, team_id, league_id, season, venue_scope, prop_key)].append((played_at, result))

    rows: list[dict[str, Any]] = []
    for key, entries in grouped.items():
        player_id, team_id, league_id, season_, scope, prop_key = key
        # sort by played_at ascending (chronological)
        entries.sort(key=lambda e: e[0])
        results = [e[1] for e in entries]
        sample = len(results)
        hits = sum(results)
        pct = round((hits / sample) * 100, 2) if sample > 0 else None
        current_streak, longest_streak = compute_streaks(results)
        l5_sample, l5_hits, l5_pct = window_stats(results, 5)
        l10_sample, l10_hits, l10_pct = window_stats(results, 10)

        rows.append({
            "player_id": player_id,
            "team_id": team_id,
            "league_id": league_id,
            "season": season_,
            "scope": scope,
            "prop_key": prop_key,
            "sample": sample,
            "hits": hits,
            "percentage": pct,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "last_5_sample": l5_sample if l5_sample > 0 else None,
            "last_5_hits": l5_hits if l5_sample > 0 else None,
            "last_5_percentage": l5_pct,
            "last_10_sample": l10_sample if l10_sample > 0 else None,
            "last_10_hits": l10_hits if l10_sample > 0 else None,
            "last_10_percentage": l10_pct,
            "updated_at": now,
        })

    LOGGER.info("Built %s player_season_prop_stats rows.", len(rows))
    return rows


def build_rankings(
    season_stats: list[dict[str, Any]],
    players: dict[int, dict[str, Any]],
    teams: dict[int, dict[str, Any]],
    leagues: dict[int, dict[str, Any]],
    next_fixtures: dict[int, NextFixture],
    season: int,
) -> list[dict[str, Any]]:
    now = utcnow().isoformat()

    # Index stats by (player_id, team_id, league_id, season, scope, prop_key)
    stats_index: dict[tuple[int, int, int, int, str, str], dict[str, Any]] = {}
    for row in season_stats:
        key = (row["player_id"], row["team_id"], row["league_id"], row["season"], row["scope"], row["prop_key"])
        stats_index[key] = row

    # For each unique (prop_key, league_id, scope) group, rank by percentage desc, sample desc
    from collections import defaultdict as _dd

    RankKey = tuple[str, int, str]  # prop_key, league_id, scope
    groups: dict[RankKey, list[dict[str, Any]]] = _dd(list)
    for row in season_stats:
        rk: RankKey = (row["prop_key"], row["league_id"], row["scope"])
        groups[rk].append(row)

    rows: list[dict[str, Any]] = []
    for (prop_key, league_id, scope), group_rows in groups.items():
        # player_prop_rankings PK is (prop_key, league_id, season, scope, player_id, team_id).
        # A transferred player has a separate row per team context — both are valid and distinct.
        # No deduplication by player_id.  Sort: percentage desc, sample desc, player_id asc, team_id asc.
        sorted_group = sorted(
            group_rows,
            key=lambda r: (-(r.get("percentage") or 0), -r.get("sample", 0), r.get("player_id", 0), r.get("team_id", 0)),
        )
        league = leagues.get(league_id, {})
        for rank, stat_row in enumerate(sorted_group, start=1):
            player_id = stat_row["player_id"]
            team_id = stat_row["team_id"]
            team = teams.get(team_id, {})
            nf = next_fixtures.get(team_id)
            player = players.get(player_id, {})
            rows.append({
                "prop_key": prop_key,
                "league_id": league_id,
                "season": season,
                "scope": scope,
                "player_id": player_id,
                "player_name": player.get("name") or f"Player #{player_id}",
                "player_photo_url": player.get("photo_url") or None,
                "team_id": team_id,
                "team_name": team.get("name") or f"Team {team_id}",
                "team_logo_url": team.get("logo_url"),
                "league_name": league.get("name") or f"League {league_id}",
                "league_logo_url": league.get("logo_url"),
                "sample": stat_row["sample"],
                "hits": stat_row["hits"],
                "percentage": stat_row["percentage"],
                "current_streak": stat_row["current_streak"],
                "longest_streak": stat_row["longest_streak"],
                "last_5_sample": stat_row.get("last_5_sample"),
                "last_5_hits": stat_row.get("last_5_hits"),
                "last_5_percentage": stat_row.get("last_5_percentage"),
                "last_10_sample": stat_row.get("last_10_sample"),
                "last_10_hits": stat_row.get("last_10_hits"),
                "last_10_percentage": stat_row.get("last_10_percentage"),
                "rank": rank,
                "next_fixture_id": nf.fixture_id if nf else None,
                "next_fixture_date": nf.played_at if nf else None,
                "next_opponent_team_id": nf.opponent_team_id if nf else None,
                "next_opponent_name": nf.opponent_name if nf else None,
                "next_venue_scope": nf.venue_scope if nf else None,
                "computed_at": now,
            })

    LOGGER.info("Built %s player_prop_rankings rows.", len(rows))
    return rows


def build_evidence(
    match_results: list[dict[str, Any]],
    teams: dict[int, dict[str, Any]],
    player_names: dict[int, str],
) -> list[dict[str, Any]]:
    now = utcnow().isoformat()
    rows: list[dict[str, Any]] = []
    for row in match_results:
        player_id = row["player_id"]
        team_id = row["team_id"]
        opp_id = row.get("opponent_team_id")
        team = teams.get(team_id, {})
        opp = teams.get(opp_id, {}) if opp_id else {}
        rows.append({
            "prop_key": row["prop_key"],
            "league_id": row["league_id"],
            "season": row["season"],
            "player_id": player_id,
            "player_name": player_names.get(player_id),
            "team_id": team_id,
            "team_name": team.get("name") or f"Team {team_id}",
            "fixture_id": row["fixture_id"],
            "played_at": row["played_at"],
            "opponent_team_id": opp_id,
            "opponent_name": opp.get("name") or (f"Team {opp_id}" if opp_id else None),
            "venue_scope": row["venue_scope"],
            "starter": row.get("starter"),
            "minutes": row.get("minutes"),
            "numeric_value": row.get("numeric_value"),
            "result": row["result"],
            "computed_at": now,
        })
    LOGGER.info("Built %s player_prop_match_evidence rows.", len(rows))
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    settings = load_settings()
    supabase = create_supabase_client(settings)
    base_repo = StufRepository(supabase, LOGGER)
    prop_repo = PlayerPropRepository(base_repo)

    season = args.season
    league_ids = resolve_target_leagues(args)
    props = load_active_prop_defs(args)
    prop_keys = tuple(p.key for p in props)

    LOGGER.info(
        "Starting Player Prop Engine rebuild: season=%s leagues=%s props=%s dry_run=%s full_refresh=%s",
        season,
        league_ids,
        len(props),
        args.dry_run,
        args.full_refresh,
    )

    # Step 1: Seed prop definitions
    if not args.dry_run:
        LOGGER.info("Seeding player_prop_definitions...")
        prop_repo.upsert_player_prop_definitions(list(PLAYER_PROP_DEFINITIONS))

    # Step 2: Load appearances
    appearances = load_player_appearances(base_repo, league_ids, season)
    if not appearances:
        LOGGER.warning("No qualifying appearances found. Exiting.")
        return

    # Step 2a: Dedupe duplicate source rows at the canonical→facts boundary.
    # Identical duplicates collapse; conflicting duplicates fail loudly.
    appearances = dedupe_appearances(appearances)

    # Step 3: Enrich opponent IDs
    enrich_opponent_ids(base_repo, appearances, league_ids, season)

    # Step 4: Load reference data
    all_team_ids = {a.team_id for a in appearances}
    all_team_ids |= {a.opponent_team_id for a in appearances if a.opponent_team_id}
    teams = load_teams(base_repo, all_team_ids)
    leagues = load_leagues(base_repo, league_ids)
    next_fixtures = load_next_fixtures(base_repo, league_ids, season)

    # Step 5: Load player identity from canonical `players` table (name, photo_url).
    # player_fixture_stats has no player_name column — players table is the source.
    all_player_ids = {a.player_id for a in appearances}
    players = load_players(base_repo, all_player_ids)
    missing_name_count = sum(1 for pid in all_player_ids if pid not in players)
    if missing_name_count:
        LOGGER.warning(
            "%s player_id(s) have no row in the players table — will use 'Player #<id>' fallback.",
            missing_name_count,
        )
    # Build flat name dict for build_evidence
    player_names: dict[int, str] = {
        pid: (row.get("name") or f"Player #{pid}")
        for pid, row in players.items()
    }
    for pid in all_player_ids:
        if pid not in player_names:
            player_names[pid] = f"Player #{pid}"

    # Step 6: Build match prop results
    match_results = build_match_prop_results(appearances, props)

    # Step 6a: Validate match_prop_results PKs are unique (catches 21000 in dry-run)
    validate_match_prop_results_rows(match_results)
    LOGGER.info("match_prop_results PK uniqueness OK (%s rows).", len(match_results))

    # Step 7: Build season prop stats
    season_stats = build_season_prop_stats(match_results, season)

    # Step 7a: Validate invariants before touching Supabase (catches bugs in dry-run too)
    validate_season_prop_stats_rows(season_stats)
    LOGGER.info("season_prop_stats invariants OK (%s rows passed).", len(season_stats))

    # Step 8: Build rankings
    rankings = build_rankings(season_stats, players, teams, leagues, next_fixtures, season)

    # Step 8a: Validate rankings PKs are unique (catches transfer-dedup regressions)
    validate_rankings_rows(rankings)
    LOGGER.info("rankings PK uniqueness OK (%s rows).", len(rankings))

    # Step 8b: INFO diagnostic — report players appearing for multiple teams
    from collections import defaultdict as _dd2
    player_team_counts: dict[int, set[int]] = _dd2(set)
    for r in rankings:
        player_team_counts[r["player_id"]].add(r["team_id"])
    multi_team = [(pid, tids) for pid, tids in player_team_counts.items() if len(tids) > 1]
    if multi_team:
        LOGGER.info(
            "Multi-team players in rankings (transferred mid-season): %s player(s). "
            "Each player-team context is a separate ranking row — this is expected.",
            len(multi_team),
        )

    # Step 9: Build evidence
    evidence = build_evidence(match_results, teams, player_names)

    # Step 9a: Validate evidence PKs are unique (catches 21000 in dry-run)
    validate_evidence_rows(evidence)
    LOGGER.info("evidence PK uniqueness OK (%s rows).", len(evidence))

    if args.dry_run:
        LOGGER.info(
            "DRY RUN — would write: %s match_results, %s season_stats, %s rankings, %s evidence rows. "
            "players_resolved=%s players_fallback=%s",
            len(match_results),
            len(season_stats),
            len(rankings),
            len(evidence),
            len(players),
            missing_name_count,
        )
        return

    # Step 10: Write to Supabase (per-league to keep batches manageable)
    for league_id in league_ids:
        LOGGER.info("Writing league_id=%s season=%s...", league_id, season)

        if args.full_refresh:
            prop_repo.delete_match_prop_results(league_id, season, prop_keys)
            prop_repo.delete_season_prop_stats(league_id, season, prop_keys)
            prop_repo.delete_rankings(league_id, season, prop_keys)
            prop_repo.delete_evidence(league_id, season, prop_keys)

        league_match = [r for r in match_results if r["league_id"] == league_id]
        league_stats = [r for r in season_stats if r["league_id"] == league_id]
        league_rankings = [r for r in rankings if r["league_id"] == league_id]
        league_evidence = [r for r in evidence if r["league_id"] == league_id]

        prop_repo.upsert_match_prop_results(league_match)
        prop_repo.upsert_season_prop_stats(league_stats)
        prop_repo.upsert_rankings(league_rankings)
        prop_repo.upsert_evidence(league_evidence)

        LOGGER.info(
            "  league=%s: %s match, %s stats, %s rankings, %s evidence rows.",
            league_id,
            len(league_match),
            len(league_stats),
            len(league_rankings),
            len(league_evidence),
        )

    LOGGER.info("Player Prop Engine rebuild complete.")


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
