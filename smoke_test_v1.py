import argparse
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Callable

from pipeline_core import (
    FINAL_STATUSES,
    UPCOMING_STATUSES,
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_target_leagues,
    utcnow,
)

LOGGER = configure_logging("stuf.smoke-v1")


@dataclass(frozen=True)
class LeagueSmokeSummary:
    league_id: int
    season: int
    upcoming_count: int
    recent_count: int
    sample_fixture_id: int | None
    fixtures_ready: bool
    streaks_ready: bool
    comparison_ready: bool
    player_panel_ready: bool | None
    referee_panel_ready: bool | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test corto de STUF V1 usando datos reales en Supabase."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha base YYYY-MM-DD. Default: hoy UTC.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 39,61,78,135,140.")
    parser.add_argument(
        "--days-back",
        type=int,
        default=20,
        help="Dias hacia atras para revisar cobertura reciente.",
    )
    parser.add_argument(
        "--days-future",
        type=int,
        default=6,
        help="Dias futuros para revisar fixtures programados.",
    )
    parser.add_argument(
        "--require-players",
        action="store_true",
        help="Hace que Player Stats sea requisito critico en el smoke.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Devuelve exit code 1 tambien cuando hay advertencias.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=12,
        help="Cantidad maxima de ejemplos por grupo de issues.",
    )
    return parser.parse_args()


def resolve_target_leagues(args: argparse.Namespace) -> tuple[int, ...]:
    settings = load_settings()
    if args.leagues:
        return parse_target_leagues(args.leagues)
    return settings.target_leagues


def apply_league_filter(query, target_leagues: tuple[int, ...], column: str = "league_id"):
    if len(target_leagues) == 1:
        return query.eq(column, target_leagues[0])
    return query.in_(column, list(target_leagues))


