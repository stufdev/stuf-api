from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from pipeline_core import (
    ApiFootballClient,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_optional_int,
    utcnow,
)


LOGGER = configure_logging("stuf.backfill.national-team-flags")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill teams.national from API-Football /teams for a competition. "
            "Used for World Cup/national-team scopes after fixture-shell ingest."
        )
    )
    parser.add_argument("--league", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--spot-check-false",
        action="store_true",
        help="For teams returned as national=false, call /teams?id=<id> and log the direct result.",
    )
    parser.add_argument(
        "--force-national-for-competition-participants",
        action="store_true",
        help=(
            "Set national=true for every returned competition participant. "
            "Use only after confirming the competition is a national-team competition."
        ),
    )
    return parser.parse_args()


def parse_api_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return None


def build_team_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    team = item.get("team") or {}
    team_id = parse_optional_int(team.get("id"))
    if team_id is None or team_id <= 0:
        return None

    payload: dict[str, Any] = {
        "id": team_id,
        "updated_at": utcnow().isoformat(),
    }
    if team.get("name") is not None:
        payload["name"] = team.get("name")
    if team.get("logo") is not None:
        payload["logo_url"] = team.get("logo")

    national = parse_api_bool(team.get("national"))
    if national is not None:
        payload["national"] = national

    return payload


async def main_async() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)

    spot_checks: list[dict[str, Any]] = []
    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        payload = await api_client.fetch(
            "teams",
            {"league": args.league, "season": args.season},
        )
        rows = (payload or {}).get("response", [])

    payloads = [
        payload
        for item in rows
        if (payload := build_team_payload(item)) is not None
    ]
    false_payloads = [
        {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "national": payload.get("national"),
        }
        for payload in payloads
        if payload.get("national") is False
    ]
    omitted_payloads = [
        {
            "id": payload.get("id"),
            "name": payload.get("name"),
        }
        for payload in payloads
        if "national" not in payload
    ]
    forced_overrides: list[dict[str, Any]] = []
    if args.force_national_for_competition_participants:
        for payload in payloads:
            if payload.get("national") is not True:
                forced_overrides.append(
                    {
                        "id": payload.get("id"),
                        "name": payload.get("name"),
                        "api_national": payload.get("national"),
                    }
                )
            payload["national"] = True

    national_true = sum(1 for payload in payloads if payload.get("national") is True)
    national_false = sum(1 for payload in payloads if payload.get("national") is False)
    national_omitted = sum(1 for payload in payloads if "national" not in payload)

    LOGGER.info(
        "API teams league=%s season=%s total=%s national_true=%s national_false=%s national_omitted=%s",
        args.league,
        args.season,
        len(payloads),
        national_true,
        national_false,
        national_omitted,
    )
    LOGGER.info(
        "Sample: %s",
        json.dumps(
            [
                {
                    "id": payload.get("id"),
                    "name": payload.get("name"),
                    "national": payload.get("national"),
                }
                for payload in payloads[:10]
            ],
            ensure_ascii=False,
        ),
    )
    if false_payloads:
        LOGGER.warning(
            "API teams returned with national=false: %s",
            json.dumps(false_payloads, ensure_ascii=False),
        )
        if args.spot_check_false:
            async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
                for payload in false_payloads:
                    team_payload = await api_client.fetch("teams", {"id": payload["id"]})
                    team_rows = (team_payload or {}).get("response", [])
                    team = ((team_rows[0] if team_rows else {}).get("team") or {})
                    spot_checks.append(
                        {
                            "id": payload["id"],
                            "league_season_name": payload["name"],
                            "league_season_national": payload["national"],
                            "direct_name": team.get("name"),
                            "direct_national": team.get("national"),
                            "direct_country": team.get("country"),
                        }
                    )
            LOGGER.warning(
                "Direct /teams?id spot-checks: %s",
                json.dumps(spot_checks, ensure_ascii=False),
            )
    if omitted_payloads:
        LOGGER.warning(
            "API teams returned without national field: %s",
            json.dumps(omitted_payloads, ensure_ascii=False),
        )
    if forced_overrides:
        LOGGER.warning(
            "Forced national=true for competition participants: %s",
            json.dumps(forced_overrides, ensure_ascii=False),
        )

    if args.dry_run:
        LOGGER.info("Dry-run only. No teams were updated.")
        return

    if not payloads:
        raise RuntimeError(
            f"No teams returned by API-Football for league={args.league} season={args.season}."
        )
    if (false_payloads or omitted_payloads) and not args.force_national_for_competition_participants:
        raise RuntimeError(
            "API returned national=false or omitted national for some competition participants. "
            "Re-run with --force-national-for-competition-participants only after confirming this is a national-team competition."
        )

    response = (
        supabase.table("teams")
        .upsert(payloads, on_conflict="id")
        .execute()
    )
    updated_count = len(response.data or payloads)
    LOGGER.info(
        "Backfill complete league=%s season=%s updated=%s national_true=%s",
        args.league,
        args.season,
        updated_count,
        national_true,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
