import argparse
import math
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

LOGGER = configure_logging("stuf.data-quality")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida la calidad minima de datos para operar STUF V1."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha base YYYY-MM-DD. Default: hoy UTC.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 39,61,78,135,140.")
    parser.add_argument(
        "--days-back",
        type=int,
        default=20,
        help="Dias hacia atras para revisar fixtures finalizados recientes.",
    )
    parser.add_argument(
        "--days-future",
        type=int,
        default=6,
        help="Dias futuros a revisar para fixtures programados.",
    )
    parser.add_argument(
        "--require-players",
        action="store_true",
        help="Trata la ausencia de player_fixture_stats recientes como error critico en vez de advertencia.",
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
        help="Cantidad maxima de ejemplos por resumen.",
    )
    return parser.parse_args()


def resolve_target_leagues(args: argparse.Namespace) -> tuple[int, ...]:
    settings = load_settings()
    if args.leagues:
        return parse_target_leagues(args.leagues)
    return settings.target_leagues


def as_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


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
    parts = [f"{key}={value}" for key, value in context.items()]
    return ", ".join(parts)


def build_issue(severity: str, code: str, message: str, **context: Any) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "context": context,
    }


def validate_team_market_rollups(
    repository: StufRepository,
    target_leagues: tuple[int, ...],
    season: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows = fetch_all_rows(
        repository,
        "team_season_market_stats",
        (
            "league_id,team_id,market_key,scope,sample,hits,percentage,current_streak,longest_streak,"
            "last_5_sample,last_5_hits,last_5_percentage,last_10_sample,last_10_hits,last_10_percentage"
        ),
        lambda query: apply_league_filter(query.eq("season", season), target_leagues),
        f"load team season market stats season={season}",
    )
    critical: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not rows:
        critical.append(
            build_issue(
                "critical",
                "missing_team_market_rollups",
                "No se encontraron filas en team_season_market_stats para la temporada objetivo.",
                season=season,
                leagues=",".join(str(item) for item in target_leagues),
            )
        )
        return critical, warnings, 0

    for row in rows:
        sample = as_int(row.get("sample"))
        hits = as_int(row.get("hits"))
        percentage = as_float(row.get("percentage"))
        current_streak = as_int(row.get("current_streak"))
        longest_streak = as_int(row.get("longest_streak"))
        last_5_sample = as_int(row.get("last_5_sample"))
        last_5_hits = as_int(row.get("last_5_hits"))
        last_5_percentage = as_float(row.get("last_5_percentage"))
        last_10_sample = as_int(row.get("last_10_sample"))
        last_10_hits = as_int(row.get("last_10_hits"))
        last_10_percentage = as_float(row.get("last_10_percentage"))
        context = {
            "league_id": row.get("league_id"),
            "team_id": row.get("team_id"),
            "market_key": row.get("market_key"),
            "scope": row.get("scope"),
        }

        if hits > sample:
            critical.append(
                build_issue(
                    "critical",
                    "hits_gt_sample",
                    "team_season_market_stats tiene hits mayores que sample.",
                    **context,
                    hits=hits,
                    sample=sample,
                )
            )
        if current_streak > sample:
            critical.append(
                build_issue(
                    "critical",
                    "current_streak_gt_sample",
                    "current_streak supera sample.",
                    **context,
                    current_streak=current_streak,
                    sample=sample,
                )
            )
        if longest_streak > sample:
            critical.append(
                build_issue(
                    "critical",
                    "longest_streak_gt_sample",
                    "longest_streak supera sample.",
                    **context,
                    longest_streak=longest_streak,
                    sample=sample,
                )
            )
        if current_streak > longest_streak:
            critical.append(
                build_issue(
                    "critical",
                    "current_streak_gt_longest_streak",
                    "current_streak no puede ser mayor que longest_streak.",
                    **context,
                    current_streak=current_streak,
                    longest_streak=longest_streak,
                )
            )

        if not 0.0 <= percentage <= 100.0:
            critical.append(
                build_issue(
                    "critical",
                    "percentage_out_of_range",
                    "percentage esta fuera de 0-100.",
                    **context,
                    percentage=percentage,
                )
            )
        elif sample == 0 and not math.isclose(percentage, 0.0, abs_tol=0.01):
            critical.append(
                build_issue(
                    "critical",
                    "percentage_without_sample",
                    "percentage no es 0 pese a sample=0.",
                    **context,
                    percentage=percentage,
                    sample=sample,
                )
            )
        elif sample > 0:
            expected = hits / sample * 100.0
            if abs(expected - percentage) > 1.0:
                critical.append(
                    build_issue(
                        "critical",
                        "percentage_mismatch",
                        "percentage no coincide con hits/sample dentro de la tolerancia.",
                        **context,
                        hits=hits,
                        sample=sample,
                        percentage=round(percentage, 2),
                        expected=round(expected, 2),
                    )
                )

        if last_5_hits > last_5_sample or last_5_sample > min(sample, 5):
            critical.append(
                build_issue(
                    "critical",
                    "last_5_rollup_invalid",
                    "last_5_hits/last_5_sample no son consistentes.",
                    **context,
                    last_5_hits=last_5_hits,
                    last_5_sample=last_5_sample,
                    sample=sample,
                )
            )
        if last_10_hits > last_10_sample or last_10_sample > min(sample, 10):
            critical.append(
                build_issue(
                    "critical",
                    "last_10_rollup_invalid",
                    "last_10_hits/last_10_sample no son consistentes.",
                    **context,
                    last_10_hits=last_10_hits,
                    last_10_sample=last_10_sample,
                    sample=sample,
                )
            )

        if not 0.0 <= last_5_percentage <= 100.0:
            critical.append(
                build_issue(
                    "critical",
                    "last_5_percentage_out_of_range",
                    "last_5_percentage esta fuera de 0-100.",
                    **context,
                    last_5_percentage=last_5_percentage,
                )
            )
        if not 0.0 <= last_10_percentage <= 100.0:
            critical.append(
                build_issue(
                    "critical",
                    "last_10_percentage_out_of_range",
                    "last_10_percentage esta fuera de 0-100.",
                    **context,
                    last_10_percentage=last_10_percentage,
                )
            )

    return critical, warnings, len(rows)


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
        (
            "id,league_id,season,date,status_short,home_team_id,away_team_id,referee_id,referee_name_raw,"
            "hydrated_statistics,hydrated_events,hydrated_players"
        ),
        lambda query: apply_league_filter(
            query.eq("season", season)
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", sorted(FINAL_STATUSES)),
            target_leagues,
        ),
        f"load recent finished fixtures season={season}",
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
        f"load upcoming fixtures season={season}",
    )


