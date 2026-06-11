"""Rebuild the Fixture Signals serving layer (Fixtures Match Intelligence).

Materializes the best cross-source signals per UPCOMING fixture into
fixture_signals, so /fixtures can become the daily entry point of STUF.

Sources & hierarchy:
  1. team_market     — primary. team_season_market_stats scored by contextual
                       informativeness (z-score vs the exact league/season/
                       scope/market context). Streak length is folded into the
                       score as a quality booster (per product decision) and
                       carried in source_payload, not emitted as a duplicate row.
  2. referee_context — amplifier. Boosts matching card/foul team-market signals
                       and emits one referee context chip per fixture.
  3. player_prop     — secondary, weight-capped so it never dominates a fixture.

This job ONLY reads/writes Supabase. It never calls API-Football and never
mutates canonical tables — it rebuilds a read model. null is preserved as
missing data; it is never coerced to 0. No odds / edge / probability fields.

Usage (run manually; this writes to Supabase):
  python rebuild_fixture_signals.py --season 2025
  python rebuild_fixture_signals.py --season 2025 --leagues 39,61,78,135,140 --full-refresh
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from pipeline_core import (
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_target_leagues,
    resolve_target_leagues,
    utcnow,
)
from fixture_signal_scoring import (
    FIXTURE_SIGNAL_CONFIG,
    TEAM_MARKET_TENDENCY_BAND,
    player_prop_strength,
    referee_amplifies,
    team_market_tendency_strength,
)

LOGGER = configure_logging("stuf.fixture_signals")

UPCOMING_STATUSES = ("NS", "TBD")
DEFAULT_WINDOW_DAYS = 14  # Wide enough to cover WC 2026 off-season gaps
PAGE_SIZE = 1000
SCOPE_LABELS = {"overall": "overall", "home": "home", "away": "away"}

OrderSpec = tuple[str, bool] | tuple[tuple[str, bool], ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild Fixture Signals serving layer from existing read models.",
    )
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--leagues", help="CSV of league_id, e.g. 39,61,78,135,140.")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Delete the projection for the requested league/season before rebuilding.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="CLI compatibility only. Unused: this job reads/writes Supabase exclusively.",
    )
    return parser.parse_args()


# ── Generic paginated reads (mirrors rebuild_market_serving_layer.py) ──────────
def normalize_order(order: OrderSpec | None) -> tuple[tuple[str, bool], ...]:
    if order is None:
        return ()
    if len(order) == 2 and isinstance(order[0], str):
        column, ascending = order
        return ((column, bool(ascending)),)
    return tuple((str(column), bool(ascending)) for column, ascending in order)


def select_all(
    repository: StufRepository,
    table: str,
    columns: str,
    *,
    eq: dict[str, Any] | None = None,
    in_filters: dict[str, Iterable[Any]] | None = None,
    gte: dict[str, Any] | None = None,
    lt: dict[str, Any] | None = None,
    order: OrderSpec | None = None,
) -> list[dict[str, Any]]:
    eq = eq or {}
    in_filters = {key: tuple(value) for key, value in (in_filters or {}).items()}
    gte = gte or {}
    lt = lt or {}
    if any(len(values) == 0 for values in in_filters.values()):
        return []

    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(columns)
            for key, value in eq.items():
                query = query.eq(key, value)
            for key, values in in_filters.items():
                query = query.in_(key, list(values))
            for key, value in gte.items():
                query = query.gte(key, value)
            for key, value in lt.items():
                query = query.lt(key, value)
            for column, ascending in normalize_order(order):
                query = query.order(column, desc=not ascending)
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
        next_kwargs = dict(kwargs)
        in_filters = dict(next_kwargs.pop("in_filters", {}) or {})
        in_filters[in_column] = batch
        rows.extend(select_all(repository, table, columns, in_filters=in_filters, **next_kwargs))
    return rows


def maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_pct(value: Any) -> int | None:
    parsed = maybe_float(value)
    return round(parsed) if parsed is not None else None


def load_league(repository: StufRepository, league_id: int) -> dict[str, Any]:
    rows = select_all(repository, "leagues", "id,name,logo_url", eq={"id": league_id})
    return rows[0] if rows else {"id": league_id, "name": f"League {league_id}", "logo_url": None}


def load_teams(repository: StufRepository, team_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(team_id) for team_id in team_ids if team_id is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "teams", "id,name,logo_url", in_column="id", values=ids)
    return {int(row["id"]): row for row in rows}


def load_referees(repository: StufRepository, referee_ids: Iterable[int]) -> dict[int, str]:
    ids = sorted({int(referee_id) for referee_id in referee_ids if referee_id is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "referees", "id,name", in_column="id", values=ids)
    return {int(row["id"]): str(row.get("name") or f"Referee {row['id']}") for row in rows}


# ── Candidate construction ────────────────────────────────────────────────────
def team_market_candidates(
    *,
    fixture: dict[str, Any],
    side: str,  # 'home' or 'away'
    team_id: int,
    opponent_id: int,
    opponent_name: str,
    teams_by_id: dict[int, dict[str, Any]],
    market_keys: list[str],
    market_meta: dict[str, dict[str, Any]],
    stats_by_key: dict[tuple[int, str, str], dict[str, Any]],
    league_id: int,
    season: int,
) -> list[dict[str, Any]]:
    # Home team uses its home + overall splits; away team uses away + overall.
    role_scopes = (side, "overall")
    team = teams_by_id.get(team_id, {})
    candidates: list[dict[str, Any]] = []

    for market_key in market_keys:
        best: dict[str, Any] | None = None
        for scope in role_scopes:
            stat = stats_by_key.get((team_id, market_key, scope))
            if not stat:
                continue
            sample = maybe_int(stat.get("sample")) or 0
            if sample < FIXTURE_SIGNAL_CONFIG["min_team_market_sample"]:
                continue
            strength = team_market_tendency_strength(stat.get("percentage"), sample)
            entry = {"scope": scope, "stat": stat, "strength": strength}
            if best is None or strength > best["strength"]:
                best = entry

        if best is None:
            continue

        stat = best["stat"]
        scope = best["scope"]
        strength = best["strength"]
        meta = market_meta.get(market_key, {})
        market_label = str(meta.get("label") or market_key)
        category = meta.get("category")
        sample = maybe_int(stat.get("sample")) or 0
        hits = maybe_int(stat.get("hits")) or 0
        pct = maybe_float(stat.get("percentage"))

        candidates.append({
            "signal_key": f"f{fixture['id']}:team_market:t{team_id}:{market_key}:{scope}",
            "source_type": "team_market",
            "subject_type": "team",
            "subject_team_id": team_id,
            "subject_player_id": None,
            "market_key": market_key,
            "prop_key": None,
            "category": category,
            "scope": scope,
            "label": market_label,
            "headline": (
                f"{team.get('name', f'Team {team_id}')} · {market_label} · "
                f"{round_pct(pct)}% ({SCOPE_LABELS.get(scope, scope)}, {hits}/{sample})"
            ),
            "sample": sample,
            "hit_rate": pct,
            "signal_strength": strength,
            "signal_band": TEAM_MARKET_TENDENCY_BAND,
            "category_for_referee": category,
            "source_payload": {
                "team_id": team_id,
                "team_name": team.get("name"),
                "team_logo_url": team.get("logo_url"),
                "scope": scope,
                "is_home": side == "home",
                "opponent_team_id": opponent_id,
                "opponent_name": opponent_name,
                "current_streak": maybe_int(stat.get("current_streak")),
                "longest_streak": maybe_int(stat.get("longest_streak")),
                "team_hit_rate_pct": round_pct(pct),
                "referee_amplified": False,
            },
        })

    return candidates


def referee_context_for_fixture(
    *,
    fixture: dict[str, Any],
    referee_id: int | None,
    referee_name: str | None,
    referee_stats_by_id: dict[int, list[dict[str, Any]]],
    market_meta: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (best_referee_chip, amplifying_stats) for a fixture's referee.

    best_referee_chip is the strongest card/foul tendency (one chip per fixture).
    amplifying_stats are the high referee stats used to boost team-market signals.
    """
    if referee_id is None:
        return None, []

    stats = referee_stats_by_id.get(referee_id, [])
    amplifying: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for stat in stats:
        category = stat.get("category")
        pct = maybe_float(stat.get("percentage"))
        sample = maybe_int(stat.get("sample")) or 0
        if not referee_amplifies(category, pct, sample):
            continue
        amplifying.append(stat)
        if best is None or (maybe_float(best.get("percentage")) or 0) < (pct or 0):
            best = stat

    chip: dict[str, Any] | None = None
    if best is not None:
        market_key = str(best.get("market_key"))
        meta = market_meta.get(market_key, {})
        market_label = str(meta.get("label") or market_key)
        pct = maybe_float(best.get("percentage"))
        sample = maybe_int(best.get("sample")) or 0
        hits = maybe_int(best.get("hits")) or 0
        chip = {
            "signal_key": f"f{fixture['id']}:referee_context:r{referee_id}:{market_key}:all",
            "source_type": "referee_context",
            "subject_type": "referee",
            "subject_team_id": None,
            "subject_player_id": None,
            "market_key": market_key,
            "prop_key": None,
            "category": meta.get("category"),
            "scope": None,
            "label": market_label,
            "headline": (
                f"Referee {referee_name or referee_id} · {market_label} · "
                f"{round_pct(pct)}% ({hits}/{sample})"
            ),
            "sample": sample,
            "hit_rate": pct,
            "signal_strength": FIXTURE_SIGNAL_CONFIG["referee_context_base_strength"],
            "signal_band": "context",
            "category_for_referee": None,
            "source_payload": {
                "referee_id": referee_id,
                "referee_name": referee_name,
                "current_streak": maybe_int(best.get("current_streak")),
                "longest_streak": maybe_int(best.get("longest_streak")),
                "referee_hit_rate_pct": round_pct(pct),
            },
        }

    return chip, amplifying


