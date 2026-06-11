from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fetch_historical_limited import hydrate_fixture_details
from pipeline_core import (
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    is_final_status,
    league_supports,
    load_settings,
    sync_reference_catalogs,
    utcnow,
)

LOGGER = configure_logging("stuf.national-history")


@dataclass(frozen=True)
class EvidenceSource:
    target_league_id: int
    target_season: int
    source_league_id: int
    source_season: int
    source_label: str
    priority: int


@dataclass(frozen=True)
class PlannedFixture:
    fixture_id: int
    payload: dict[str, Any]
    source: EvidenceSource
    requested_team_ids: tuple[int, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Targeted national-team history ingest for World Cup evidence."
    )
    parser.add_argument("--target-league", type=int, default=1)
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--window-months", type=int, default=24)
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Minimum pause between API-Football requests.",
    )
    parser.add_argument("--skip-players", action="store_true", help="Do not call /fixtures/players.")
    parser.add_argument(
        "--include-predictions",
        action="store_true",
        help="Also hydrate /predictions. Off by default because Phase 4 evidence does not need it.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rehydrate fixture details even if STUF already marks them complete.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write fixture shells/details/facts. Without this flag the script only plans and estimates.",
    )
    parser.add_argument(
        "--max-fixtures",
        type=int,
        help="Debug guard: hydrate at most N unique fixtures after planning.",
    )
    return parser.parse_args()


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def select_all(repository: StufRepository, table: str, select: str, *, page_size: int = 1000, **filters) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(select)
            for key, value in filters.items():
                query = query.eq(key, value)
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
        target_league_id=target_league_id,
        target_season=target_season,
        is_active=True,
    )
    sources = [
        EvidenceSource(
            target_league_id=int(row["target_league_id"]),
            target_season=int(row["target_season"]),
            source_league_id=int(row["source_league_id"]),
            source_season=int(row["source_season"]),
            source_label=str(row["source_label"]),
            priority=int(row.get("priority") or 50),
        )
        for row in rows
    ]
    return sorted(sources, key=lambda item: (item.priority, item.source_league_id, item.source_season))


def load_target_team_ids(repository: StufRepository, target_league_id: int, target_season: int) -> tuple[int, ...]:
    rows = select_all(
        repository,
        "team_league_seasons",
        "team_id",
        league_id=target_league_id,
        season=target_season,
        is_active=True,
    )
    return tuple(sorted({int(row["team_id"]) for row in rows if row.get("team_id") is not None}))


def fixture_source_key(fixture: dict[str, Any]) -> tuple[int, int] | None:
    league = fixture.get("league") or {}
    league_id = league.get("id")
    season = league.get("season")
    if league_id is None or season is None:
        return None
    return int(league_id), int(season)


def fixture_team_ids(fixture: dict[str, Any]) -> set[int]:
    teams = fixture.get("teams") or {}
    ids = {
        ((teams.get("home") or {}).get("id")),
        ((teams.get("away") or {}).get("id")),
    }
    return {int(team_id) for team_id in ids if team_id is not None}


def estimate_detail_requests(
    planned: list[PlannedFixture],
    coverage_map: dict[tuple[int, int], dict[str, Any]],
    *,
    include_players: bool,
    include_predictions: bool,
) -> int:
    total = 0
    for item in planned:
        source_key = (item.source.source_league_id, item.source.source_season)
        coverage_known = source_key in coverage_map
        if not coverage_known or league_supports(coverage_map, *source_key, "fixtures_statistics"):
            total += 1
        if not coverage_known or league_supports(coverage_map, *source_key, "fixtures_events"):
            total += 1
        if include_players and (not coverage_known or league_supports(coverage_map, *source_key, "fixtures_players_statistics")):
            total += 1
        if include_predictions and (not coverage_known or league_supports(coverage_map, *source_key, "predictions")):
            total += 1
    return total


