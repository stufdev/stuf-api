from pipeline_core import (
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
)
from stat_average_engine import rebuild_team_stat_averages

LOGGER = configure_logging("stuf.stat-averages")


def main() -> None:
    args = parse_cli_args("Recalculo local de team stat averages.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    season = args.season or 2025
    target_leagues = resolve_target_leagues(args, settings)

    for league_id in target_leagues:
        team_ids = repository.get_team_ids_for_league_season(league_id, season)
        LOGGER.info(
            "Recalculando promedios para liga=%s temporada=%s con %s equipo(s).",
            league_id,
            season,
            len(team_ids),
        )

        for team_id in team_ids:
            rebuild_team_stat_averages(repository, team_id, league_id, season)

    LOGGER.info("Team stat averages recalculados para temporada %s.", season)


if __name__ == "__main__":
    main()
