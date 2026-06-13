from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Sequence

from pipeline_core import StufRepository, configure_logging, create_supabase_client, load_settings, utcnow
from stat_average_engine import _context_from_fact as _average_context_from_fact
from stat_average_engine import _to_row as _average_row
from trend_engine import MARKET_RULES, _context_from_fact, _current_streak, _longest_streak, _percentage

LOGGER = configure_logging("stuf.national-evidence")


FACT_SELECT = """
fixture_id,
team_id,
opponent_team_id,
league_id,
season,
played_at,
venue_scope,
result,
goals_for,
goals_against,
total_match_goals,
goals_for_1h,
goals_against_1h,
total_1h_goals,
goals_for_2h,
goals_against_2h,
total_2h_goals,
corners_for,
corners_against,
total_corners,
corners_for_1h,
corners_against_1h,
total_corners_1h,
corners_for_2h,
corners_against_2h,
total_corners_2h,
cards_for,
cards_against,
total_cards,
booking_points_for,
booking_points_against,
total_booking_points,
fouls_committed,
fouls_won,
total_fouls,
offsides_for,
offsides_against,
total_offsides,
total_shots_for,
total_shots_against,
shots_on_target_for,
shots_on_target_against,
goal_kicks_for,
goal_kicks_against,
total_goal_kicks,
throw_ins_for,
throw_ins_against,
total_throw_ins,
tackles_for,
tackles_against,
total_tackles
"""


