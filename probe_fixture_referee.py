import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

from pipeline_core import ApiFootballClient, Settings, configure_logging

LOGGER = configure_logging("stuf.probe-fixture-referee")


def build_settings() -> Settings:
    load_dotenv()
    api_key = os.getenv("API_SPORTS_KEY")
    if not api_key:
        raise RuntimeError("Falta API_SPORTS_KEY en el entorno.")

    return Settings(
        api_key=api_key,
        supabase_url="",
        supabase_service_role_key="",
        target_leagues=(),
        pinnacle_bookmaker_name="",
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Consulta un fixture puntual en API-Football y muestra el referee devuelto."
    )
    parser.add_argument("--fixture-id", type=int, required=True, help="ID del fixture a consultar.")
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Delay entre requests del cliente API.",
    )
    parser.add_argument(
        "--show-fixture-json",
        action="store_true",
        help="Muestra el bloque completo fixture del payload.",
    )
    args = parser.parse_args()

    settings = build_settings()

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        payload = await api_client.fetch("fixtures", {"id": args.fixture_id})

    response_rows = (payload or {}).get("response", [])
    if not response_rows:
        LOGGER.warning("No hubo response para fixture_id=%s", args.fixture_id)
        print(
            json.dumps(
                {
                    "fixture_id": args.fixture_id,
                    "results": (payload or {}).get("results"),
                    "errors": (payload or {}).get("errors"),
                    "response": response_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    row = response_rows[0]
    fixture = row.get("fixture") or {}
    teams = row.get("teams") or {}
    home = (teams.get("home") or {}).get("name")
    away = (teams.get("away") or {}).get("name")

    result = {
        "fixture_id": fixture.get("id"),
        "date": fixture.get("date"),
        "status": (fixture.get("status") or {}).get("short"),
        "home_team": home,
        "away_team": away,
        "referee": fixture.get("referee"),
        "venue": fixture.get("venue"),
        "results": (payload or {}).get("results"),
        "errors": (payload or {}).get("errors"),
    }

    if args.show_fixture_json:
        result["fixture_payload"] = fixture

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
