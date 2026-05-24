from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from market_catalog import ensure_market_definitions
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

LOGGER = configure_logging("stuf.market_serving")

SCOPES = ("overall", "home", "away")
UPCOMING_STATUSES = ("NS", "TBD")
NEXT_FIXTURE_WINDOW_DAYS = 6
PAGE_SIZE = 1000
MARKET_CATEGORIES = {"corners", "goals", "shots"}


@dataclass(frozen=True)
class NextFixture:
    fixture_id: int
    opponent_team_id: int
    opponent_name: str
    venue_scope: str
    played_at: str
    home_team_id: int
    away_team_id: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenera Market Serving Layer V1 desde tablas canonicas/analiticas.",
    )
    parser.add_argument("--category", choices=sorted(MARKET_CATEGORIES), default="corners")
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--leagues", help="Lista CSV de league_id, ej: 39,61,78,135,140.")
    parser.add_argument("--market-key", help="Market key puntual a regenerar.")
    parser.add_argument("--full-refresh", action="store_true", help="Borra la proyeccion antes de regenerar el scope solicitado.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Compatibilidad CLI. No se usa porque este job solo lee/escribe Supabase.",
    )
    return parser.parse_args()


def select_all(
    repository: StufRepository,
    table: str,
    columns: str,
    *,
    eq: dict[str, Any] | None = None,
    in_filters: dict[str, Iterable[Any]] | None = None,
    gte: dict[str, Any] | None = None,
    lt: dict[str, Any] | None = None,
    order: tuple[str, bool] | None = None,
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
            if order:
                column, ascending = order
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


def required_float(value: Any) -> float:
    parsed = maybe_float(value)
    return parsed if parsed is not None else 0.0


def stat_key(market_key: str, team_id: int, scope: str) -> tuple[str, int, str]:
    return market_key, team_id, scope


def metric_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "sample": 0,
            "hits": 0,
            "percentage": 0.0,
            "current_streak": 0,
            "longest_streak": 0,
            "last_5_sample": None,
            "last_5_hits": None,
            "last_5_percentage": None,
            "last_10_sample": None,
            "last_10_hits": None,
            "last_10_percentage": None,
        }

    return {
        "sample": maybe_int(row.get("sample")) or 0,
        "hits": maybe_int(row.get("hits")) or 0,
        "percentage": required_float(row.get("percentage")),
        "current_streak": maybe_int(row.get("current_streak")) or 0,
        "longest_streak": maybe_int(row.get("longest_streak")) or 0,
        "last_5_sample": maybe_int(row.get("last_5_sample")),
        "last_5_hits": maybe_int(row.get("last_5_hits")),
        "last_5_percentage": maybe_float(row.get("last_5_percentage")),
        "last_10_sample": maybe_int(row.get("last_10_sample")),
        "last_10_hits": maybe_int(row.get("last_10_hits")),
        "last_10_percentage": maybe_float(row.get("last_10_percentage")),
    }


def profile_metric_fields(row: dict[str, Any] | None, scope: str) -> dict[str, Any]:
    fields = metric_fields(row) if row else {
        "sample": None,
        "hits": None,
        "percentage": None,
        "current_streak": None,
        "longest_streak": None,
        "last_5_sample": None,
        "last_5_hits": None,
        "last_5_percentage": None,
        "last_10_sample": None,
        "last_10_hits": None,
        "last_10_percentage": None,
    }
    return {
        f"{scope}_sample": fields["sample"],
        f"{scope}_hits": fields["hits"],
        f"{scope}_percentage": fields["percentage"],
        f"current_streak_{scope}": fields["current_streak"],
        f"longest_streak_{scope}": fields["longest_streak"],
        f"last_5_sample_{scope}": fields["last_5_sample"],
        f"last_5_hits_{scope}": fields["last_5_hits"],
        f"last_5_percentage_{scope}": fields["last_5_percentage"],
        f"last_10_sample_{scope}": fields["last_10_sample"],
        f"last_10_hits_{scope}": fields["last_10_hits"],
        f"last_10_percentage_{scope}": fields["last_10_percentage"],
    }


