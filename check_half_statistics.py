import argparse
import asyncio
from typing import Any

from pipeline_core import ApiFootballClient, configure_logging, load_settings


LOGGER = configure_logging("stuf.check-half-statistics")


def normalize_stat_value(value: Any) -> str:
    if value is None:
        return "NULL"
    return str(value).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara /fixtures/statistics FT contra variantes de half para validar datos 1H."
    )
    parser.add_argument("--fixture", type=int, required=True, help="fixture_id de API-Football.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    return parser.parse_args()


def extract_team_stats(payload: dict[str, Any] | None, statistics_key: str = "statistics") -> dict[int, dict[str, str]]:
    stats_by_team: dict[int, dict[str, str]] = {}
    for item in (payload or {}).get("response", []):
        team = item.get("team") or {}
        team_id = team.get("id")
        if team_id is None:
            continue

        team_stats: dict[str, str] = {}
        for stat in item.get(statistics_key) or []:
            stat_type = stat.get("type")
            if stat_type:
                team_stats[str(stat_type)] = normalize_stat_value(stat.get("value"))

        if team_stats:
            stats_by_team[int(team_id)] = team_stats

    return stats_by_team


def extract_team_corners(team_stats: dict[int, dict[str, str]]) -> dict[int, str | None]:
    return {
        team_id: stats.get("Corner Kicks")
        for team_id, stats in team_stats.items()
    }


def format_corners(corners_by_team: dict[int, str | None]) -> str:
    if not corners_by_team:
        return "sin response"
    return ", ".join(f"{team_id}={value}" for team_id, value in sorted(corners_by_team.items()))


def format_errors(payload: dict[str, Any] | None) -> str:
    errors = (payload or {}).get("errors")
    if not errors:
        return ""
    return f" | errors={errors}"


def compare_stats(
    candidate: dict[int, dict[str, str]],
    baseline: dict[int, dict[str, str]],
) -> str:
    compared = 0
    diffs: list[str] = []

    for team_id, baseline_stats in sorted(baseline.items()):
        candidate_stats = candidate.get(team_id)
        if candidate_stats is None:
            diffs.append(f"{team_id}:missing_team")
            continue

        for stat_type in sorted(set(baseline_stats.keys()) & set(candidate_stats.keys())):
            compared += 1
            baseline_value = baseline_stats[stat_type]
            candidate_value = candidate_stats[stat_type]
            if baseline_value != candidate_value:
                diffs.append(f"{team_id}:{stat_type} {baseline_value}->{candidate_value}")

    if not candidate:
        return " | stats=NO_RESPONSE"
    if diffs:
        preview = "; ".join(diffs[:5])
        suffix = "..." if len(diffs) > 5 else ""
        return f" | stats_differ={len(diffs)}/{compared}: {preview}{suffix}"
    return f" | stats_same={compared}/{compared}"


async def main() -> None:
    args = parse_args()
    settings = load_settings()

    variants: list[tuple[str, dict[str, Any]]] = [
        ("FT/no-half", {"fixture": args.fixture}),
        ("half=false", {"fixture": args.fixture, "half": "false"}),
        ("half=true", {"fixture": args.fixture, "half": "true"}),
        ("type=Corner Kicks", {"fixture": args.fixture, "type": "Corner Kicks"}),
        ("half=true + type=Corner Kicks", {"fixture": args.fixture, "half": "true", "type": "Corner Kicks"}),
        ("half=1", {"fixture": args.fixture, "half": "1"}),
        ("half=1H", {"fixture": args.fixture, "half": "1H"}),
    ]

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        baseline_corners: dict[int, str | None] | None = None
        baseline_stats: dict[int, dict[str, str]] | None = None
        for label, params in variants:
            payload = await api_client.fetch("fixtures/statistics", params)
            team_stats = extract_team_stats(payload)
            team_stats_1h = extract_team_stats(payload, "statistics_1h")
            team_stats_2h = extract_team_stats(payload, "statistics_2h")
            corners = extract_team_corners(team_stats)
            corners_1h = extract_team_corners(team_stats_1h)
            corners_2h = extract_team_corners(team_stats_2h)
            if baseline_corners is None:
                baseline_corners = corners
                baseline_stats = team_stats

            comparison = ""
            if label != "FT/no-half":
                if not corners:
                    comparison = " | NO_RESPONSE_OR_INVALID"
                else:
                    comparison = (
                        " | main_statistics_same_as_FT"
                        if corners == baseline_corners
                        else " | main_statistics_differs_from_FT"
                    )
                comparison += compare_stats(team_stats, baseline_stats or {})
                if corners_1h:
                    comparison += f" | statistics_1h={format_corners(corners_1h)}"
                if corners_2h:
                    comparison += f" | statistics_2h={format_corners(corners_2h)}"

            print(f"{label}: {format_corners(corners)}{format_errors(payload)}{comparison}")


if __name__ == "__main__":
    asyncio.run(main())