def fetch_all_rows(
    repository: StufRepository,
    table: str,
    columns: str,
    builder: Callable[[Any], Any],
    operation: str,
    *,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    offset = 0
    rows: list[dict[str, Any]] = []
    while True:
        response = repository._execute(
            lambda offset=offset: builder(
                repository.supabase.table(table).select(columns)
            ).range(offset, offset + page_size - 1),
            f"{operation} offset={offset}",
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def format_context(context: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in context.items())


def build_issue(severity: str, code: str, message: str, **context: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "context": context,
    }


def log_issue_group(title: str, issues: list[dict[str, Any]], *, max_examples: int) -> None:
    if not issues:
        LOGGER.info("%s: OK", title)
        return

    LOGGER.warning("%s: %s incidencia(s)", title, len(issues))
    for issue in issues[:max_examples]:
        LOGGER.warning(
            "[%s] %s | %s",
            issue["code"],
            issue["message"],
            format_context(issue["context"]),
        )
    remaining = len(issues) - max_examples
    if remaining > 0:
        LOGGER.warning("%s: %s ejemplo(s) adicional(es) omitidos del log.", title, remaining)


def load_recent_finished_fixtures(
    repository: StufRepository,
    target_leagues: tuple[int, ...],
    season: int,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    return fetch_all_rows(
        repository,
        "fixtures",
        "id,league_id,season,date,status_short,home_team_id,away_team_id",
        lambda query: apply_league_filter(
            query.eq("season", season)
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", sorted(FINAL_STATUSES)),
            target_leagues,
        ),
        f"load smoke recent fixtures season={season}",
    )


def load_upcoming_fixtures(
    repository: StufRepository,
    target_leagues: tuple[int, ...],
    season: int,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    return fetch_all_rows(
        repository,
        "fixtures",
        "id,league_id,season,date,status_short,home_team_id,away_team_id,referee_id,referee_name_raw",
        lambda query: apply_league_filter(
            query.eq("season", season)
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", sorted(UPCOMING_STATUSES)),
            target_leagues,
        ),
        f"load smoke upcoming fixtures season={season}",
    )


def has_team_rows(repository: StufRepository, table: str, team_id: int, league_id: int, season: int) -> bool:
    response = repository._execute(
        lambda: repository.supabase.table(table)
        .select("team_id")
        .eq("team_id", team_id)
        .eq("league_id", league_id)
        .eq("season", season)
        .limit(1),
        f"smoke {table} team={team_id} league={league_id} season={season}",
    )
    return bool(response.data)


def has_referee_rows(repository: StufRepository, referee_id: int, league_id: int, season: int) -> bool:
    response = repository._execute(
        lambda: repository.supabase.table("referee_market_stats")
        .select("referee_id")
        .eq("referee_id", referee_id)
        .eq("league_id", league_id)
        .eq("season", season)
        .limit(1),
        f"smoke referee_market_stats referee={referee_id} league={league_id} season={season}",
    )
    return bool(response.data)


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args)
    target_date = datetime.fromisoformat(args.target_date).date() if args.target_date else utcnow().date()

    recent_start = datetime.combine(target_date - timedelta(days=max(1, args.days_back) - 1), time.min)
    recent_end = datetime.combine(target_date, time.max)
    future_start = datetime.combine(target_date, time.min)
    future_end = datetime.combine(target_date + timedelta(days=max(1, args.days_future) - 1), time.max)

    LOGGER.info(
        "Smoke V1. leagues=%s season=%s recent=%s..%s future=%s..%s require_players=%s",
        ",".join(str(item) for item in target_leagues),
        args.season,
        recent_start.isoformat(),
        recent_end.isoformat(),
        future_start.isoformat(),
        future_end.isoformat(),
        args.require_players,
    )

    recent_rows = load_recent_finished_fixtures(
        repository,
        target_leagues,
        args.season,
        recent_start,
        recent_end,
    )
    upcoming_rows = load_upcoming_fixtures(
        repository,
        target_leagues,
        args.season,
        future_start,
        future_end,
    )

    recent_by_league: dict[int, list[dict[str, Any]]] = {league_id: [] for league_id in target_leagues}
    upcoming_by_league: dict[int, list[dict[str, Any]]] = {league_id: [] for league_id in target_leagues}
    for row in recent_rows:
        league_id = row.get("league_id")
        if league_id in recent_by_league:
            recent_by_league[league_id].append(row)
    for row in upcoming_rows:
        league_id = row.get("league_id")
        if league_id in upcoming_by_league:
            upcoming_by_league[league_id].append(row)

    critical: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    league_summaries: list[LeagueSmokeSummary] = []

    for league_id in target_leagues:
        league_upcoming = sorted(upcoming_by_league.get(league_id, []), key=lambda row: row.get("date") or "")
        league_recent = sorted(recent_by_league.get(league_id, []), key=lambda row: row.get("date") or "", reverse=True)
        sample_fixture = league_upcoming[0] if league_upcoming else None
        fixtures_ready = False
        streaks_ready = False
        comparison_ready = False
        player_panel_ready: bool | None = None
        referee_panel_ready: bool | None = None

        if not league_recent:
            warnings.append(
                build_issue(
                    "warning",
                    "smoke_league_without_recent_fixtures",
                    "La liga no tiene fixtures recientes en la ventana del smoke.",
                    league_id=league_id,
                    season=args.season,
                )
            )

        if sample_fixture is None:
            warnings.append(
                build_issue(
                    "warning",
                    "smoke_league_without_upcoming_fixtures",
                    "La liga no tiene fixtures programados en la ventana del smoke.",
                    league_id=league_id,
                    season=args.season,
                )
            )
            league_summaries.append(
                LeagueSmokeSummary(
                    league_id=league_id,
                    season=args.season,
                    upcoming_count=len(league_upcoming),
                    recent_count=len(league_recent),
                    sample_fixture_id=None,
                    fixtures_ready=False,
                    streaks_ready=False,
                    comparison_ready=False,
                    player_panel_ready=None,
                    referee_panel_ready=None,
                )
            )
            continue

        fixture_id = sample_fixture.get("id")
        home_team_id = sample_fixture.get("home_team_id")
        away_team_id = sample_fixture.get("away_team_id")
        fixture_context = {
            "league_id": league_id,
            "season": args.season,
            "fixture_id": fixture_id,
        }

        if home_team_id is None or away_team_id is None:
            critical.append(
                build_issue(
                    "critical",
                    "smoke_sample_fixture_missing_teams",
                    "El fixture sample del smoke no tiene ambos team_id.",
                    **fixture_context,
                )
            )
            league_summaries.append(
                LeagueSmokeSummary(
                    league_id=league_id,
                    season=args.season,
                    upcoming_count=len(league_upcoming),
                    recent_count=len(league_recent),
                    sample_fixture_id=fixture_id,
                    fixtures_ready=False,
                    streaks_ready=False,
                    comparison_ready=False,
                    player_panel_ready=None,
                    referee_panel_ready=None,
                )
            )
            continue

        sample_team_ids = [int(home_team_id), int(away_team_id)]
        team_market_stats_ok = {
            team_id: has_team_rows(repository, "team_season_market_stats", team_id, league_id, args.season)
            for team_id in sample_team_ids
        }
        team_match_results_ok = {
            team_id: has_team_rows(repository, "team_match_market_results", team_id, league_id, args.season)
            for team_id in sample_team_ids
        }
        team_averages_ok = {
            team_id: has_team_rows(repository, "team_stat_averages", team_id, league_id, args.season)
            for team_id in sample_team_ids
        }
        player_panel_checks = {
            team_id: has_team_rows(repository, "player_season_stats", team_id, league_id, args.season)
            for team_id in sample_team_ids
        }

        fixtures_ready = all(team_market_stats_ok.values()) and all(team_match_results_ok.values())
        streaks_ready = all(team_market_stats_ok.values())
        comparison_ready = all(team_averages_ok.values()) and all(team_market_stats_ok.values())
        player_panel_ready = all(player_panel_checks.values())

        for team_id, ok in team_market_stats_ok.items():
            if not ok:
                critical.append(
                    build_issue(
                        "critical",
                        "smoke_missing_team_season_market_stats",
                        "Faltan team_season_market_stats para un equipo del fixture sample.",
                        **fixture_context,
                        team_id=team_id,
                    )
                )
        for team_id, ok in team_match_results_ok.items():
            if not ok:
                critical.append(
                    build_issue(
                        "critical",
                        "smoke_missing_team_match_market_results",
                        "Faltan team_match_market_results para un equipo del fixture sample.",
                        **fixture_context,
                        team_id=team_id,
                    )
                )
        for team_id, ok in team_averages_ok.items():
            if not ok:
                critical.append(
                    build_issue(
                        "critical",
                        "smoke_missing_team_stat_averages",
                        "Faltan team_stat_averages para un equipo del fixture sample.",
                        **fixture_context,
                        team_id=team_id,
                    )
                )
        for team_id, ok in player_panel_checks.items():
            if ok:
                continue
            issue = build_issue(
                "critical" if args.require_players else "warning",
                "smoke_missing_player_season_stats",
                (
                    "Faltan player_season_stats para un equipo del fixture sample."
                    if args.require_players
                    else "Player Stats no tiene player_season_stats para un equipo del fixture sample."
                ),
                **fixture_context,
                team_id=team_id,
            )
            if args.require_players:
                critical.append(issue)
            else:
                warnings.append(issue)

        referee_id = sample_fixture.get("referee_id")
        if referee_id is not None:
            referee_panel_ready = has_referee_rows(repository, int(referee_id), league_id, args.season)
            if not referee_panel_ready:
                warnings.append(
                    build_issue(
                        "warning",
                        "smoke_missing_referee_market_stats",
                        "El fixture sample ya tiene referee_id pero faltan referee_market_stats.",
                        **fixture_context,
                        referee_id=referee_id,
                    )
                )
        else:
            referee_panel_ready = None
            if sample_fixture.get("referee_name_raw"):
                warnings.append(
                    build_issue(
                        "warning",
                        "smoke_sample_fixture_referee_not_canonical",
                        "El fixture sample tiene referee_name_raw pero no referee_id.",
                        **fixture_context,
                        referee_name_raw=sample_fixture.get("referee_name_raw"),
                    )
                )

        league_summaries.append(
            LeagueSmokeSummary(
                league_id=league_id,
                season=args.season,
                upcoming_count=len(league_upcoming),
                recent_count=len(league_recent),
                sample_fixture_id=fixture_id,
                fixtures_ready=fixtures_ready,
                streaks_ready=streaks_ready,
                comparison_ready=comparison_ready,
                player_panel_ready=player_panel_ready,
                referee_panel_ready=referee_panel_ready,
            )
        )

    LOGGER.info(
        "Resumen smoke: recent_fixtures=%s upcoming_fixtures=%s leagues=%s",
        len(recent_rows),
        len(upcoming_rows),
        len(target_leagues),
    )
    for summary in league_summaries:
        LOGGER.info(
            (
                "league=%s season=%s upcoming=%s recent=%s sample_fixture=%s "
                "fixtures_ready=%s streaks_ready=%s comparison_ready=%s "
                "player_panel_ready=%s referee_panel_ready=%s"
            ),
            summary.league_id,
            summary.season,
            summary.upcoming_count,
            summary.recent_count,
            summary.sample_fixture_id,
            summary.fixtures_ready,
            summary.streaks_ready,
            summary.comparison_ready,
            summary.player_panel_ready,
            summary.referee_panel_ready,
        )

    log_issue_group("Errores criticos", critical, max_examples=args.max_examples)
    log_issue_group("Advertencias", warnings, max_examples=args.max_examples)

    if critical:
        LOGGER.error("SMOKE FALLIDO: %s error(es) critico(s).", len(critical))
        raise SystemExit(1)
    if warnings and args.strict_warnings:
        LOGGER.error("SMOKE FALLIDO: %s advertencia(s) con strict_warnings.", len(warnings))
        raise SystemExit(1)
    if warnings:
        LOGGER.info("Smoke completado sin errores criticos, con %s advertencia(s).", len(warnings))
        return

    LOGGER.info("Smoke completado sin errores criticos.")


if __name__ == "__main__":
    main()