def build_count_map(
    repository: StufRepository,
    table: str,
    fixture_ids: list[int],
    operation: str,
    *,
    ft_only: bool = False,
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for batch in chunked(fixture_ids, 150):
        offset = 0
        while True:
            response = repository._execute(
                lambda batch=batch, offset=offset: (
                    repository.supabase.table(table)
                    .select("fixture_id")
                    .in_("fixture_id", batch)
                    .eq("period", "FT")
                    .range(offset, offset + 999)
                    if ft_only
                    else repository.supabase.table(table)
                    .select("fixture_id")
                    .in_("fixture_id", batch)
                    .range(offset, offset + 999)
                ),
                f"{operation} batch={len(batch)} offset={offset}",
            )
            batch_rows = response.data or []
            for row in batch_rows:
                fixture_id = row.get("fixture_id")
                if fixture_id is None:
                    continue
                counts[fixture_id] = counts.get(fixture_id, 0) + 1
            if len(batch_rows) < 1000:
                break
            offset += 1000
    return counts


def validate_recent_finished_health(
    repository: StufRepository,
    rows: list[dict[str, Any]],
    *,
    require_players: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    critical: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not rows:
        warnings.append(
            build_issue(
                "warning",
                "no_recent_finished_fixtures",
                "No se encontraron fixtures finalizados en la ventana reciente.",
            )
        )
        return critical, warnings

    fixture_ids = [row["id"] for row in rows if row.get("id") is not None]
    fact_counts = build_count_map(
        repository,
        "team_fixture_facts",
        fixture_ids,
        "load team fixture facts counts",
    )
    stats_counts = build_count_map(
        repository,
        "fixture_statistics",
        fixture_ids,
        "load fixture statistics counts",
        ft_only=True,
    )
    player_counts = build_count_map(
        repository,
        "player_fixture_stats",
        fixture_ids,
        "load player fixture stats counts",
    )

    for row in rows:
        context = {
            "fixture_id": row.get("id"),
            "league_id": row.get("league_id"),
            "status_short": row.get("status_short"),
            "date": row.get("date"),
        }
        if row.get("home_team_id") is None or row.get("away_team_id") is None:
            critical.append(
                build_issue(
                    "critical",
                    "recent_fixture_missing_teams",
                    "Fixture finalizado reciente sin home_team_id o away_team_id.",
                    **context,
                )
            )
        if not row.get("hydrated_statistics"):
            critical.append(
                build_issue(
                    "critical",
                    "recent_fixture_missing_statistics_flag",
                    "Fixture finalizado reciente sin hydrated_statistics=true.",
                    **context,
                )
            )
        if not row.get("hydrated_events"):
            critical.append(
                build_issue(
                    "critical",
                    "recent_fixture_missing_events_flag",
                    "Fixture finalizado reciente sin hydrated_events=true.",
                    **context,
                )
            )
        if not row.get("hydrated_players"):
            issue = build_issue(
                "critical" if require_players else "warning",
                "recent_fixture_missing_players_flag",
                (
                    "Fixture finalizado reciente sin hydrated_players=true."
                    if require_players
                    else "Fixture finalizado reciente aun no confirma hydrated_players=true."
                ),
                **context,
            )
            if require_players:
                critical.append(issue)
            else:
                warnings.append(issue)
        if row.get("referee_name_raw") and row.get("referee_id") is None:
            warnings.append(
                build_issue(
                    "warning",
                    "recent_fixture_referee_not_canonical",
                    "Fixture finalizado reciente tiene referee_name_raw pero referee_id sigue nulo.",
                    **context,
                    referee_name_raw=row.get("referee_name_raw"),
                )
            )

        fixture_id = row.get("id")
        if fact_counts.get(fixture_id, 0) < 2:
            critical.append(
                build_issue(
                    "critical",
                    "recent_fixture_missing_team_facts",
                    "Fixture finalizado reciente no tiene 2 filas en team_fixture_facts.",
                    **context,
                    team_fixture_facts=fact_counts.get(fixture_id, 0),
                )
            )
        if stats_counts.get(fixture_id, 0) < 2:
            critical.append(
                build_issue(
                    "critical",
                    "recent_fixture_missing_ft_stats",
                    "Fixture finalizado reciente no tiene 2 filas FT en fixture_statistics.",
                    **context,
                    fixture_statistics_ft=stats_counts.get(fixture_id, 0),
                )
            )
        if player_counts.get(fixture_id, 0) == 0:
            issue = build_issue(
                "critical" if require_players else "warning",
                "recent_fixture_missing_player_stats",
                (
                    "Fixture finalizado reciente no tiene filas en player_fixture_stats."
                    if require_players
                    else "Fixture finalizado reciente aun no tiene filas en player_fixture_stats."
                ),
                **context,
            )
            if require_players:
                critical.append(issue)
            else:
                warnings.append(issue)

    return critical, warnings


def validate_upcoming_fixture_shells(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    critical: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not rows:
        warnings.append(
            build_issue(
                "warning",
                "no_upcoming_fixtures",
                "No se encontraron fixtures programados en la ventana futura.",
            )
        )
        return critical, warnings

    for row in rows:
        context = {
            "fixture_id": row.get("id"),
            "league_id": row.get("league_id"),
            "status_short": row.get("status_short"),
            "date": row.get("date"),
        }
        if row.get("home_team_id") is None or row.get("away_team_id") is None:
            critical.append(
                build_issue(
                    "critical",
                    "upcoming_fixture_missing_teams",
                    "Fixture programado sin home_team_id o away_team_id.",
                    **context,
                )
            )
        if row.get("referee_name_raw") and row.get("referee_id") is None:
            warnings.append(
                build_issue(
                    "warning",
                    "upcoming_fixture_referee_not_canonical",
                    "Fixture programado tiene referee_name_raw pero referee_id sigue nulo.",
                    **context,
                    referee_name_raw=row.get("referee_name_raw"),
                )
            )

    return critical, warnings


def log_issue_group(
    title: str,
    issues: list[dict[str, Any]],
    *,
    max_examples: int,
) -> None:
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
        "Validando calidad de datos V1. leagues=%s season=%s recent=%s..%s future=%s..%s require_players=%s",
        ",".join(str(item) for item in target_leagues),
        args.season,
        recent_start.isoformat(),
        recent_end.isoformat(),
        future_start.isoformat(),
        future_end.isoformat(),
        args.require_players,
    )

    critical_rollups, warning_rollups, rollup_rows = validate_team_market_rollups(
        repository,
        target_leagues,
        args.season,
    )
    recent_rows = load_recent_finished_fixtures(
        repository,
        target_leagues,
        args.season,
        recent_start,
        recent_end,
    )
    critical_recent, warning_recent = validate_recent_finished_health(
        repository,
        recent_rows,
        require_players=args.require_players,
    )
    upcoming_rows = load_upcoming_fixtures(
        repository,
        target_leagues,
        args.season,
        future_start,
        future_end,
    )
    critical_upcoming, warning_upcoming = validate_upcoming_fixture_shells(upcoming_rows)

    critical = [*critical_rollups, *critical_recent, *critical_upcoming]
    warnings = [*warning_rollups, *warning_recent, *warning_upcoming]

    LOGGER.info(
        "Resumen de universo validado: rollups=%s recent_fixtures=%s upcoming_fixtures=%s",
        rollup_rows,
        len(recent_rows),
        len(upcoming_rows),
    )
    log_issue_group("Errores criticos", critical, max_examples=args.max_examples)
    log_issue_group("Advertencias", warnings, max_examples=args.max_examples)

    if critical:
        LOGGER.error("VALIDACION FALLIDA: %s error(es) critico(s).", len(critical))
        raise SystemExit(1)
    if warnings and args.strict_warnings:
        LOGGER.error("VALIDACION FALLIDA: %s advertencia(s) con strict_warnings.", len(warnings))
        raise SystemExit(1)
    if warnings:
        LOGGER.info("Validacion completada sin errores criticos, con %s advertencia(s).", len(warnings))
        return

    LOGGER.info("Validacion completada sin errores criticos.")


if __name__ == "__main__":
    main()