def support_fields(row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "opponent_support_sample": maybe_int(row.get("sample")) if row else None,
        "opponent_support_hits": maybe_int(row.get("hits")) if row else None,
        "opponent_support_percentage": maybe_float(row.get("percentage")) if row else None,
        "opponent_support_last_5_sample": maybe_int(row.get("last_5_sample")) if row else None,
        "opponent_support_last_5_hits": maybe_int(row.get("last_5_hits")) if row else None,
        "opponent_support_last_5_percentage": maybe_float(row.get("last_5_percentage")) if row else None,
        "opponent_support_last_10_sample": maybe_int(row.get("last_10_sample")) if row else None,
        "opponent_support_last_10_hits": maybe_int(row.get("last_10_hits")) if row else None,
        "opponent_support_last_10_percentage": maybe_float(row.get("last_10_percentage")) if row else None,
    }


def fixture_scope_for_opponent(scope: str, fixture: NextFixture | None) -> str | None:
    if fixture is None:
        return None
    if scope == "overall":
        return "overall"
    return "away" if fixture.venue_scope == "home" else "home"


def load_active_market_keys(repository: StufRepository, category: str, market_key: str | None) -> tuple[str, ...]:
    eq = {"category": category, "is_active": True}
    if market_key:
        eq["key"] = market_key
    rows = select_all(
        repository,
        "market_definitions",
        "key,is_active,display_order",
        eq=eq,
        order=("display_order", True),
    )
    market_keys = tuple(str(row["key"]) for row in rows if row.get("key"))
    if market_key and not market_keys:
        raise RuntimeError(f"Market key no activo o inexistente para category={category}: {market_key}")
    return market_keys


def load_league(repository: StufRepository, league_id: int) -> dict[str, Any]:
    rows = select_all(repository, "leagues", "id,name,logo_url", eq={"id": league_id})
    return rows[0] if rows else {"id": league_id, "name": f"League {league_id}", "logo_url": None}


