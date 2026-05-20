import asyncio
from datetime import datetime, timedelta

from pipeline_core import (
    UPCOMING_STATUSES,
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    league_supports,
    load_settings,
    parse_cli_args,
    prediction_needs_refresh,
    resolve_target_leagues,
    sync_reference_catalogs,
    utcnow,
)

LOGGER = configure_logging("stuf.planning")


async def hydrate_fixture_if_missing_referee(
    api_client: ApiFootballClient,
    fixture: dict,
) -> dict:
    fixture_info = fixture.get("fixture") or {}
    fixture_id = fixture_info.get("id")
    referee_name = fixture_info.get("referee")
    if fixture_id is None or referee_name:
        return fixture

    detail_payload = await api_client.fetch("fixtures", {"id": fixture_id})
    detail_rows = (detail_payload or {}).get("response", [])
    if not detail_rows:
        return fixture

    detailed_fixture = detail_rows[0]
    detailed_info = detailed_fixture.get("fixture") or {}
    if detailed_info.get("referee"):
        LOGGER.info(
            "Fixture %s recupero referee via detalle puntual: %s",
            fixture_id,
            detailed_info.get("referee"),
        )
        return detailed_fixture

    return fixture


async def main() -> None:
    args = parse_cli_args("Carril C - planning y predicciones prepartido.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args, settings)
    include_predictions = not args.skip_predictions
    base_date = args.target_date or utcnow().date().isoformat()
    base_date_value = datetime.fromisoformat(base_date).date()
    target_dates = [
        (base_date_value + timedelta(days=day_offset)).isoformat()
        for day_offset in range(args.days)
    ]

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        coverage_map = await sync_reference_catalogs(
            api_client,
            repository,
            settings,
            target_leagues=target_leagues,
            include_odds_catalogs=False,
        )

        LOGGER.info(
            "Planificando calendario de %s dia(s), leagues=%s, predictions=%s, request_delay=%ss.",
            args.days,
            ",".join(str(league_id) for league_id in target_leagues),
            include_predictions,
            args.request_delay,
        )

        single_league_mode = len(target_leagues) == 1
        single_league_id = target_leagues[0] if single_league_mode else None
        fixtures_by_date: dict[str, list[dict]] = {}

        if single_league_mode and single_league_id is not None:
            range_params = {
                "league": single_league_id,
                "from": target_dates[0],
                "to": target_dates[-1],
                "status": "NS-TBD",
            }
            if args.season:
                range_params["season"] = args.season

            fixtures_payload = await api_client.fetch("fixtures", range_params)
            if fixtures_payload is None:
                LOGGER.warning(
                    "No se pudieron obtener fixtures para league=%s en rango %s..%s",
                    single_league_id,
                    target_dates[0],
                    target_dates[-1],
                )
                fixtures_by_date = {target_date: [] for target_date in target_dates}
            else:
                for fixture in (fixtures_payload or {}).get("response", []):
                    fixture_info = fixture.get("fixture") or {}
                    fixture_date = (fixture_info.get("date") or "")[:10]
                    if fixture_date in target_dates:
                        fixtures_by_date.setdefault(fixture_date, []).append(fixture)

        for target_date in target_dates:
            if single_league_mode and single_league_id is not None:
                fixtures = fixtures_by_date.get(target_date, [])
            else:
                params = {"date": target_date, "status": "NS-TBD"}
                if args.season:
                    params["season"] = args.season
                fixtures_payload = await api_client.fetch("fixtures", params)
                if fixtures_payload is None:
                    LOGGER.warning("No se pudieron obtener fixtures para %s", target_date)
                    continue

                fixtures = []
                for fixture in (fixtures_payload or {}).get("response", []):
                    league = fixture.get("league") or {}
                    if league.get("id") in target_leagues:
                        fixtures.append(fixture)

            LOGGER.info("Fecha %s: %s fixture(s) objetivo(s).", target_date, len(fixtures))

            for fixture in fixtures:
                fixture_info = fixture.get("fixture") or {}
                fixture_id = fixture_info.get("id")
                fixture_date = fixture_info.get("date")
                league = fixture.get("league") or {}
                league_id = league.get("id")
                season = league.get("season")

                if not fixture_id:
                    continue

                fixture = await hydrate_fixture_if_missing_referee(api_client, fixture)
                fixture_info = fixture.get("fixture") or {}
                fixture_date = fixture_info.get("date")
                league = fixture.get("league") or {}
                league_id = league.get("id")
                season = league.get("season")

                repository.upsert_fixture_shell(fixture)

                if not include_predictions or not league_supports(coverage_map, league_id, season, "predictions"):
                    continue

                if not prediction_needs_refresh(repository, fixture_id, fixture_date):
                    continue

                prediction_payload = await api_client.fetch("predictions", {"fixture": fixture_id})
                if prediction_payload is not None:
                    repository.mark_fixture_hydration(fixture_id, hydrated_predictions=True)
                prediction_rows = (prediction_payload or {}).get("response", [])
                if prediction_rows:
                    repository.upsert_prediction(fixture_id, prediction_rows[0])

                await asyncio.sleep(0.35)

            await asyncio.sleep(0.8)

    LOGGER.info(
        "Planning completado. Estados programados considerados: %s",
        ",".join(sorted(UPCOMING_STATUSES)),
    )


if __name__ == "__main__":
    asyncio.run(main())
