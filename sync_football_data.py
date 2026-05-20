import asyncio
from datetime import timedelta

from pipeline_core import (
    FINAL_STATUSES,
    NON_PLAYED_STATUSES,
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    is_final_status,
    league_supports,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
    should_skip_finished_fanout,
    supports_first_half_statistics,
    sync_reference_catalogs,
    utcnow,
)
from market_catalog import ensure_market_definitions
from player_season_engine import refresh_player_season_stats_for_fixture
from referee_engine import refresh_referee_stats_for_fixture
from stat_average_engine import refresh_stat_averages_for_fixture
from trend_engine import refresh_trends_for_fixture

LOGGER = configure_logging("stuf.sync")


async def main() -> None:
    args = parse_cli_args("Carril B - cierre de caja nocturno para API-Football.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    target_date = args.target_date or (utcnow() - timedelta(days=1)).date().isoformat()
    target_leagues = resolve_target_leagues(args, settings)
    include_players = not args.skip_players
    include_predictions = not args.skip_predictions
    LOGGER.info(
        "Sincronizando cierre nocturno para %s leagues=%s players=%s predictions=%s request_delay=%ss",
        target_date,
        ",".join(str(league_id) for league_id in target_leagues),
        include_players,
        include_predictions,
        args.request_delay,
    )

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = await sync_reference_catalogs(
            api_client,
            repository,
            settings,
            target_leagues=target_leagues,
            include_odds_catalogs=False,
        )

        payload = await api_client.fetch("fixtures", {"date": target_date})
        if not payload:
            LOGGER.error("No fue posible obtener fixtures para %s", target_date)
            return

        fixtures = []
        for fixture in payload.get("response", []):
            league = fixture.get("league") or {}
            if league.get("id") in target_leagues:
                fixtures.append(fixture)

        LOGGER.info("Fixtures objetivo detectados: %s", len(fixtures))

        for fixture in fixtures:
            fixture_info = fixture.get("fixture") or {}
            fixture_id = fixture_info.get("id")
            status = (fixture_info.get("status") or {}).get("short")
            league = fixture.get("league") or {}
            league_id = league.get("id")
            season = league.get("season")

            if not fixture_id:
                continue

            repository.upsert_fixture_shell(fixture)

            if status in NON_PLAYED_STATUSES:
                LOGGER.info("Fixture %s actualizado como %s sin fan-out adicional.", fixture_id, status)
                continue

            if should_skip_finished_fanout(
                repository,
                fixture_id,
                status,
                require_events=league_supports(coverage_map, league_id, season, "fixtures_events"),
                require_players=include_players,
                require_prediction=include_predictions,
            ):
                LOGGER.info("Fixture %s ya estaba cerrado e hidratado. Se omiten sub-endpoints.", fixture_id)
                continue

            if not is_final_status(status):
                LOGGER.info("Fixture %s sigue en estado %s. Solo se sincronizo la carcasa.", fixture_id, status)
                continue

            if league_supports(coverage_map, league_id, season, "fixtures_statistics"):
                statistics_payload = await api_client.fetch("fixtures/statistics", {"fixture": fixture_id})
                first_half_statistics_payload = None
                if supports_first_half_statistics(season):
                    first_half_statistics_payload = await api_client.fetch(
                        "fixtures/statistics",
                        {"fixture": fixture_id, "half": "true"},
                    )
                if statistics_payload is not None:
                    repository.mark_fixture_hydration(fixture_id, hydrated_statistics=True)
                stats = (statistics_payload or {}).get("response", [])
                first_half_stats = (first_half_statistics_payload or {}).get("response", [])
                if stats:
                    repository.replace_fixture_statistics(fixture_id, stats, first_half_stats)

            if league_supports(coverage_map, league_id, season, "fixtures_events"):
                events_payload = await api_client.fetch("fixtures/events", {"fixture": fixture_id})
                if events_payload is not None:
                    repository.mark_fixture_hydration(fixture_id, hydrated_events=True)
                events = (events_payload or {}).get("response", [])
                if events:
                    repository.replace_fixture_events(fixture_id, events)

            if include_players and league_supports(coverage_map, league_id, season, "fixtures_players_statistics"):
                players_payload = await api_client.fetch("fixtures/players", {"fixture": fixture_id})
                player_groups = (players_payload or {}).get("response", [])
                if player_groups:
                    persisted_rows = repository.replace_player_stats(fixture_id, player_groups)
                    repository.mark_fixture_hydration(fixture_id, hydrated_players=persisted_rows > 0)

            if include_predictions and league_supports(coverage_map, league_id, season, "predictions"):
                prediction_payload = await api_client.fetch("predictions", {"fixture": fixture_id})
                if prediction_payload is not None:
                    repository.mark_fixture_hydration(fixture_id, hydrated_predictions=True)
                prediction_rows = (prediction_payload or {}).get("response", [])
                if prediction_rows:
                    repository.upsert_prediction(fixture_id, prediction_rows[0])

            if repository.replace_team_fixture_facts(fixture_id):
                refresh_trends_for_fixture(repository, fixture_id)
                refresh_stat_averages_for_fixture(repository, fixture_id)
                refresh_referee_stats_for_fixture(repository, fixture_id)

            if include_players:
                refresh_player_season_stats_for_fixture(repository, fixture_id)

            await asyncio.sleep(0.4)

    LOGGER.info(
        "Cierre nocturno finalizado para %s. Estados finales aceptados: %s",
        target_date,
        ",".join(sorted(FINAL_STATUSES)),
    )


if __name__ == "__main__":
    asyncio.run(main())
