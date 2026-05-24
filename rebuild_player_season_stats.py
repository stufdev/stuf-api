from pipeline_core import (
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
)
from player_season_engine import rebuild_player_season_stats

LOGGER = configure_logging("stuf.player-season-stats")


def main() -> None:
    args = parse_cli_args("Recalculo local de player season stats.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    season = args.season or 2025
    target_leagues = resolve_target_leagues(args, settings, repository, season=args.season)

    for league_id in target_leagues:
        team_ids = repository.get_team_ids_for_league_season(league_id, season)
        LOGGER.info(
            "Recalculando player season stats para liga=%s temporada=%s con %s equipo(s).",
            league_id,
            season,
            len(team_ids),
        )

        for team_id in team_ids:
            rebuild_player_season_stats(repository, team_id, league_id, season)

    LOGGER.info("Player season stats recalculados para temporada %s.", season)


if __name__ == "__main__":
    main()