@dataclass(frozen=True)
class EvidenceSource:
    target_league_id: int
    target_season: int
    source_league_id: int
    source_season: int
    source_label: str
    priority: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project national-team source evidence into a target tournament scope."
    )
    parser.add_argument("--target-league", type=int, default=1)
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--window-months", type=int, default=24)
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def select_all(
    repository: StufRepository,
    table: str,
    select: str,
    *,
    eq: dict[str, Any] | None = None,
    in_filters: dict[str, Sequence[Any]] | None = None,
    gte: dict[str, Any] | None = None,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(select)
            for key, value in (eq or {}).items():
                query = query.eq(key, value)
            for key, values in (in_filters or {}).items():
                query = query.in_(key, list(values))
            for key, value in (gte or {}).items():
                query = query.gte(key, value)
            return query.range(offset, offset + page_size - 1)

        response = repository._execute(request, f"select {table} offset={offset}")
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def load_sources(repository: StufRepository, target_league_id: int, target_season: int) -> list[EvidenceSource]:
    rows = select_all(
        repository,
        "national_team_evidence_sources",
        "target_league_id,target_season,source_league_id,source_season,source_label,priority,is_active",
        eq={"target_league_id": target_league_id, "target_season": target_season, "is_active": True},
    )
    return sorted(
        [
            EvidenceSource(
                target_league_id=int(row["target_league_id"]),
                target_season=int(row["target_season"]),
                source_league_id=int(row["source_league_id"]),
                source_season=int(row["source_season"]),
                source_label=str(row["source_label"]),
                priority=int(row.get("priority") or 50),
            )
            for row in rows
        ],
        key=lambda item: (item.priority, item.source_league_id, item.source_season),
    )


def load_target_team_ids(repository: StufRepository, target_league_id: int, target_season: int) -> tuple[int, ...]:
    rows = select_all(
        repository,
        "team_league_seasons",
        "team_id",
        eq={"league_id": target_league_id, "season": target_season, "is_active": True},
    )
    return tuple(sorted({int(row["team_id"]) for row in rows if row.get("team_id") is not None}))


def load_active_market_keys(repository: StufRepository) -> set[str]:
    rows = select_all(
        repository,
        "market_definitions",
        "key,is_active",
        eq={"is_active": True},
    )
    return {str(row["key"]) for row in rows if row.get("key")}


def load_organic_target_teams(repository: StufRepository, target_league_id: int, target_season: int) -> set[int]:
    rows = select_all(
        repository,
        "team_fixture_facts",
        "team_id",
        eq={"league_id": target_league_id, "season": target_season},
    )
    return {int(row["team_id"]) for row in rows if row.get("team_id") is not None}


def load_source_facts(
    repository: StufRepository,
    *,
    team_ids: tuple[int, ...],
    sources: list[EvidenceSource],
    cutoff_iso: str,
) -> list[dict[str, Any]]:
    source_pairs = {(source.source_league_id, source.source_season) for source in sources}
    league_ids = sorted({league_id for league_id, _ in source_pairs})
    seasons = sorted({season for _, season in source_pairs})
    facts: list[dict[str, Any]] = []

    for team_chunk in chunked(team_ids, 50):
        rows = select_all(
            repository,
            "team_fixture_facts",
            FACT_SELECT,
            in_filters={"team_id": team_chunk, "league_id": league_ids, "season": seasons},
            gte={"played_at": cutoff_iso},
        )
        facts.extend(
            row
            for row in rows
            if (int(row["league_id"]), int(row["season"])) in source_pairs
        )

    facts.sort(key=lambda row: str(row["played_at"]), reverse=True)
    return facts


def build_source_match_results(
    facts: list[dict[str, Any]],
    rules: Sequence[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fact in facts:
        context = _context_from_fact(fact)
        for scope_name in ("overall", context.scope):
            for rule in rules:
                if not rule.sample_predicate(context):
                    continue
                rows.append(
                    {
                        "fixture_id": context.fixture_id,
                        "team_id": context.team_id,
                        "opponent_team_id": context.opponent_team_id,
                        "league_id": context.league_id,
                        "season": context.season,
                        "played_at": context.played_at,
                        "scope": scope_name,
                        "market_key": rule.key,
                        "result": bool(rule.predicate(context)),
                        "numeric_value": rule.value_getter(context),
                        "created_at": utcnow().isoformat(),
                    }
                )
    return rows


def build_projected_market_stats(
    *,
    team_id: int,
    facts: list[dict[str, Any]],
    rules: Sequence[Any],
    target_league_id: int,
    target_season: int,
    projected_source: dict[str, Any],
) -> list[dict[str, Any]]:
    contexts = [_context_from_fact(row) for row in facts]
    contexts.sort(key=lambda item: item.played_at, reverse=True)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        for scope_name in ("overall", context.scope):
            for rule in rules:
                if not rule.sample_predicate(context):
                    continue
                grouped[(scope_name, rule.key)].append(
                    {
                        "fixture_id": context.fixture_id,
                        "played_at": context.played_at,
                        "scope": scope_name,
                        "market_key": rule.key,
                        "result": bool(rule.predicate(context)),
                        "numeric_value": rule.value_getter(context),
                    }
                )

    rows: list[dict[str, Any]] = []
    for rule in rules:
        for scope_name in ("overall", "home", "away"):
            group = grouped.get((scope_name, rule.key), [])
            if not group:
                continue
            sample = len(group)
            hits = sum(1 for item in group if item["result"])
            last_5 = group[:5]
            last_10 = group[:10]
            last_5_hits = sum(1 for item in last_5 if item["result"])
            last_10_hits = sum(1 for item in last_10 if item["result"])
            rows.append(
                {
                    "team_id": team_id,
                    "league_id": target_league_id,
                    "season": target_season,
                    "scope": scope_name,
                    "market_key": rule.key,
                    "category": rule.category,
                    "sample": sample,
                    "hits": hits,
                    "percentage": _percentage(hits, sample),
                    "current_streak": _current_streak(group),
                    "longest_streak": _longest_streak(group),
                    "last_5_sample": len(last_5),
                    "last_5_hits": last_5_hits,
                    "last_5_percentage": _percentage(last_5_hits, len(last_5)),
                    "last_10_sample": len(last_10),
                    "last_10_hits": last_10_hits,
                    "last_10_percentage": _percentage(last_10_hits, len(last_10)),
                    "projected_source": projected_source,
                    "updated_at": utcnow().isoformat(),
                }
            )
    return rows


def build_projected_average_rows(
    *,
    team_id: int,
    facts: list[dict[str, Any]],
    target_league_id: int,
    target_season: int,
    projected_source: dict[str, Any],
) -> list[dict[str, Any]]:
    contexts = [_average_context_from_fact(row) for row in facts]
    rows = [
        row
        for row in (
            _average_row(team_id, target_league_id, target_season, "overall", contexts),
            _average_row(team_id, target_league_id, target_season, "home", [item for item in contexts if item.scope == "home"]),
            _average_row(team_id, target_league_id, target_season, "away", [item for item in contexts if item.scope == "away"]),
        )
        if row is not None
    ]
    return [{**row, "projected_source": projected_source} for row in rows]


def delete_projected_rows(
    repository: StufRepository,
    table: str,
    target_league_id: int,
    target_season: int,
    team_ids: tuple[int, ...] = (),
    chunk_size: int = 10,
) -> None:
    """Delete projected rows chunked by team_id to avoid Supabase statement timeout."""
    if not team_ids:
        def request():
            return (
                repository.supabase.table(table)
                .delete()
                .eq("league_id", target_league_id)
                .eq("season", target_season)
                .filter("projected_source", "not.is", "null")
            )
        repository._execute(request, f"delete projected {table} league={target_league_id} season={target_season}")
        return

    for i in range(0, len(team_ids), chunk_size):
        chunk = list(team_ids[i : i + chunk_size])

        def request(chunk: list[int] = chunk):
            return (
                repository.supabase.table(table)
                .delete()
                .eq("league_id", target_league_id)
                .eq("season", target_season)
                .in_("team_id", chunk)
                .filter("projected_source", "not.is", "null")
            )

        repository._execute(request, f"delete projected {table} league={target_league_id} season={target_season} chunk={chunk}")


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    sources = load_sources(repository, args.target_league, args.target_season)
    if not sources:
        raise RuntimeError("No active national_team_evidence_sources rows found.")
    target_team_ids = load_target_team_ids(repository, args.target_league, args.target_season)
    if not target_team_ids:
        raise RuntimeError("No target World Cup team ids found.")

    organic_target_teams = load_organic_target_teams(repository, args.target_league, args.target_season)
    if organic_target_teams:
        LOGGER.warning(
            "Organic target facts exist for teams=%s. Those teams will not receive projected stats.",
            ",".join(str(team_id) for team_id in sorted(organic_target_teams)),
        )
    eligible_team_ids = tuple(team_id for team_id in target_team_ids if team_id not in organic_target_teams)

    active_market_keys = load_active_market_keys(repository)
    rules = tuple(rule for rule in MARKET_RULES if rule.key in active_market_keys)
    if not rules:
        raise RuntimeError("No active market rules matched market_definitions.")

    cutoff = utcnow() - timedelta(days=max(1, args.window_months) * 31)
    facts = load_source_facts(
        repository,
        team_ids=eligible_team_ids,
        sources=sources,
        cutoff_iso=cutoff.isoformat(),
    )
    facts_by_team: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in facts:
        facts_by_team[int(row["team_id"])].append(row)

    source_pairs = sorted({(source.source_league_id, source.source_season) for source in sources})
    built_at = utcnow().isoformat()
    projected_stats: list[dict[str, Any]] = []
    projected_averages: list[dict[str, Any]] = []

    for team_id in eligible_team_ids:
        team_facts = facts_by_team.get(team_id, [])
        if not team_facts:
            LOGGER.warning("No source facts for target team=%s; no projected rows emitted.", team_id)
            continue
        projected_source = {
            "projection": "national_team_evidence_v1",
            "target_league_id": args.target_league,
            "target_season": args.target_season,
            "window_months": args.window_months,
            "built_at": built_at,
            "source_pairs": [
                {"league_id": league_id, "season": season}
                for league_id, season in source_pairs
            ],
            "source_fixture_count": len({int(row["fixture_id"]) for row in team_facts}),
        }
        projected_stats.extend(
            build_projected_market_stats(
                team_id=team_id,
                facts=team_facts,
                rules=rules,
                target_league_id=args.target_league,
                target_season=args.target_season,
                projected_source=projected_source,
            )
        )
        projected_averages.extend(
            build_projected_average_rows(
                team_id=team_id,
                facts=team_facts,
                target_league_id=args.target_league,
                target_season=args.target_season,
                projected_source=projected_source,
            )
        )

    source_match_results = build_source_match_results(facts, rules)
    LOGGER.info(
        "Projection plan: target_teams=%s eligible=%s source_facts=%s source_match_results=%s projected_stats=%s projected_averages=%s dry_run=%s",
        len(target_team_ids),
        len(eligible_team_ids),
        len(facts),
        len(source_match_results),
        len(projected_stats),
        len(projected_averages),
        args.dry_run,
    )

    if args.dry_run:
        return

    if args.full_refresh:
        delete_projected_rows(repository, "team_season_market_stats", args.target_league, args.target_season, eligible_team_ids)
        delete_projected_rows(repository, "team_stat_averages", args.target_league, args.target_season, eligible_team_ids)

    if source_match_results:
        repository._upsert_rows(
            "team_match_market_results",
            source_match_results,
            "fixture_id,team_id,scope,market_key",
            "upsert national source team_match_market_results",
        )
    if projected_stats:
        repository._upsert_rows(
            "team_season_market_stats",
            projected_stats,
            "team_id,league_id,season,scope,market_key",
            "upsert projected national team_season_market_stats",
        )
    if projected_averages:
        repository._upsert_rows(
            "team_stat_averages",
            projected_averages,
            "team_id,league_id,season,scope",
            "upsert projected national team_stat_averages",
        )

    LOGGER.info("National evidence projection complete.")


if __name__ == "__main__":
    main()