async def plan_fixtures(
    api_client: ApiFootballClient,
    *,
    team_ids: tuple[int, ...],
    sources: list[EvidenceSource],
    cutoff: datetime,
) -> tuple[list[PlannedFixture], int]:
    source_by_pair = {(source.source_league_id, source.source_season): source for source in sources}
    source_seasons = sorted({
        source.source_season
        for source in sources
        # Keep Africa's API season=2023 for the 2026 cycle, but avoid querying
        # old World Cup 2022 under the default 24-month window.
        if source.source_season >= cutoff.year - 1
    })
    planned_by_fixture: dict[int, PlannedFixture] = {}
    list_requests = 0
    mismatches = 0

    for team_id in team_ids:
        for season in source_seasons:
            payload = await api_client.fetch(
                "fixtures",
                {"team": team_id, "season": season, "status": "FT-AET-PEN"},
            )
            list_requests += 1
            for fixture in (payload or {}).get("response", []):
                fixture_info = fixture.get("fixture") or {}
                fixture_id = fixture_info.get("id")
                status = (fixture_info.get("status") or {}).get("short")
                played_at = parse_api_datetime(fixture_info.get("date"))
                source_key = fixture_source_key(fixture)
                if not fixture_id or not is_final_status(status) or not played_at:
                    continue
                if played_at < cutoff:
                    continue
                if source_key not in source_by_pair:
                    continue
                team_ids_in_fixture = fixture_team_ids(fixture)
                if team_id not in team_ids_in_fixture:
                    mismatches += 1
                    LOGGER.warning(
                        "Fixture %s returned for team=%s but payload teams are %s; skipping.",
                        fixture_id,
                        team_id,
                        sorted(team_ids_in_fixture),
                    )
                    continue

                source = source_by_pair[source_key]
                existing = planned_by_fixture.get(int(fixture_id))
                if existing is None:
                    planned_by_fixture[int(fixture_id)] = PlannedFixture(
                        fixture_id=int(fixture_id),
                        payload=fixture,
                        source=source,
                        requested_team_ids=(team_id,),
                    )
                elif team_id not in existing.requested_team_ids:
                    planned_by_fixture[int(fixture_id)] = PlannedFixture(
                        fixture_id=existing.fixture_id,
                        payload=existing.payload,
                        source=existing.source,
                        requested_team_ids=tuple(sorted((*existing.requested_team_ids, team_id))),
                    )

    if mismatches:
        LOGGER.warning("Team mapping mismatches skipped=%s", mismatches)

    return (
        sorted(
            planned_by_fixture.values(),
            key=lambda item: (parse_api_datetime((item.payload.get("fixture") or {}).get("date")) or cutoff),
        ),
        list_requests,
    )


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    sources = load_sources(repository, args.target_league, args.target_season)
    if not sources:
        raise RuntimeError(
            f"No active national evidence sources for target={args.target_league}/{args.target_season}. "
            "Run schema/015_national_team_evidence.sql first."
        )

    team_ids = load_target_team_ids(repository, args.target_league, args.target_season)
    if not team_ids:
        raise RuntimeError(
            f"No target teams found in team_league_seasons for league={args.target_league} season={args.target_season}."
        )

    cutoff = utcnow() - timedelta(days=max(1, args.window_months) * 31)
    include_players = not args.skip_players
    include_predictions = bool(args.include_predictions)

    LOGGER.info(
        "Planning national history target=%s/%s teams=%s sources=%s cutoff=%s apply=%s players=%s predictions=%s",
        args.target_league,
        args.target_season,
        len(team_ids),
        len(sources),
        cutoff.date().isoformat(),
        args.apply,
        include_players,
        include_predictions,
    )

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = repository.load_coverage_map()
        if args.apply:
            source_league_ids = sorted({source.source_league_id for source in sources})
            coverage_map = await sync_reference_catalogs(
                api_client,
                repository,
                settings,
                target_leagues=source_league_ids,
                include_odds_catalogs=False,
            )

        planned, list_requests = await plan_fixtures(
            api_client,
            team_ids=team_ids,
            sources=sources,
            cutoff=cutoff,
        )
        if args.max_fixtures is not None:
            planned = planned[: max(0, args.max_fixtures)]

        detail_estimate = estimate_detail_requests(
            planned,
            coverage_map,
            include_players=include_players,
            include_predictions=include_predictions,
        )
        LOGGER.info(
            "National history plan: list_requests=%s unique_fixtures=%s estimated_detail_requests=%s estimated_total_api=%s",
            list_requests,
            len(planned),
            detail_estimate,
            list_requests + detail_estimate,
        )

        by_source: dict[tuple[int, int, str], int] = {}
        for item in planned:
            key = (item.source.source_league_id, item.source.source_season, item.source.source_label)
            by_source[key] = by_source.get(key, 0) + 1
        for (league_id, season, label), count in sorted(by_source.items()):
            LOGGER.info("Planned source league=%s season=%s label=%s fixtures=%s", league_id, season, label, count)

        if not args.apply:
            LOGGER.info("Dry-run only. Re-run with --apply to hydrate fixtures and rebuild facts.")
            return

        for index, item in enumerate(planned, start=1):
            LOGGER.info(
                "Hydrating national fixture %s/%s fixture=%s source=%s/%s teams=%s",
                index,
                len(planned),
                item.fixture_id,
                item.source.source_league_id,
                item.source.source_season,
                ",".join(str(team_id) for team_id in item.requested_team_ids),
            )
            await hydrate_fixture_details(
                api_client,
                repository,
                coverage_map,
                item.payload,
                skip_known=False if args.force else None,
                include_players=include_players,
                include_predictions=include_predictions,
                refresh_derived=False,
            )
            await asyncio.sleep(0.25)

    LOGGER.info("National history ingest complete. fixtures=%s apply=%s", len(planned), args.apply)


if __name__ == "__main__":
    asyncio.run(main())
