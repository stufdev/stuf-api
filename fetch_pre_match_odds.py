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

LOGGER = configure_logging("stuf.odds")


def resolve_bookmaker(repository: StufRepository, bookmaker_name: str) -> dict | None:
    normalized_target = bookmaker_name.strip().lower()
    for bookmaker in repository.get_bookmakers():
        name = (bookmaker.get("name") or "").strip().lower()
        if name == normalized_target:
            return bookmaker
    return None


async def main() -> None:
    args = parse_cli_args("Carril D - captura de cuotas pre-match.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args, settings)

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = await sync_reference_catalogs(api_client, repository, settings, target_leagues=target_leagues)

        bookmaker = resolve_bookmaker(repository, settings.pinnacle_bookmaker_name)
        if bookmaker is None:
            LOGGER.error(
                "No se encontro el bookmaker '%s' en el catalogo sincronizado.",
                settings.pinnacle_bookmaker_name,
            )
            return

        window_start = utcnow()
        window_end = window_start + timedelta(hours=args.window_hours)
        candidate_fixtures = repository.get_candidate_fixtures(window_start, window_end, sorted(UPCOMING_STATUSES))
        filtered_candidates = [
            row
            for row in candidate_fixtures
            if row.get("league_id") in target_leagues
            and league_supports(coverage_map, row.get("league_id"), row.get("season"), "odds")
        ]

        if not filtered_candidates:
            LOGGER.info("No hay fixtures con odds en la ventana %sh.", args.window_hours)
            return

        fixture_ids = {row["id"] for row in filtered_candidates}
        dates = sorted({row["date"].split("T")[0] for row in filtered_candidates if row.get("date")})
        captured_at = utcnow()

        for date_value in dates:
            odds_rows = await api_client.fetch_paginated(
                "odds",
                {"date": date_value, "bookmaker": bookmaker["id"]},
            )
            scoped_rows = [row for row in odds_rows if (row.get("fixture") or {}).get("id") in fixture_ids]
            if scoped_rows:
                repository.store_odds_snapshots(
                    market_scope="prematch",
                    captured_at=captured_at,
                    bookmaker_id=bookmaker["id"],
                    bookmaker_name=bookmaker["name"],
                    odds_items=scoped_rows,
                )
                for row in scoped_rows:
                    fixture_id = (row.get("fixture") or {}).get("id")
                    if fixture_id:
                        repository.mark_fixture_hydration(fixture_id, hydrated_odds=True)

            await asyncio.sleep(0.5)

    LOGGER.info(
        "Snapshot de odds pre-match completado para %s (%s).",
        settings.pinnacle_bookmaker_name,
        captured_at.isoformat(),
    )


if __name__ == "__main__":
    asyncio.run(main())
