import argparse
import asyncio
from datetime import datetime, timedelta

from pipeline_core import (
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    is_final_status,
    league_supports,
    load_settings,
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

LOGGER = configure_logging("stuf.historical")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Carril A seguro - ingesta historica limitada o ventana reciente."
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        help="Fecha final YYYY-MM-DD cuando uses --days-back. Default: hoy UTC.",
    )
    parser.add_argument("--season", type=int, help="Temporada YYYY para ingesta historica.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Limite por liga. En modo default usa 12 si no se especifica. En modo ventana es opcional.",
    )
    parser.add_argument(
        "--days-back",
        type=int,
        help="Si se especifica, procesa todos los fixtures finalizados en los ultimos N dias hasta --date/hoy UTC.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rehidrata fixtures aunque ya existan cerrados. Usar para corregir datos derivados tras cambios de parser.",
    )
    parser.add_argument("--leagues", help="Lista CSV de league_id para esta corrida, ej: 140 o 39,140.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football. Usar 1.0 para bootstrap seguro.",
    )
    parser.add_argument("--skip-players", action="store_true", help="No llamar /fixtures/players en esta corrida.")
    parser.add_argument("--skip-predictions", action="store_true", help="No llamar /predictions en esta corrida.")
    return parser.parse_args()


async def hydrate_fixture_details(
    api_client: ApiFootballClient,
    repository: StufRepository,
    coverage_map,
    fixture: dict,
    skip_known: bool | None = None,
    include_players: bool = True,
    include_predictions: bool = True,
    refresh_derived: bool = True,
) -> None:
    fixture_info = fixture.get("fixture") or {}
    fixture_id = fixture_info.get("id")
    status = (fixture_info.get("status") or {}).get("short")
    league = fixture.get("league") or {}
    league_id = league.get("id")
    season = league.get("season")

    if not fixture_id or not is_final_status(status):
        return

    should_skip = (
        skip_known
        if skip_known is not None
        else should_skip_finished_fanout(
            repository,
            fixture_id,
            status,
            require_events=league_supports(coverage_map, league_id, season, "fixtures_events"),
            require_players=include_players,
            require_prediction=include_predictions,
        )
    )
    if should_skip:
        LOGGER.info("Fixture historico %s ya existe cerrado. Se omite fan-out.", fixture_id)
        return

    repository.upsert_fixture_shell(fixture)

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
        statistics_rows = (statistics_payload or {}).get("response", [])
        first_half_statistics_rows = (first_half_statistics_payload or {}).get("response", [])
        if statistics_rows:
            repository.replace_fixture_statistics(fixture_id, statistics_rows, first_half_statistics_rows)

    if league_supports(coverage_map, league_id, season, "fixtures_events"):
        events_payload = await api_client.fetch("fixtures/events", {"fixture": fixture_id})
        if events_payload is not None:
            repository.mark_fixture_hydration(fixture_id, hydrated_events=True)
        event_rows = (events_payload or {}).get("response", [])
        if event_rows:
            repository.replace_fixture_events(fixture_id, event_rows)

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

    replaced_facts = repository.replace_team_fixture_facts(fixture_id)

    if refresh_derived and replaced_facts:
        refresh_trends_for_fixture(repository, fixture_id)
        refresh_stat_averages_for_fixture(repository, fixture_id)
        refresh_referee_stats_for_fixture(repository, fixture_id)

    if refresh_derived and include_players:
        refresh_player_season_stats_for_fixture(repository, fixture_id)


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    season = args.season or 2025
    target_leagues = resolve_target_leagues(args, settings, repository, season=season)
    include_players = not args.skip_players
    include_predictions = not args.skip_predictions
    existing_coverage_map = repository.load_coverage_map()
    days_back = max(1, args.days_back) if args.days_back else None
    limit = args.limit
    pending_leagues: list[int] = list(target_leagues)
    end_date = None
    start_date = None

    if days_back is not None:
        end_date = datetime.fromisoformat(args.target_date).date() if args.target_date else utcnow().date()
        start_date = end_date - timedelta(days=days_back - 1)
        LOGGER.info(
            "Modo historico por ventana: leagues=%s season=%s from=%s to=%s limit=%s players=%s predictions=%s request_delay=%ss",
            ",".join(str(league_id) for league_id in target_leagues),
            season,
            start_date.isoformat(),
            end_date.isoformat(),
            limit if limit is not None else "ALL",
            include_players,
            include_predictions,
            args.request_delay,
        )
    else:
        limit = limit or 12
        pending_leagues = []

        for league_id in target_leagues:
            if args.force:
                pending_leagues.append(league_id)
                continue

            coverage_row = existing_coverage_map.get((league_id, season))
            if coverage_row is None:
                pending_leagues.append(league_id)
                continue

            require_events = bool(coverage_row.get("fixtures_events"))
            is_satisfied = repository.historical_backfill_satisfied(
                league_id,
                season,
                limit,
                require_events=require_events,
                require_players=include_players,
                require_prediction=include_predictions,
            )
            if is_satisfied:
                LOGGER.info(
                    "Liga %s temporada %s ya cubre los ultimos %s fixtures para este modo. Se omite sin tocar API.",
                    league_id,
                    season,
                    limit,
                )
                continue

            pending_leagues.append(league_id)

        LOGGER.info(
            "Modo historico por limite: leagues=%s season=%s limit=%s players=%s predictions=%s request_delay=%ss",
            ",".join(str(league_id) for league_id in target_leagues),
            season,
            limit,
            include_players,
            include_predictions,
            args.request_delay,
        )

        if not pending_leagues:
            LOGGER.info("No hay ligas pendientes para backfill historico.")
            return

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = await sync_reference_catalogs(
            api_client,
            repository,
            settings,
            target_leagues=pending_leagues,
            include_odds_catalogs=False,
        )

        for league_id in pending_leagues:
            if start_date is not None and end_date is not None:
                LOGGER.info(
                    "Liga %s temporada %s: descargando finales recientes en %s..%s",
                    league_id,
                    season,
                    start_date.isoformat(),
                    end_date.isoformat(),
                )
                fixtures_payload = await api_client.fetch(
                    "fixtures",
                    {
                        "league": league_id,
                        "season": season,
                        "from": start_date.isoformat(),
                        "to": end_date.isoformat(),
                        "status": "FT-AET-PEN",
                    },
                )
            else:
                LOGGER.info("Liga %s temporada %s: descargando historial seguro (limit=%s)", league_id, season, limit)
                fixtures_payload = await api_client.fetch(
                    "fixtures",
                    {"league": league_id, "season": season, "status": "FT-AET-PEN"},
                )
            response_rows = (fixtures_payload or {}).get("response", [])
            if start_date is not None and end_date is not None:
                sorted_rows = sorted(
                    response_rows,
                    key=lambda item: (item.get("fixture") or {}).get("timestamp") or 0,
                    reverse=False,
                )
                selected = sorted_rows[-limit:] if limit is not None and limit > 0 else sorted_rows
            else:
                sorted_rows = sorted(
                    response_rows,
                    key=lambda item: (item.get("fixture") or {}).get("timestamp") or 0,
                    reverse=True,
                )
                selected = sorted_rows[:limit]
            require_events = league_supports(coverage_map, league_id, season, "fixtures_events")
            skip_map = repository.get_finished_fixture_skip_map(
                [
                    (fixture.get("fixture") or {}).get("id")
                    for fixture in selected
                    if (fixture.get("fixture") or {}).get("id")
                ],
                require_events=require_events,
                require_players=include_players,
                require_prediction=include_predictions,
            )

            for fixture in selected:
                fixture_id = (fixture.get("fixture") or {}).get("id")
                await hydrate_fixture_details(
                    api_client,
                    repository,
                    coverage_map,
                    fixture,
                    skip_known=False if args.force else bool(skip_map.get(fixture_id)),
                    include_players=include_players,
                    include_predictions=include_predictions,
                    refresh_derived=False,
                )
                await asyncio.sleep(0.25)

    LOGGER.info("Ingesta historica finalizada.")


if __name__ == "__main__":
    asyncio.run(main())
