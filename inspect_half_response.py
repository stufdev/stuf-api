import argparse
import asyncio
import json
from typing import Any

from pipeline_core import ApiFootballClient, configure_logging, load_settings


LOGGER = configure_logging("stuf.inspect-half-response")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspecciona la estructura cruda de /fixtures/statistics FT vs half=true."
    )
    parser.add_argument("--fixture", type=int, required=True, help="fixture_id de API-Football.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    parser.add_argument(
        "--raw-limit",
        type=int,
        default=3000,
        help="Cantidad maxima de caracteres del JSON crudo a imprimir por variante.",
    )
    return parser.parse_args()


def summarize_response(payload: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    response = (payload or {}).get("response") or []
    lines.append(f"top_keys={list((payload or {}).keys())}")
    lines.append(f"errors={(payload or {}).get('errors')}")
    lines.append(f"response_len={len(response)}")

    for index, item in enumerate(response):
        team = item.get("team") or {}
        statistics = item.get("statistics") or []
        lines.append(
            f"item[{index}] keys={list(item.keys())} team_id={team.get('id')} "
            f"team_name={team.get('name')} stats_len={len(statistics)}"
        )
        lines.append(f"item[{index}] first_stats={statistics[:5]}")
        for half_key in ("statistics_1h", "statistics_2h"):
            half_statistics = item.get(half_key) or []
            if half_statistics:
                lines.append(f"item[{index}] {half_key}_len={len(half_statistics)} first_stats={half_statistics[:5]}")

    return lines


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    variants: list[tuple[str, dict[str, Any]]] = [
        ("FT/no-half", {"fixture": args.fixture}),
        ("half=true", {"fixture": args.fixture, "half": "true"}),
    ]

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        for label, params in variants:
            payload = await api_client.fetch("fixtures/statistics", params)
            print(f"\n=== {label} {params} ===")
            for line in summarize_response(payload):
                print(line)
            raw = json.dumps(payload, ensure_ascii=False)
            print(f"raw_preview={raw[: args.raw_limit]}")


if __name__ == "__main__":
    asyncio.run(main())
