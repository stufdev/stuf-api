"""Matchday refresh — un solo comando para todo el pipeline diario.

Encadena en orden, con timing por paso y resumen final:

  1. fetch_pre_match_odds      — captura precios pre-partido (API-Football)
  2. normalize_odds_snapshots  — resuelve precios + decision cards
  3. rebuild_market_serving_layer — categorías pendientes (solo si se piden)
  4. rebuild_fixture_signals   — Match Intelligence

Uso típico (Mundial, día de partido):
    python run_matchday_refresh.py --season 2026 --leagues 1

Con refresh de serving layer incluido (una sola corrida, sin comandos sueltos):
    python run_matchday_refresh.py --season 2026 --leagues 1 --serving-categories fouls,booking_points

Saltar pasos:
    python run_matchday_refresh.py --season 2026 --leagues 1 --skip-odds
    python run_matchday_refresh.py --season 2026 --leagues 1 --skip-signals

Un paso que falla NO corta la cadena: se reporta y se continúa, y el
resumen final muestra OK/FAIL por paso con su duración real.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


@dataclass
class StepResult:
    name: str
    command: list[str]
    elapsed_seconds: float
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_step(name: str, command: list[str]) -> StepResult:
    print(f"\n{'=' * 70}")
    print(f"PASO: {name}")
    print(f"CMD : {' '.join(command)}")
    print("=" * 70, flush=True)
    started = time.monotonic()
    completed = subprocess.run(command, cwd=SCRIPT_DIR)
    elapsed = time.monotonic() - started
    status = "OK" if completed.returncode == 0 else f"FAIL (exit {completed.returncode})"
    print(f"\n--> {name}: {status} en {elapsed:,.0f}s", flush=True)
    return StepResult(name=name, command=command, elapsed_seconds=elapsed, returncode=completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Matchday refresh: todo el pipeline en una sola corrida.")
    parser.add_argument("--season", required=True, help="Temporada (ej. 2026)")
    parser.add_argument("--leagues", required=True, help="IDs de liga separados por coma (ej. 1)")
    parser.add_argument("--window-hours", default="96", help="Ventana de odds en horas (default 96)")
    parser.add_argument(
        "--serving-categories",
        default="",
        help="Categorías de serving layer a reconstruir en esta corrida (ej. fouls,booking_points). Vacío = no tocar.",
    )
    parser.add_argument("--skip-odds", action="store_true", help="Salta fetch + normalize de odds")
    parser.add_argument("--skip-signals", action="store_true", help="Salta rebuild de fixture signals")
    args = parser.parse_args()

    python = sys.executable
    results: list[StepResult] = []

    if not args.skip_odds:
        results.append(run_step(
            "Fetch pre-match odds",
            [python, "fetch_pre_match_odds.py", "--window-hours", args.window_hours,
             "--season", args.season, "--leagues", args.leagues],
        ))
        results.append(run_step(
            "Normalize odds + decision cards",
            [python, "normalize_odds_snapshots.py", "--season", args.season, "--leagues", args.leagues],
        ))

    serving_categories = [item.strip() for item in args.serving_categories.split(",") if item.strip()]
    for category in serving_categories:
        results.append(run_step(
            f"Serving layer: {category}",
            [python, "rebuild_market_serving_layer.py", "--category", category,
             "--season", args.season, "--leagues", args.leagues],
        ))

    if not args.skip_signals:
        results.append(run_step(
            "Fixture signals (Match Intelligence)",
            [python, "rebuild_fixture_signals.py", "--season", args.season,
             "--leagues", args.leagues, "--full-refresh"],
        ))

    print(f"\n{'=' * 70}")
    print("RESUMEN MATCHDAY REFRESH")
    print("=" * 70)
    total = 0.0
    failures = 0
    for result in results:
        total += result.elapsed_seconds
        mark = "✓" if result.ok else "✗"
        if not result.ok:
            failures += 1
        print(f"  {mark} {result.name:<40} {result.elapsed_seconds:>8,.0f}s")
    print(f"  {'TOTAL':<42} {total:>8,.0f}s ({total / 60:,.1f} min)")
    if failures:
        print(f"\n{failures} paso(s) FALLARON — revisar logs arriba. Los demás pasos sí corrieron.")
        return 1
    print("\nTodo OK. STUF está fresco para el matchday.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