def load_teams(repository: StufRepository, team_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted({int(team_id) for team_id in team_ids if team_id is not None})
    if not ids:
        return {}
    rows = select_all_in_chunks(repository, "teams", "id,name,logo_url", in_column="id", values=ids)
    return {int(row["id"]): row for row in rows}


def load_next_fixtures(
    repository: StufRepository,
    league_id: int,
    season: int,
    team_ids: Iterable[int],
) -> dict[int, NextFixture]:
    selected_team_ids = {int(team_id) for team_id in team_ids}
    if not selected_team_ids:
        return {}

    start = datetime.now(timezone.utc)
    end = start + timedelta(days=NEXT_FIXTURE_WINDOW_DAYS)
    fixture_team_rows = select_all(
        repository,
        "fixture_teams",
        "fixture_id,team_id,opponent_team_id,is_home,played_at",
        eq={"league_id": league_id, "season": season},
        gte={"played_at": start.isoformat()},
        lt={"played_at": end.isoformat()},
        order=("played_at", True),
    )
    fixture_team_rows = [row for row in fixture_team_rows if int(row["team_id"]) in selected_team_ids]
    fixture_ids = {int(row["fixture_id"]) for row in fixture_team_rows}
    if not fixture_ids:
        return {}

    fixture_rows = select_all_in_chunks(
        repository,
        "fixtures",
        "id,status_short,date",
        in_column="id",
        values=fixture_ids,
        in_filters={"status_short": UPCOMING_STATUSES},
        gte={"date": start.isoformat()},
        lt={"date": end.isoformat()},
    )
    eligible_fixture_ids = {int(row["id"]) for row in fixture_rows}
    eligible_rows = [row for row in fixture_team_rows if int(row["fixture_id"]) in eligible_fixture_ids]
    opponent_ids = {int(row["opponent_team_id"]) for row in eligible_rows}
    teams_by_id = load_teams(repository, opponent_ids)

    next_by_team: dict[int, NextFixture] = {}
    for row in eligible_rows:
        team_id = int(row["team_id"])
        if team_id in next_by_team:
            continue

        opponent_id = int(row["opponent_team_id"])
        is_home = bool(row["is_home"])
        opponent = teams_by_id.get(opponent_id)
        next_by_team[team_id] = NextFixture(
            fixture_id=int(row["fixture_id"]),
            opponent_team_id=opponent_id,
            opponent_name=str(opponent.get("name") if opponent else f"Team {opponent_id}"),
            venue_scope="home" if is_home else "away",
            played_at=str(row["played_at"]),
            home_team_id=team_id if is_home else opponent_id,
            away_team_id=opponent_id if is_home else team_id,
        )
    return next_by_team


def corner_values_for_market(
    market_key: str,
    fixture: dict[str, Any],
    facts_by_fixture_team: dict[tuple[int, int], dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    fixture_id = int(fixture["fixture_id"])
    home_team_id = int(fixture["home_team_id"])
    away_team_id = int(fixture["away_team_id"])
    home_fact = facts_by_fixture_team.get((fixture_id, home_team_id), {})
    away_fact = facts_by_fixture_team.get((fixture_id, away_team_id), {})

    if "EACH_HALF" in market_key:
        home_1h = maybe_float(home_fact.get("corners_for_1h"))
        away_1h = maybe_float(away_fact.get("corners_for_1h"))
        home_2h = maybe_float(home_fact.get("corners_for_2h"))
        away_2h = maybe_float(away_fact.get("corners_for_2h"))
        home_total = None if home_1h is None or home_2h is None else home_1h + home_2h
        away_total = None if away_1h is None or away_2h is None else away_1h + away_2h
        first_half_total = None if home_1h is None or away_1h is None else home_1h + away_1h
        second_half_total = None if home_2h is None or away_2h is None else home_2h + away_2h
        total_value = None if first_half_total is None or second_half_total is None else min(first_half_total, second_half_total)
        return home_total, away_total, total_value

    if "_1H_" in market_key:
        home = maybe_float(home_fact.get("corners_for_1h"))
        away = maybe_float(away_fact.get("corners_for_1h"))
        total = None if home is None or away is None else home + away
        return home, away, total

    if "_2H_" in market_key:
        home = maybe_float(home_fact.get("corners_for_2h"))
        away = maybe_float(away_fact.get("corners_for_2h"))
        total = None if home is None or away is None else home + away
        return home, away, total

    home = maybe_float(fixture.get("home_corners"))
    away = maybe_float(fixture.get("away_corners"))
    total = None if home is None or away is None else home + away
    return home, away, total


def shot_values_for_market(market_key: str, fixture: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    use_target = "SHOTS_ON_TARGET" in market_key
    home = maybe_float(fixture.get("home_shots_on_target" if use_target else "home_total_shots"))
    away = maybe_float(fixture.get("away_shots_on_target" if use_target else "away_total_shots"))

    if "EACH_TEAM" in market_key:
        total_value = None if home is None or away is None else min(home, away)
    else:
        total_value = None if home is None or away is None else home + away
    return home, away, total_value


def goal_values_for_market(
    market_key: str,
    fixture: dict[str, Any],
    facts_by_fixture_team: dict[tuple[int, int], dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    fixture_id = int(fixture["fixture_id"])
    home_team_id = int(fixture["home_team_id"])
    away_team_id = int(fixture["away_team_id"])
    home_fact = facts_by_fixture_team.get((fixture_id, home_team_id), {})
    away_fact = facts_by_fixture_team.get((fixture_id, away_team_id), {})

    if "_1H_" in market_key:
        home = maybe_float(home_fact.get("goals_for_1h"))
        away = maybe_float(away_fact.get("goals_for_1h"))
    elif "_2H_" in market_key:
        home = maybe_float(home_fact.get("goals_for_2h"))
        away = maybe_float(away_fact.get("goals_for_2h"))
    else:
        home = maybe_float(fixture.get("home_goals"))
        away = maybe_float(fixture.get("away_goals"))

    if "BOTH_HALVES" in market_key or "GOAL_RANGE" in market_key:
        home = maybe_float(fixture.get("home_goals"))
        away = maybe_float(fixture.get("away_goals"))

    total = None if home is None or away is None else home + away
    return home, away, total


def market_values_for_category(
    category: str,
    market_key: str,
    fixture: dict[str, Any],
    facts_by_fixture_team: dict[tuple[int, int], dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    if category == "corners":
        return corner_values_for_market(market_key, fixture, facts_by_fixture_team)
    if category == "goals":
        return goal_values_for_market(market_key, fixture, facts_by_fixture_team)
    if category == "shots":
        return shot_values_for_market(market_key, fixture)
    raise RuntimeError(f"Market Serving Layer no soporta evidencia para category={category}.")


def build_market_team_rankings(
    *,
    category: str,
    market_keys: tuple[str, ...],
    stats: list[dict[str, Any]],
    stats_by_key: dict[tuple[str, int, str], dict[str, Any]],
    next_by_team: dict[int, NextFixture],
    teams_by_id: dict[int, dict[str, Any]],
    league: dict[str, Any],
    league_id: int,
    season: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stats_by_market_scope: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in stats:
        stats_by_market_scope[(str(row["market_key"]), str(row["scope"]))].append(row)

    for market_key in market_keys:
        for scope in SCOPES:
            candidates = stats_by_market_scope.get((market_key, scope), [])
            candidates.sort(
                key=lambda row: (
                    -required_float(row.get("percentage")),
                    -(maybe_int(row.get("hits")) or 0),
                    -(maybe_int(row.get("sample")) or 0),
                    str(teams_by_id.get(int(row["team_id"]), {}).get("name", "")),
                )
            )
            for rank, stat in enumerate(candidates, start=1):
                team_id = int(stat["team_id"])
                team = teams_by_id.get(team_id, {})
                next_fixture = next_by_team.get(team_id)
                opponent_scope = fixture_scope_for_opponent(scope, next_fixture)
                opponent_stat = (
                    stats_by_key.get(stat_key(market_key, next_fixture.opponent_team_id, opponent_scope))
                    if next_fixture and opponent_scope
                    else None
                )
                rows.append({
                    "league_id": league_id,
                    "season": season,
                    "category": category,
                    "market_key": market_key,
                    "scope": scope,
                    "team_id": team_id,
                    "team_name": str(team.get("name", f"Team {team_id}")),
                    "team_logo_url": team.get("logo_url"),
                    "league_name": str(league.get("name", f"League {league_id}")),
                    "league_logo_url": league.get("logo_url"),
                    **metric_fields(stat),
                    "rank": rank,
                    "next_fixture_id": next_fixture.fixture_id if next_fixture else None,
                    "next_fixture_date": next_fixture.played_at if next_fixture else None,
                    "next_home_team_id": next_fixture.home_team_id if next_fixture else None,
                    "next_away_team_id": next_fixture.away_team_id if next_fixture else None,
                    "next_opponent_team_id": next_fixture.opponent_team_id if next_fixture else None,
                    "next_opponent_name": next_fixture.opponent_name if next_fixture else None,
                    "next_venue_scope": next_fixture.venue_scope if next_fixture else None,
                    "opponent_support_scope": opponent_scope,
                    **support_fields(opponent_stat),
                })
    return rows


def build_team_market_profiles(
    *,
    category: str,
    market_keys: tuple[str, ...],
    stats: list[dict[str, Any]],
    stats_by_key: dict[tuple[str, int, str], dict[str, Any]],
    next_by_team: dict[int, NextFixture],
    teams_by_id: dict[int, dict[str, Any]],
    league: dict[str, Any],
    league_id: int,
    season: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    teams_by_market: dict[str, set[int]] = defaultdict(set)
    for row in stats:
        teams_by_market[str(row["market_key"])].add(int(row["team_id"]))

    for market_key in market_keys:
        team_ids = sorted(teams_by_market.get(market_key, set()))
        team_ids.sort(
            key=lambda team_id: (
                -required_float((stats_by_key.get(stat_key(market_key, team_id, "overall")) or {}).get("percentage")),
                -(maybe_int((stats_by_key.get(stat_key(market_key, team_id, "overall")) or {}).get("sample")) or 0),
                str(teams_by_id.get(team_id, {}).get("name", "")),
            )
        )
        for team_id in team_ids:
            team = teams_by_id.get(team_id, {})
            next_fixture = next_by_team.get(team_id)
            rows.append({
                "league_id": league_id,
                "season": season,
                "category": category,
                "market_key": market_key,
                "team_id": team_id,
                "team_name": str(team.get("name", f"Team {team_id}")),
                "team_logo_url": team.get("logo_url"),
                "league_name": str(league.get("name", f"League {league_id}")),
                "league_logo_url": league.get("logo_url"),
                **profile_metric_fields(stats_by_key.get(stat_key(market_key, team_id, "overall")), "overall"),
                **profile_metric_fields(stats_by_key.get(stat_key(market_key, team_id, "home")), "home"),
                **profile_metric_fields(stats_by_key.get(stat_key(market_key, team_id, "away")), "away"),
                "next_fixture_id": next_fixture.fixture_id if next_fixture else None,
                "next_fixture_date": next_fixture.played_at if next_fixture else None,
                "next_home_team_id": next_fixture.home_team_id if next_fixture else None,
                "next_away_team_id": next_fixture.away_team_id if next_fixture else None,
                "next_opponent_team_id": next_fixture.opponent_team_id if next_fixture else None,
                "next_opponent_name": next_fixture.opponent_name if next_fixture else None,
                "next_venue_scope": next_fixture.venue_scope if next_fixture else None,
                "relevant_scope_for_next_fixture": next_fixture.venue_scope if next_fixture else None,
            })
    return rows


def build_team_market_match_evidence(
    *,
    category: str,
    market_keys: tuple[str, ...],
    match_results: list[dict[str, Any]],
    fixture_by_id: dict[int, dict[str, Any]],
    fixture_status_by_id: dict[int, str | None],
    facts_by_fixture_team: dict[tuple[int, int], dict[str, Any]],
    league_id: int,
    season: int,
) -> list[dict[str, Any]]:
    sorted_results = sorted(
        match_results,
        key=lambda row: str(row.get("played_at", "")),
        reverse=True,
    )
    valid_market_keys = set(market_keys)
    seen_keys: set[tuple[str, str, int, int, int, str, int]] = set()
    rows: list[dict[str, Any]] = []
    for result in sorted_results:
        market_key = str(result["market_key"])
        if market_key not in valid_market_keys:
            continue

        fixture_id = int(result["fixture_id"])
        fixture = fixture_by_id.get(fixture_id)
        if not fixture:
            continue

        team_id = int(result["team_id"])
        scope = str(result["scope"])
        dedupe_key = (category, market_key, league_id, season, team_id, scope, fixture_id)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        home_team_id = int(fixture["home_team_id"])
        away_team_id = int(fixture["away_team_id"])
        home_value, away_value, total_value = market_values_for_category(category, market_key, fixture, facts_by_fixture_team)
        team_value = home_value if team_id == home_team_id else away_value
        opponent_value = away_value if team_id == home_team_id else home_value

        rows.append({
            "league_id": league_id,
            "season": season,
            "category": category,
            "market_key": market_key,
            "team_id": team_id,
            "fixture_id": fixture_id,
            "played_at": result.get("played_at") or fixture["date"],
            "scope": scope,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home_team_name": str(fixture["home_team_name"]),
            "away_team_name": str(fixture["away_team_name"]),
            "team_value": team_value,
            "opponent_value": opponent_value,
            "home_value": home_value,
            "away_value": away_value,
            "total_value": total_value,
            "numeric_value": maybe_float(result.get("numeric_value")),
            "result": bool(result["result"]),
            "status_short": fixture_status_by_id.get(fixture_id),
        })
    return rows


def delete_projection_rows(
    repository: StufRepository,
    table: str,
    filters: dict[str, Any],
    operation: str,
    *,
    older_than: str | None = None,
) -> None:
    def request():
        query = repository.supabase.table(table).delete()
        for key, value in filters.items():
            query = query.eq(key, value)
        if older_than is not None:
            query = query.lt("computed_at", older_than)
        return query

    repository._execute(request, operation)


def replace_projection_rows(
    repository: StufRepository,
    table: str,
    rows: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
    on_conflict: str,
) -> None:
    if not rows:
        delete_projection_rows(repository, table, filters, f"delete empty {table}")
        return

    marker = utcnow().isoformat()
    stamped_rows = [{**row, "computed_at": marker} for row in rows]
    repository._upsert_rows(
        table,
        stamped_rows,
        on_conflict,
        f"upsert {table} category={filters.get('category')} league={filters.get('league_id')} season={filters.get('season')}",
    )
    delete_projection_rows(repository, table, filters, f"cleanup {table}", older_than=marker)


def rebuild_league_category(
    repository: StufRepository,
    category: str,
    league_id: int,
    season: int,
    *,
    market_key: str | None = None,
    full_refresh: bool = False,
) -> None:
    market_keys = load_active_market_keys(repository, category, market_key)
    if not market_keys:
        raise RuntimeError(f"No active market definitions for category={category}.")

    LOGGER.info(
        "Building Market Serving Layer category=%s league=%s season=%s markets=%s",
        category,
        league_id,
        season,
        len(market_keys),
    )
    league = load_league(repository, league_id)
    stats = select_all(
        repository,
        "team_season_market_stats",
        (
            "team_id,league_id,season,scope,market_key,sample,hits,percentage,current_streak,"
            "longest_streak,last_5_sample,last_5_hits,last_5_percentage,last_10_sample,"
            "last_10_hits,last_10_percentage"
        ),
        eq={"category": category, "league_id": league_id, "season": season},
        in_filters={"market_key": market_keys},
    )
    stats_by_key = {
        stat_key(str(row["market_key"]), int(row["team_id"]), str(row["scope"])): row
        for row in stats
    }
    team_ids = {int(row["team_id"]) for row in stats}
    next_by_team = load_next_fixtures(repository, league_id, season, team_ids)
    teams_by_id = load_teams(repository, team_ids | {fixture.opponent_team_id for fixture in next_by_team.values()})

    fixture_rows = select_all(
        repository,
        "fixture_team_summary",
        (
            "fixture_id,date,league_id,season,home_team_id,home_team_name,away_team_id,away_team_name,"
            "home_goals,away_goals,home_corners,away_corners,home_total_shots,away_total_shots,"
            "home_shots_on_target,away_shots_on_target"
        ),
        eq={"league_id": league_id, "season": season},
    )
    fixture_by_id = {int(row["fixture_id"]): row for row in fixture_rows}
    fixture_status_rows = select_all(
        repository,
        "fixtures",
        "id,status_short,date",
        eq={"league_id": league_id, "season": season},
    )
    fixture_status_by_id = {
        int(row["id"]): str(row["status_short"]) if row.get("status_short") is not None else None
        for row in fixture_status_rows
    }
    fact_rows = select_all(
        repository,
        "team_fixture_facts",
        (
            "fixture_id,team_id,corners_for_1h,corners_for_2h,goals_for,goals_against,total_match_goals,"
            "goals_for_1h,goals_against_1h,total_1h_goals,goals_for_2h,goals_against_2h,total_2h_goals"
        ),
        eq={"league_id": league_id, "season": season},
    )
    facts_by_fixture_team = {
        (int(row["fixture_id"]), int(row["team_id"])): row
        for row in fact_rows
    }
    match_results = select_all(
        repository,
        "team_match_market_results",
        "fixture_id,team_id,league_id,season,played_at,scope,market_key,result,numeric_value",
        eq={"league_id": league_id, "season": season},
        in_filters={"market_key": market_keys},
        order=("played_at", False),
    )

    rankings = build_market_team_rankings(
        category=category,
        market_keys=market_keys,
        stats=stats,
        stats_by_key=stats_by_key,
        next_by_team=next_by_team,
        teams_by_id=teams_by_id,
        league=league,
        league_id=league_id,
        season=season,
    )
    profiles = build_team_market_profiles(
        category=category,
        market_keys=market_keys,
        stats=stats,
        stats_by_key=stats_by_key,
        next_by_team=next_by_team,
        teams_by_id=teams_by_id,
        league=league,
        league_id=league_id,
        season=season,
    )
    evidence = build_team_market_match_evidence(
        category=category,
        market_keys=market_keys,
        match_results=match_results,
        fixture_by_id=fixture_by_id,
        fixture_status_by_id=fixture_status_by_id,
        facts_by_fixture_team=facts_by_fixture_team,
        league_id=league_id,
        season=season,
    )

    filters = {"category": category, "league_id": league_id, "season": season}
    if market_key:
        filters["market_key"] = market_key

    if full_refresh:
        for table in ("market_team_rankings", "team_market_profiles", "team_market_match_evidence"):
            delete_projection_rows(repository, table, filters, f"full refresh delete {table}")

    replace_projection_rows(
        repository,
        "market_team_rankings",
        rankings,
        filters=filters,
        on_conflict="category,market_key,league_id,season,scope,team_id",
    )
    replace_projection_rows(
        repository,
        "team_market_profiles",
        profiles,
        filters=filters,
        on_conflict="category,market_key,league_id,season,team_id",
    )
    replace_projection_rows(
        repository,
        "team_market_match_evidence",
        evidence,
        filters=filters,
        on_conflict="category,market_key,league_id,season,team_id,scope,fixture_id",
    )
    LOGGER.info(
        "Market Serving Layer ready category=%s league=%s season=%s rankings=%s profiles=%s evidence=%s",
        category,
        league_id,
        season,
        len(rankings),
        len(profiles),
        len(evidence),
    )


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    if args.leagues:
        target_leagues = parse_target_leagues(args.leagues)
    else:
        target_leagues = resolve_target_leagues(args, settings, repository, feature="pipeline", season=args.season)

    for league_id in target_leagues:
        rebuild_league_category(
            repository,
            args.category,
            league_id,
            args.season,
            market_key=args.market_key,
            full_refresh=args.full_refresh,
        )
    LOGGER.info("Market Serving Layer V1 rebuilt category=%s season=%s.", args.category, args.season)


if __name__ == "__main__":
    main()