def apply_referee_amplification(
    candidates: list[dict[str, Any]],
    amplifying_stats: list[dict[str, Any]],
    referee_name: str | None,
) -> None:
    if not amplifying_stats:
        return
    amplifying_categories = {str(stat.get("category")) for stat in amplifying_stats}
    bonus = FIXTURE_SIGNAL_CONFIG["referee_amplify_bonus"]
    for candidate in candidates:
        if candidate["source_type"] != "team_market":
            continue
        if str(candidate.get("category_for_referee")) in amplifying_categories:
            candidate["signal_strength"] = float(candidate["signal_strength"]) + bonus
            candidate["source_payload"]["referee_amplified"] = True
            candidate["source_payload"]["referee_name"] = referee_name


def player_prop_candidates(
    *,
    fixture: dict[str, Any],
    rankings: list[dict[str, Any]],
    prop_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rankings:
        pct = maybe_float(row.get("percentage"))
        sample = maybe_int(row.get("sample")) or 0
        strength, band = player_prop_strength(pct, sample)
        prop_key = str(row.get("prop_key"))
        scope = str(row.get("scope"))
        meta = prop_meta.get(prop_key, {})
        prop_label = str(meta.get("label") or prop_key)
        player_id = maybe_int(row.get("player_id"))
        team_id = maybe_int(row.get("team_id"))
        hits = maybe_int(row.get("hits")) or 0
        scored.append((strength, {
            "signal_key": f"f{fixture['id']}:player_prop:p{player_id}:{prop_key}:{scope}",
            "source_type": "player_prop",
            "subject_type": "player",
            "subject_team_id": team_id,
            "subject_player_id": player_id,
            "market_key": None,
            "prop_key": prop_key,
            "category": "player",
            "scope": scope,
            "label": prop_label,
            "headline": (
                f"{row.get('player_name')} ({row.get('team_name')}) · {prop_label} · "
                f"{round_pct(pct)}% ({hits}/{sample})"
            ),
            "sample": sample,
            "hit_rate": pct,
            "signal_strength": strength,
            "signal_band": band,
            "category_for_referee": None,
            "source_payload": {
                "player_id": player_id,
                "player_name": row.get("player_name"),
                "team_id": team_id,
                "team_name": row.get("team_name"),
                "scope": scope,
                "current_streak": maybe_int(row.get("current_streak")),
                "longest_streak": maybe_int(row.get("longest_streak")),
                "next_venue_scope": row.get("next_venue_scope"),
                "prop_rank": maybe_int(row.get("rank")),
            },
        }))

    scored.sort(key=lambda item: (-item[0], -(item[1]["sample"] or 0), str(item[1]["label"])))
    limit = FIXTURE_SIGNAL_CONFIG["player_prop_max_per_fixture"]
    return [candidate for _, candidate in scored[:limit]]


def finalize_fixture_rows(
    *,
    fixture: dict[str, Any],
    candidates: list[dict[str, Any]],
    teams_by_id: dict[int, dict[str, Any]],
    league: dict[str, Any],
    league_id: int,
    season: int,
) -> list[dict[str, Any]]:
    candidates.sort(
        key=lambda candidate: (
            -float(candidate["signal_strength"]),
            -(candidate["sample"] or 0),
            str(candidate["label"]),
            str(candidate["signal_key"]),
        )
    )
    top = candidates[: FIXTURE_SIGNAL_CONFIG["top_signals_per_fixture"]]

    home_id = int(fixture["home_team_id"])
    away_id = int(fixture["away_team_id"])
    home = teams_by_id.get(home_id, {})
    away = teams_by_id.get(away_id, {})

    rows: list[dict[str, Any]] = []
    for rank, candidate in enumerate(top, start=1):
        rows.append({
            "signal_key": candidate["signal_key"],
            "fixture_id": int(fixture["id"]),
            "league_id": league_id,
            "season": season,
            "played_at": fixture["date"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team_name": str(home.get("name", f"Team {home_id}")),
            "home_team_logo_url": home.get("logo_url"),
            "away_team_name": str(away.get("name", f"Team {away_id}")),
            "away_team_logo_url": away.get("logo_url"),
            "league_name": str(league.get("name", f"League {league_id}")),
            "league_logo_url": league.get("logo_url"),
            "source_type": candidate["source_type"],
            "subject_type": candidate["subject_type"],
            "subject_team_id": candidate["subject_team_id"],
            "subject_player_id": candidate["subject_player_id"],
            "market_key": candidate["market_key"],
            "prop_key": candidate["prop_key"],
            "category": candidate["category"],
            "scope": candidate["scope"],
            "label": candidate["label"],
            "headline": candidate["headline"],
            "sample": candidate["sample"],
            "hit_rate": candidate["hit_rate"],
            "signal_strength": round(float(candidate["signal_strength"]), 4),
            "signal_band": candidate["signal_band"],
            "signal_rank": rank,
            "source_payload": candidate["source_payload"],
        })
    return rows


# ── Projection write (computed_at marker + stale delete) ──────────────────────
def delete_projection_rows(
    repository: StufRepository,
    filters: dict[str, Any],
    operation: str,
    *,
    older_than: str | None = None,
) -> None:
    def request():
        query = repository.supabase.table("fixture_signals").delete()
        for key, value in filters.items():
            query = query.eq(key, value)
        if older_than is not None:
            query = query.lt("computed_at", older_than)
        return query

    repository._execute(request, operation)


def replace_projection_rows(
    repository: StufRepository,
    rows: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
) -> None:
    if not rows:
        delete_projection_rows(repository, filters, "delete empty fixture_signals")
        return

    marker = utcnow().isoformat()
    stamped_rows = [{**row, "computed_at": marker} for row in rows]
    repository._upsert_rows(
        "fixture_signals",
        stamped_rows,
        "signal_key",
        f"upsert fixture_signals league={filters.get('league_id')} season={filters.get('season')}",
    )
    delete_projection_rows(repository, filters, "cleanup fixture_signals", older_than=marker)


# ── League/season rebuild ─────────────────────────────────────────────────────
def rebuild_league(
    repository: StufRepository,
    league_id: int,
    season: int,
    *,
    window_days: int,
    full_refresh: bool,
) -> int:
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=window_days)

    fixtures = select_all(
        repository,
        "fixtures",
        "id,date,league_id,season,status_short,home_team_id,away_team_id,referee_id,referee_name_raw",
        eq={"league_id": league_id, "season": season},
        in_filters={"status_short": UPCOMING_STATUSES},
        gte={"date": start.isoformat()},
        lt={"date": end.isoformat()},
        order=("date", True),
    )
    filters = {"league_id": league_id, "season": season}
    if not fixtures:
        if full_refresh:
            delete_projection_rows(repository, filters, "full refresh delete fixture_signals")
        else:
            replace_projection_rows(repository, [], filters=filters)
        LOGGER.info("No upcoming fixtures league=%s season=%s; projection cleared.", league_id, season)
        return 0

    fixture_ids = [int(row["id"]) for row in fixtures]
    team_ids = {int(row["home_team_id"]) for row in fixtures} | {int(row["away_team_id"]) for row in fixtures}
    referee_ids = {int(row["referee_id"]) for row in fixtures if row.get("referee_id") is not None}

    league = load_league(repository, league_id)
    teams_by_id = load_teams(repository, team_ids)
    referees_by_id = load_referees(repository, referee_ids)

    # Active markets.
    market_rows = select_all(
        repository,
        "market_definitions",
        "key,label,category,display_order,is_active",
        eq={"is_active": True},
        order=(("display_order", True), ("key", True)),
    )
    market_meta = {
        str(row["key"]): {"label": row.get("label"), "category": row.get("category")}
        for row in market_rows
        if row.get("key")
    }
    market_keys = list(market_meta.keys())

    # Team-market stats (context universe + per-key lookup), emerging floor applied.
    stat_rows = select_all(
        repository,
        "team_season_market_stats",
        "team_id,league_id,season,scope,market_key,category,sample,hits,percentage,current_streak,longest_streak",
        eq={"league_id": league_id, "season": season},
        gte={"sample": FIXTURE_SIGNAL_CONFIG["min_team_market_sample"]},
    )
    stats_by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in stat_rows:
        stats_by_key[(int(row["team_id"]), str(row["market_key"]), str(row["scope"]))] = row

    # Referee market stats for the fixtures' referees.
    referee_stats_by_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if referee_ids:
        ref_rows = select_all_in_chunks(
            repository,
            "referee_market_stats",
            "referee_id,league_id,season,market_key,category,sample,hits,percentage,current_streak,longest_streak",
            in_column="referee_id",
            values=referee_ids,
            eq={"league_id": league_id, "season": season},
        )
        for row in ref_rows:
            referee_stats_by_id[int(row["referee_id"])].append(row)

    # Player prop rankings whose next fixture is one of these fixtures.
    prop_def_rows = select_all(
        repository,
        "player_prop_definitions",
        "key,label,category,is_active",
        eq={"is_active": True},
    )
    prop_meta = {
        str(row["key"]): {"label": row.get("label"), "category": row.get("category")}
        for row in prop_def_rows
        if row.get("key")
    }
    prop_rankings_by_fixture: dict[int, list[dict[str, Any]]] = defaultdict(list)
    prop_rows = select_all_in_chunks(
        repository,
        "player_prop_rankings",
        (
            "prop_key,league_id,season,scope,player_id,player_name,team_id,team_name,"
            "sample,hits,percentage,current_streak,longest_streak,next_fixture_id,next_venue_scope,rank"
        ),
        in_column="next_fixture_id",
        values=fixture_ids,
        eq={"league_id": league_id, "season": season},
    )
    for row in prop_rows:
        next_fixture_id = maybe_int(row.get("next_fixture_id"))
        if next_fixture_id is not None:
            prop_rankings_by_fixture[next_fixture_id].append(row)

    all_rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        fixture_id = int(fixture["id"])
        home_id = int(fixture["home_team_id"])
        away_id = int(fixture["away_team_id"])
        home_name = str(teams_by_id.get(home_id, {}).get("name", f"Team {home_id}"))
        away_name = str(teams_by_id.get(away_id, {}).get("name", f"Team {away_id}"))

        candidates: list[dict[str, Any]] = []
        candidates.extend(team_market_candidates(
            fixture=fixture, side="home", team_id=home_id, opponent_id=away_id, opponent_name=away_name,
            teams_by_id=teams_by_id, market_keys=market_keys, market_meta=market_meta,
            stats_by_key=stats_by_key, league_id=league_id, season=season,
        ))
        candidates.extend(team_market_candidates(
            fixture=fixture, side="away", team_id=away_id, opponent_id=home_id, opponent_name=home_name,
            teams_by_id=teams_by_id, market_keys=market_keys, market_meta=market_meta,
            stats_by_key=stats_by_key, league_id=league_id, season=season,
        ))

        referee_id = maybe_int(fixture.get("referee_id"))
        referee_name = referees_by_id.get(referee_id) if referee_id is not None else fixture.get("referee_name_raw")
        referee_chip, amplifying = referee_context_for_fixture(
            fixture=fixture, referee_id=referee_id, referee_name=referee_name,
            referee_stats_by_id=referee_stats_by_id, market_meta=market_meta,
        )
        apply_referee_amplification(candidates, amplifying, referee_name)
        if referee_chip is not None:
            candidates.append(referee_chip)

        candidates.extend(player_prop_candidates(
            fixture=fixture, rankings=prop_rankings_by_fixture.get(fixture_id, []), prop_meta=prop_meta,
        ))

        all_rows.extend(finalize_fixture_rows(
            fixture=fixture, candidates=candidates, teams_by_id=teams_by_id,
            league=league, league_id=league_id, season=season,
        ))

    if full_refresh:
        delete_projection_rows(repository, filters, "full refresh delete fixture_signals")

    replace_projection_rows(repository, all_rows, filters=filters)
    LOGGER.info(
        "Fixture Signals ready league=%s season=%s fixtures=%s signals=%s",
        league_id, season, len(fixtures), len(all_rows),
    )
    return len(all_rows)


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    if args.leagues:
        target_leagues = parse_target_leagues(args.leagues)
    else:
        target_leagues = resolve_target_leagues(args, settings, repository, feature="fixtures", season=args.season)

    total = 0
    for league_id in target_leagues:
        total += rebuild_league(
            repository,
            league_id,
            args.season,
            window_days=args.window_days,
            full_refresh=args.full_refresh,
        )
    LOGGER.info("Fixture Signals V1 rebuilt season=%s leagues=%s signals=%s", args.season, len(target_leagues), total)


if __name__ == "__main__":
    main()
