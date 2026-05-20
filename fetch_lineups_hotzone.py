import asyncio
from datetime import timedelta

from pipeline_core import (
    UPCOMING_STATUSES,
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    league_supports,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
    sync_reference_catalogs,
    utcnow,
)

LOGGER = configure_logging("stuf.lineups")


async def main() -> None:
    args = parse_cli_args("Carril D - radar tactico de alineaciones.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args, settings)

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = await sync_reference_catalogs(
            api_client,
            repository,
            settings,
            target_leagues=target_leagues,
            include_odds_catalogs=False,
        )

        now = utcnow()
        hotzone_end = now + timedelta(minutes=90)
        fixtures = repository.get_candidate_fixtures(now, hotzone_end, sorted(UPCOMING_STATUSES))

        candidates = [
            row
            for row in fixtures
            if row.get("league_id") in target_leagues
            and league_supports(coverage_map, row.get("league_id"), row.get("season"), "fixtures_lineups")
            and not repository.has_lineups(row["id"])
        ]

        LOGGER.info("Fixtures en hot zone para lineups: %s", len(candidates))

        for fixture in candidates:
            fixture_id = fixture["id"]
            payload = await api_client.fetch("fixtures/lineups", {"fixture": fixture_id})
            if payload is not None:
                repository.mark_fixture_hydration(fixture_id, hydrated_lineups=True)
            lineup_rows = (payload or {}).get("response", [])
            if lineup_rows:
                repository.upsert_lineups(fixture_id, lineup_rows)
                LOGGER.info("Lineups disponibles para fixture %s.", fixture_id)
            else:
                LOGGER.info("Fixture %s aun sin lineups publicadas.", fixture_id)

            await asyncio.sleep(0.35)

    LOGGER.info("Radar tactico finalizado.")


if __name__ == "__main__":
    asyncio.run(main())
