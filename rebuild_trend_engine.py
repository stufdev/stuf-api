from pipeline_core import (
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
)
from market_catalog import ensure_market_definitions
from trend_engine import rebuild_team_trend_rollups

LOGGER = configure_logging("stuf.trends")


def main() -> None:
    args = parse_cli_args("Recalculo local del Trend Engine P0.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    season = args.season or 2025
    target_leagues = resolve_target_leagues(args, settings)

    for league_id in target_leagues:
        team_ids = repository.get_team_ids_for_league_season(league_id, season)
        LOGGER.info(
            "Recalculando tendencias para liga=%s temporada=%s con %s equipo(s).",
            league_id,
            season,
            len(team_ids),
        )

        for team_id in team_ids:
            rebuild_team_trend_rollups(repository, team_id, league_id, season)

    LOGGER.info("Trend Engine P0 recalculado para temporada %s.", season)


if __name__ == "__main__":
    main()
