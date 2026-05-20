import argparse
import asyncio
from datetime import datetime, timedelta

from fetch_historical_limited import hydrate_fixture_details
from market_catalog import ensure_market_definitions
from pipeline_core import (
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    league_supports,
    load_settings,
    parse_target_leagues,
    sync_reference_catalogs,
    utcnow,
)

LOGGER = configure_logging("stuf.recent-window")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rehidrata solo fixtures finalizados dentro de una ventana reciente."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha final YYYY-MM-DD de la ventana. Default: hoy UTC.")
    parser.add_argument("--days-back", type=int, default=20, help="Cantidad de dias hacia atras, incluyendo la fecha final.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 140 o 39,61,78,135,140.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    parser.add_argument("--skip-players", action="store_true", help="No llamar /fixtures/players en esta corrida.")
    parser.add_argument("--skip-predictions", action="store_true", help="No llamar /predictions en esta corrida.")
    parser.add_argument(
        "--skip-known",
        action="store_true",
        help="Omite fixtures finales que ya esten completamente hidratados en Supabase.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    target_leagues = parse_target_leagues(args.leagues) if args.leagues else settings.target_leagues
    season = args.season
    include_players = not args.skip_players
    include_predictions = not args.skip_predictions

    end_date = datetime.fromisoformat(args.target_date).date() if args.target_date else utcnow().date()
    days_back = max(1, args.days_back)
    start_date = end_date - timedelta(days=days_back - 1)

    LOGGER.info(
        "Ventana reciente: leagues=%s season=%s from=%s to=%s players=%s predictions=%s skip_known=%s delay=%ss",
        ",".join(str(league_id) for league_id in target_leagues),
        season,
        start_date.isoformat(),
        end_date.isoformat(),
        include_players,
        include_predictions,
        args.skip_known,
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

        total_selected = 0
        total_processed = 0

        for league_id in target_leagues:
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

            response_rows = (fixtures_payload or {}).get("response", [])
            selected = sorted(
                response_rows,
                key=lambda item: (item.get("fixture") or {}).get("timestamp") or 0,
                reverse=False,
            )
            total_selected += len(selected)

            skip_map: dict[int, bool] = {}
            if args.skip_known:
                fixture_ids = [
                    (fixture.get("fixture") or {}).get("id")
                    for fixture in selected
                    if (fixture.get("fixture") or {}).get("id")
                ]
                require_events = league_supports(coverage_map, league_id, season, "fixtures_events")
                skip_map = repository.get_finished_fixture_skip_map(
                    fixture_ids,
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
                    skip_known=bool(skip_map.get(fixture_id)) if args.skip_known else False,
                    include_players=include_players,
                    include_predictions=include_predictions,
                    refresh_derived=False,
                )
                total_processed += 1
                await asyncio.sleep(0.25)

        LOGGER.info(
            "Ventana reciente completada. Fixtures encontrados=%s procesados=%s",
            total_selected,
            total_processed,
        )


if __name__ == "__main__":
    asyncio.run(main())
