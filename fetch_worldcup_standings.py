from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from pipeline_core import (
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_optional_int,
    utcnow,
)

LOGGER = configure_logging("stuf.worldcup-standings")


@dataclass(frozen=True)
class ParsedStanding:
    team_id: int
    team_name: str
    group_name: str | None
    row: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and store World Cup standings/groups from API-Football.")
    parser.add_argument("--league", type=int, default=1)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--apply", action="store_true", help="Write standings_snapshots and team_standings.")
    return parser.parse_args()


def select_all(repository: StufRepository, table: str, select: str, **eq: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(select)
            for key, value in eq.items():
                query = query.eq(key, value)
            return query.range(offset, offset + 999)

        response = repository._execute(request, f"select {table} offset={offset}")
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def optional_int(value: Any) -> int | None:
    return parse_optional_int(value)


def goals_for_against(block: dict[str, Any] | None) -> tuple[int | None, int | None]:
    goals = (block or {}).get("goals") or {}
    return optional_int(goals.get("for")), optional_int(goals.get("against"))


def standing_block(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def parse_standing_row(row: dict[str, Any], league_id: int, season: int) -> ParsedStanding | None:
    team = row.get("team") or {}
    team_id = optional_int(team.get("id"))
    if not team_id:
        return None

    all_block = standing_block(row, "all")
    home_block = standing_block(row, "home")
    away_block = standing_block(row, "away")
    all_gf, all_ga = goals_for_against(all_block)
    home_gf, home_ga = goals_for_against(home_block)
    away_gf, away_ga = goals_for_against(away_block)

    parsed_row = {
        "league_id": league_id,
        "season": season,
        "team_id": team_id,
        "rank": optional_int(row.get("rank")),
        "points": optional_int(row.get("points")),
        "goals_diff": optional_int(row.get("goalsDiff")),
        "group_name": row.get("group"),
        "form": row.get("form"),
        "status": row.get("status"),
        "description": row.get("description"),
        "played": optional_int(all_block.get("played")),
        "win": optional_int(all_block.get("win")),
        "draw": optional_int(all_block.get("draw")),
        "loss": optional_int(all_block.get("lose")),
        "goals_for": all_gf,
        "goals_against": all_ga,
        "home_played": optional_int(home_block.get("played")),
        "home_win": optional_int(home_block.get("win")),
        "home_draw": optional_int(home_block.get("draw")),
        "home_loss": optional_int(home_block.get("lose")),
        "home_goals_for": home_gf,
        "home_goals_against": home_ga,
        "away_played": optional_int(away_block.get("played")),
        "away_win": optional_int(away_block.get("win")),
        "away_draw": optional_int(away_block.get("draw")),
        "away_loss": optional_int(away_block.get("lose")),
        "away_goals_for": away_gf,
        "away_goals_against": away_ga,
        "updated_at": utcnow().isoformat(),
        "raw_payload": row,
    }
    return ParsedStanding(team_id=team_id, team_name=str(team.get("name") or ""), group_name=row.get("group"), row=parsed_row)


def parse_payload(payload: dict[str, Any], league_id: int, season: int) -> list[ParsedStanding]:
    parsed: list[ParsedStanding] = []
    for competition in payload.get("response") or []:
        league = competition.get("league") or {}
        standings = league.get("standings") or []
        for group_rows in standings:
            if not isinstance(group_rows, list):
                continue
            for raw_row in group_rows:
                if not isinstance(raw_row, dict):
                    continue
                group_name = str(raw_row.get("group") or "").strip().lower()
                if "third-placed" in group_name or "third placed" in group_name:
                    continue
                row = parse_standing_row(raw_row, league_id, season)
                if row is not None:
                    parsed.append(row)
    return parsed


def deduplicate_standings(rows: list[ParsedStanding]) -> list[ParsedStanding]:
    """One row per team_id — named group wins over 'Group Stage' catch-all."""
    by_team: dict[int, ParsedStanding] = {}
    for row in rows:
        existing = by_team.get(row.team_id)
        if existing is None:
            by_team[row.team_id] = row
        else:
            existing_is_catchall = (existing.group_name or "").strip().lower() == "group stage"
            incoming_is_catchall = (row.group_name or "").strip().lower() == "group stage"
            if existing_is_catchall and not incoming_is_catchall:
                by_team[row.team_id] = row
    return list(by_team.values())


def load_known_team_ids(repository: StufRepository, league_id: int, season: int) -> set[int]:
    rows = select_all(
        repository,
        "team_league_seasons",
        "team_id",
        league_id=league_id,
        season=season,
        is_active=True,
    )
    return {int(row["team_id"]) for row in rows if row.get("team_id") is not None}


def describe_plan(rows: list[ParsedStanding], known_team_ids: set[int]) -> dict[str, Any]:
    team_ids = {row.team_id for row in rows}
    group_counts: dict[str, int] = {}
    for row in rows:
        label = row.group_name or "NO_GROUP"
        group_counts[label] = group_counts.get(label, 0) + 1
    return {
        "rows": len(rows),
        "teams": len(team_ids),
        "groups": len(group_counts),
        "group_counts": dict(sorted(group_counts.items())),
        "unknown_team_ids": sorted(team_ids - known_team_ids),
        "duplicate_team_ids": sorted(team_id for team_id in team_ids if sum(1 for row in rows if row.team_id == team_id) > 1),
    }


def write_payload(repository: StufRepository, league_id: int, season: int, payload: dict[str, Any], rows: list[ParsedStanding]) -> None:
    repository._execute(
        lambda: repository.supabase.table("standings_snapshots").insert(
            {
                "league_id": league_id,
                "season": season,
                "payload": payload,
            }
        ),
        f"insert standings snapshot league={league_id} season={season}",
    )
    repository._upsert_rows(
        "team_standings",
        [item.row for item in rows],
        "league_id,season,team_id",
        f"upsert team_standings league={league_id} season={season}",
    )


async def main_async() -> None:
    args = parse_args()
    settings = load_settings()
    repository = StufRepository(create_supabase_client(settings), LOGGER)

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        payload = await api_client.fetch("standings", {"league": args.league, "season": args.season})

    if payload is None:
        raise RuntimeError("API-Football standings request failed.")

    rows = parse_payload(payload, args.league, args.season)
    rows = deduplicate_standings(rows)
    known_team_ids = load_known_team_ids(repository, args.league, args.season)
    plan = describe_plan(rows, known_team_ids)
    LOGGER.info("World Cup standings plan: %s apply=%s", plan, args.apply)

    if plan["unknown_team_ids"]:
        raise RuntimeError(f"API returned standings teams that are not registered WC participants: {plan['unknown_team_ids']}")
    if not rows:
        LOGGER.warning("No standings rows parsed. Nothing to write.")
        return
    if not args.apply:
        return

    write_payload(repository, args.league, args.season, payload, rows)
    LOGGER.info("World Cup standings stored rows=%s groups=%s.", plan["rows"], plan["groups"])


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
