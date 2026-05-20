import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pipeline_core import configure_logging

LOGGER = configure_logging("stuf.v1-refresh")
SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class StepResult:
    name: str
    script_name: str
    elapsed_seconds: float
    returncode: int

    @property
    def status(self) -> str:
        return "ok" if self.returncode == 0 else f"failed({self.returncode})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orquesta el refresh minimo operable de STUF V1."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha base YYYY-MM-DD. Default: hoy UTC.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 39,61,78,135,140.")
    parser.add_argument(
        "--recent-days-back",
        type=int,
        default=20,
        help="Dias hacia atras para refrescar fixtures finalizados.",
    )
    parser.add_argument(
        "--upcoming-days",
        type=int,
        default=6,
        help="Cantidad de dias futuros a planificar (hoy incluido).",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    parser.add_argument(
        "--skip-players",
        action="store_true",
        help="No hidrata /fixtures/players ni exige player stats en validacion/smoke.",
    )
    parser.add_argument(
        "--require-players",
        action="store_true",
        help="Hace que validacion y smoke traten player stats recientes como requisito critico.",
    )
    parser.add_argument(
        "--include-predictions",
        action="store_true",
        help="Incluye predictions. Por defecto se omiten para ahorrar cuota en V1.",
    )
    parser.add_argument(
        "--skip-known",
        action="store_true",
        help="Omite fixtures recientes ya completamente hidratados.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="No ejecuta validate_data_quality.py al final.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="No ejecuta smoke_test_v1.py al final.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Hace fallar la corrida si validacion o smoke devuelven advertencias.",
    )
    return parser.parse_args()


def append_common_flags(args: argparse.Namespace, *, include_date: bool = False) -> list[str]:
    flags: list[str] = []
    if args.leagues:
        flags.extend(["--leagues", args.leagues])
    if args.season:
        flags.extend(["--season", str(args.season)])
    if include_date and args.target_date:
        flags.extend(["--date", args.target_date])
    return flags


def run_step(name: str, script_name: str, extra_args: list[str]) -> StepResult:
    command = [sys.executable, str(SCRIPT_DIR / script_name), *extra_args]
    LOGGER.info("Inicio %s", name)
    LOGGER.info("Comando: %s", subprocess.list2cmdline(command))
    started_at = time.perf_counter()
    completed = subprocess.run(command, check=False)
    elapsed = time.perf_counter() - started_at
    if completed.returncode != 0:
        LOGGER.error("%s fallo con exit_code=%s en %.1fs", name, completed.returncode, elapsed)
    else:
        LOGGER.info("%s completado en %.1fs", name, elapsed)
    return StepResult(
        name=name,
        script_name=script_name,
        elapsed_seconds=elapsed,
        returncode=completed.returncode,
    )


def log_run_summary(results: list[StepResult], total_elapsed: float) -> None:
    ok_count = sum(1 for item in results if item.returncode == 0)
    LOGGER.info("Resumen final V1 refresh:")
    for item in results:
        LOGGER.info(
            "step=%s script=%s status=%s duration=%.1fs",
            item.name,
            item.script_name,
            item.status,
            item.elapsed_seconds,
        )
    LOGGER.info(
        "Total corrida: steps=%s ok=%s failed=%s duration=%.1fs",
        len(results),
        ok_count,
        len(results) - ok_count,
        total_elapsed,
    )


def main() -> None:
    args = parse_args()
    started_at = time.perf_counter()
    results: list[StepResult] = []

    prediction_flag = [] if args.include_predictions else ["--skip-predictions"]
    player_flag = ["--skip-players"] if args.skip_players else []
    skip_known_flag = ["--skip-known"] if args.skip_known else []

    recent_args = [
        *append_common_flags(args, include_date=True),
        "--days-back",
        str(args.recent_days_back),
        "--request-delay",
        str(args.request_delay),
        *player_flag,
        *prediction_flag,
        *skip_known_flag,
    ]
    upcoming_args = [
        *append_common_flags(args, include_date=True),
        "--days",
        str(args.upcoming_days),
        "--request-delay",
        str(args.request_delay),
        *prediction_flag,
    ]
    rebuild_args = append_common_flags(args)
    validate_args = [
        *append_common_flags(args, include_date=True),
        "--days-back",
        str(args.recent_days_back),
        "--days-future",
        str(args.upcoming_days),
        *(["--require-players"] if args.require_players and not args.skip_players else []),
        *(["--strict-warnings"] if args.strict_warnings else []),
    ]
    smoke_args = [
        *append_common_flags(args, include_date=True),
        "--days-back",
        str(args.recent_days_back),
        "--days-future",
        str(args.upcoming_days),
        *(["--require-players"] if args.require_players and not args.skip_players else []),
        *(["--strict-warnings"] if args.strict_warnings else []),
    ]

    step_definitions: list[tuple[str, str, list[str]]] = [
        ("recent-window-refresh", "fetch_recent_window.py", recent_args),
        ("upcoming-fixtures-refresh", "fetch_upcoming_fixtures.py", upcoming_args),
        ("rebuild-stat-averages", "rebuild_stat_averages.py", rebuild_args),
        ("rebuild-trend-engine", "rebuild_trend_engine.py", rebuild_args),
        ("rebuild-player-season-stats", "rebuild_player_season_stats.py", rebuild_args),
        ("rebuild-referee-stats", "rebuild_referee_stats.py", rebuild_args),
    ]

    if not args.skip_validate:
        step_definitions.append(("validate-data-quality", "validate_data_quality.py", validate_args))
    if not args.skip_smoke:
        step_definitions.append(("smoke-test-v1", "smoke_test_v1.py", smoke_args))

    for name, script_name, extra_args in step_definitions:
        result = run_step(name, script_name, extra_args)
        results.append(result)
        if result.returncode != 0:
            log_run_summary(results, time.perf_counter() - started_at)
            raise SystemExit(result.returncode)

    log_run_summary(results, time.perf_counter() - started_at)
    LOGGER.info("V1 refresh completado.")


if __name__ == "__main__":
    main()
