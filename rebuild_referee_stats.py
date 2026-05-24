from pipeline_core import (
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
)
from market_catalog import ensure_market_definitions
from referee_engine import rebuild_referee_stats_for_league


LOGGER = configure_logging("stuf.referees")


def main() -> None:
    args = parse_cli_args("Recalculo local de Referee Stats.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    ensure_market_definitions(repository)

    season = args.season or 2025
    target_leagues = resolve_target_leagues(args, settings, repository, season=args.season)

    for league_id in target_leagues:
        LOGGER.info("Recalculando referee stats para liga=%s temporada=%s.", league_id, season)
        rebuild_referee_stats_for_league(repository, league_id, season)

    LOGGER.info("Referee Stats recalculado para temporada %s.", season)


if __name__ == "__main__":
    main()
