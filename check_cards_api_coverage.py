from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from pipeline_core import (
    ApiFootballClient,
    chunked,
    configure_logging,
    create_supabase_client,
    is_final_status,
    load_settings,
    parse_optional_int,
    parse_target_leagues,
)


LOGGER = configure_logging("stuf.audit.cards-api-coverage")
DEFAULT_LEAGUES = (39, 61, 78, 135, 140)
CLASSIFICATIONS = (
    "API_HAS_CARDS_AND_DB_HAS_CARDS",
    "API_HAS_CARDS_BUT_DB_MISSING",
    "EVENTS_HAVE_CARDS_BUT_FACTS_MISSING",
    "STATS_HAVE_CARDS_BUT_FACTS_MISSING",
    "PLAYER_STATS_HAVE_CARDS_BUT_FACTS_MISSING",
    "FIXTURE_NOT_FINAL_OR_CANCELLED",
    "API_MISSING_CARDS",
    "API_TEAM_MAPPING_MISMATCH",
    "API_RESPONSE_ERROR",
    "DB_HAS_CARDS_BUT_API_MISSING",
)


@dataclass(frozen=True)
class FixturePick:
    league_id: int
    season: int
    fixture_id: int
    played_at: str | None
    sample_bucket: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit for Cards coverage. Compares API-Football "
            "/fixtures/statistics, /fixtures/events, and optionally "
            "/fixtures/players against STUF DB."
        )
    )
    parser.add_argument("--leagues", default=",".join(str(item) for item in DEFAULT_LEAGUES))
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument(
        "--mode",
        choices=("exhaustive", "sample"),
        default="exhaustive",
        help="exhaustive audits every fixture in scope; sample is only for smoke tests.",
    )
    parser.add_argument("--fixtures-per-league", type=int, default=10, help="Only used with --mode sample.")
    parser.add_argument("--missing-ratio", type=float, default=0.7, help="Only used with --mode sample.")
    parser.add_argument("--team-id", type=int)
    parser.add_argument("--fixture-id", type=int, action="append", dest="fixture_ids")
    parser.add_argument("--max-api-requests", type=int, default=100)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--include-half-statistics", action="store_true")
    parser.add_argument("--include-players", action="store_true")
    parser.add_argument(
        "--include-player-card-tops",
        action="store_true",
        help="Also query /players/topyellowcards and /players/topredcards per league as provider-level cross-checks.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-files", action="store_true")
    parser.add_argument("--output-dir", default=".")
    return parser.parse_args()


def select_all(build_query: Callable[[], Any], page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = build_query().range(offset, offset + page_size - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def select_in_chunks(
    supabase: Any,
    table: str,
    columns: str,
    *,
    in_column: str,
    values: Iterable[int],
    chunk_size: int = 100,
    extra_query: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ids = sorted({int(value) for value in values if value is not None})
    for id_chunk in chunked(ids, chunk_size):
        def build_query(id_chunk: Sequence[int] = id_chunk):
            query = supabase.table(table).select(columns).in_(in_column, list(id_chunk))
            return extra_query(query) if extra_query else query

        rows.extend(select_all(build_query))
    return rows


def normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def parse_card_value(value: Any) -> int | None:
    return parse_optional_int(value)


def is_card_stat_type(value: Any) -> bool:
    normalized = normalize_token(value)
    return normalized in {
        "cards",
        "card",
        "yellowcards",
        "yellowcard",
        "redcards",
        "redcard",
        "secondyellowcards",
        "secondyellowcard",
    }


def stat_bucket(value: Any) -> str | None:
    normalized = normalize_token(value)
    if normalized in {"yellowcards", "yellowcard"}:
        return "yellow_cards"
    if normalized in {"redcards", "redcard"}:
        return "red_cards"
    if normalized in {"cards", "card"}:
        return "cards"
    if normalized in {"secondyellowcards", "secondyellowcard"}:
        return "second_yellow_cards"
    return None


def is_card_event(event_type: Any, detail: Any) -> bool:
    normalized_type = normalize_token(event_type)
    normalized_detail = normalize_token(detail)
    return normalized_type == "card" or "card" in normalized_detail


def event_bucket(detail: Any) -> str:
    normalized = normalize_token(detail)
    if "secondyellow" in normalized:
        return "second_yellow_card_events"
    if "yellow" in normalized:
        return "yellow_card_events"
    if "red" in normalized:
        return "red_card_events"
    return "other_card_events"


def safe_sum(*values: int | None) -> int | None:
    parsed = [value for value in values if value is not None]
    if len(parsed) != len(values):
        return None
    return sum(parsed)


def has_numeric_cards(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is not None
        for key in ("yellow_cards", "red_cards", "total_cards")
    )


def extract_api_statistics(payload: dict[str, Any] | None, statistics_key: str = "statistics") -> tuple[dict[int, dict[str, Any]], str | None]:
    if payload is None:
        return {}, "API client returned no payload."
    errors = payload.get("errors")
    if errors:
        return {}, f"API payload errors: {errors}"
    response = payload.get("response")
    if not isinstance(response, list):
        return {}, "API payload has no response list."

    by_team: dict[int, dict[str, Any]] = {}
    for team_stat in response:
        team = team_stat.get("team") or {}
        team_id = parse_optional_int(team.get("id"))
        if team_id is None:
            continue

        card_items = []
        buckets: dict[str, int | None] = {
            "yellow_cards": None,
            "red_cards": None,
            "cards": None,
            "second_yellow_cards": None,
        }
        stat_types = []
        for item in team_stat.get(statistics_key) or []:
            stat_type = item.get("type")
            stat_types.append(str(stat_type))
            if not is_card_stat_type(stat_type):
                continue
            bucket = stat_bucket(stat_type)
            parsed = parse_card_value(item.get("value"))
            card_items.append({"type": stat_type, "value": item.get("value"), "parsed_value": parsed})
            if bucket:
                buckets[bucket] = parsed

        total_cards = buckets["cards"]
        if total_cards is None:
            total_cards = safe_sum(buckets["yellow_cards"], buckets["red_cards"])

        by_team[team_id] = {
            "team_id": team_id,
            "team_name": team.get("name"),
            "card_items": card_items,
            "stat_types": stat_types,
            "yellow_cards": buckets["yellow_cards"],
            "red_cards": buckets["red_cards"],
            "cards": buckets["cards"],
            "second_yellow_cards": buckets["second_yellow_cards"],
            "total_cards": total_cards,
            "has_cards": any(item["parsed_value"] is not None for item in card_items),
        }
    return by_team, None


def extract_api_events(payload: dict[str, Any] | None) -> tuple[dict[int, dict[str, Any]], str | None]:
    if payload is None:
        return {}, "API client returned no payload."
    errors = payload.get("errors")
    if errors:
        return {}, f"API payload errors: {errors}"
    response = payload.get("response")
    if not isinstance(response, list):
        return {}, "API payload has no response list."

    by_team: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "yellow_card_events": 0,
            "red_card_events": 0,
            "second_yellow_card_events": 0,
            "other_card_events": 0,
            "card_events": 0,
            "items": [],
        }
    )
    for event in response:
        team = event.get("team") or {}
        team_id = parse_optional_int(team.get("id"))
        if team_id is None or not is_card_event(event.get("type"), event.get("detail")):
            continue

        bucket = event_bucket(event.get("detail"))
        by_team[team_id][bucket] += 1
        by_team[team_id]["card_events"] += 1
        by_team[team_id]["team_id"] = team_id
        by_team[team_id]["team_name"] = team.get("name")
        by_team[team_id]["items"].append(
            {
                "type": event.get("type"),
                "detail": event.get("detail"),
                "elapsed": (event.get("time") or {}).get("elapsed"),
                "extra": (event.get("time") or {}).get("extra"),
                "player": (event.get("player") or {}).get("name"),
            }
        )

    return dict(by_team), None


def extract_api_players(payload: dict[str, Any] | None) -> tuple[dict[int, dict[str, Any]], str | None]:
    if payload is None:
        return {}, "API client returned no payload."
    errors = payload.get("errors")
    if errors:
        return {}, f"API payload errors: {errors}"
    response = payload.get("response")
    if not isinstance(response, list):
        return {}, "API payload has no response list."

    by_team: dict[int, dict[str, Any]] = {}
    for team_group in response:
        team = team_group.get("team") or {}
        team_id = parse_optional_int(team.get("id"))
        if team_id is None:
            continue

        yellow_cards = 0
        red_cards = 0
        players_with_cards = []
        for player_item in team_group.get("players") or []:
            player = player_item.get("player") or {}
            for stats in player_item.get("statistics") or []:
                cards = stats.get("cards") or {}
                yellow = parse_optional_int(cards.get("yellow")) or 0
                red = parse_optional_int(cards.get("red")) or 0
                yellow_cards += yellow
                red_cards += red
                if yellow or red:
                    players_with_cards.append(
                        {
                            "player_id": player.get("id"),
                            "player_name": player.get("name"),
                            "yellow_cards": yellow,
                            "red_cards": red,
                        }
                    )

        by_team[team_id] = {
            "team_id": team_id,
            "team_name": team.get("name"),
            "yellow_cards": yellow_cards,
            "red_cards": red_cards,
            "total_cards": yellow_cards + red_cards,
            "players_with_cards": players_with_cards,
            "has_cards": (yellow_cards + red_cards) > 0,
        }
    return by_team, None


def db_event_counts(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_team: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "yellow_card_events": 0,
            "red_card_events": 0,
            "second_yellow_card_events": 0,
            "other_card_events": 0,
            "card_events": 0,
            "items": [],
        }
    )
    for row in rows:
        team_id = parse_optional_int(row.get("team_id"))
        if team_id is None or not is_card_event(row.get("type"), row.get("detail")):
            continue
        bucket = event_bucket(row.get("detail"))
        by_team[team_id][bucket] += 1
        by_team[team_id]["card_events"] += 1
        by_team[team_id]["items"].append(
            {
                "type": row.get("type"),
                "detail": row.get("detail"),
                "elapsed": row.get("elapsed"),
                "extra_time": row.get("extra_time"),
            }
        )
    return dict(by_team)


def db_player_card_counts(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_team: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "yellow_cards": 0,
            "red_cards": 0,
            "total_cards": 0,
            "players_with_cards": [],
            "has_cards": False,
        }
    )
    for row in rows:
        team_id = parse_optional_int(row.get("team_id"))
        if team_id is None:
            continue
        yellow = parse_optional_int(row.get("yellow_cards")) or 0
        red = parse_optional_int(row.get("red_cards")) or 0
        by_team[team_id]["yellow_cards"] += yellow
        by_team[team_id]["red_cards"] += red
        by_team[team_id]["total_cards"] += yellow + red
        if yellow or red:
            by_team[team_id]["has_cards"] = True
            by_team[team_id]["players_with_cards"].append(
                {
                    "player_id": row.get("player_id"),
                    "yellow_cards": yellow,
                    "red_cards": red,
                }
            )
    return dict(by_team)


def summarize_player_card_top_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {"rows": 0, "error": "API client returned no payload."}
    errors = payload.get("errors")
    if errors:
        return {"rows": 0, "error": f"API payload errors: {errors}"}
    response = payload.get("response")
    if not isinstance(response, list):
        return {"rows": 0, "error": "API payload has no response list."}
    return {
        "rows": len(response),
        "error": None,
        "sample": response[:5],
    }


def evenly_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not rows:
        return []
    rows = sorted(rows, key=lambda row: (str(row.get("played_at") or ""), int(row["fixture_id"])))
    if len(rows) <= limit:
        return rows
    if limit == 1:
        return [rows[-1]]
    indexes = sorted({round(index * (len(rows) - 1) / (limit - 1)) for index in range(limit)})
    return [rows[index] for index in indexes]


def build_fixture_plan(
    facts: list[dict[str, Any]],
    *,
    leagues: Sequence[int],
    season: int,
    mode: str,
    fixtures_per_league: int,
    missing_ratio: float,
    explicit_fixture_ids: Sequence[int] | None,
) -> list[FixturePick]:
    facts_by_fixture: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_fixture[int(fact["fixture_id"])].append(fact)

    rows_by_league_bucket: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for fixture_id, fixture_facts in facts_by_fixture.items():
        first = fixture_facts[0]
        league_id = int(first["league_id"])
        if league_id not in leagues:
            continue
        if explicit_fixture_ids and fixture_id not in explicit_fixture_ids:
            continue
        complete = all(
            row.get("cards_for") is not None
            and row.get("cards_against") is not None
            and row.get("total_cards") is not None
            for row in fixture_facts
        )
        bucket = "db_cards_present" if complete else "db_cards_missing"
        rows_by_league_bucket[(league_id, bucket)].append(
            {
                "league_id": league_id,
                "season": season,
                "fixture_id": fixture_id,
                "played_at": first.get("played_at"),
                "sample_bucket": bucket,
            }
        )

    picks: list[FixturePick] = []
    for league_id in leagues:
        all_rows = sorted(
            rows_by_league_bucket.get((league_id, "db_cards_missing"), [])
            + rows_by_league_bucket.get((league_id, "db_cards_present"), []),
            key=lambda row: (str(row.get("played_at") or ""), int(row["fixture_id"])),
        )
        if explicit_fixture_ids:
            selected = all_rows
        elif mode == "exhaustive":
            selected = all_rows
        else:
            missing_target = math.ceil(fixtures_per_league * max(0.0, min(1.0, missing_ratio)))
            present_target = fixtures_per_league - missing_target
            missing = evenly_sample(rows_by_league_bucket.get((league_id, "db_cards_missing"), []), missing_target)
            present = evenly_sample(rows_by_league_bucket.get((league_id, "db_cards_present"), []), present_target)
            selected = missing + present
            if len(selected) < fixtures_per_league:
                selected_ids = {int(row["fixture_id"]) for row in selected}
                filler_pool = [
                    row
                    for row in (
                        rows_by_league_bucket.get((league_id, "db_cards_missing"), [])
                        + rows_by_league_bucket.get((league_id, "db_cards_present"), [])
                    )
                    if int(row["fixture_id"]) not in selected_ids
                ]
                selected.extend(evenly_sample(filler_pool, fixtures_per_league - len(selected)))

        if mode == "sample" and not explicit_fixture_ids:
            selected = selected[:fixtures_per_league]

        for row in selected:
            picks.append(
                FixturePick(
                    league_id=int(row["league_id"]),
                    season=season,
                    fixture_id=int(row["fixture_id"]),
                    played_at=row.get("played_at"),
                    sample_bucket=str(row["sample_bucket"]),
                )
            )
    return picks


def classify_row(
    *,
    fixture_is_final: bool,
    api_stats_error: str | None,
    api_events_error: str | None,
    api_players_error: str | None,
    api_stats_team_ids: set[int],
    api_event_card_team_ids: set[int],
    api_player_team_ids: set[int],
    api_player_card_team_ids: set[int],
    fixture_team_ids: set[int],
    team_id: int,
    api_stats: dict[str, Any],
    api_half_ft_stats: dict[str, Any],
    api_half_1h_stats: dict[str, Any],
    api_half_2h_stats: dict[str, Any],
    api_events: dict[str, Any],
    api_players: dict[str, Any],
    db_stats: dict[str, Any],
    db_stats_1h: dict[str, Any],
    db_stats_2h: dict[str, Any],
    db_events: dict[str, Any],
    db_players: dict[str, Any],
    db_fact: dict[str, Any],
) -> str:
    if not fixture_is_final:
        return "FIXTURE_NOT_FINAL_OR_CANCELLED"

    if api_stats_error or api_events_error or api_players_error:
        return "API_RESPONSE_ERROR"

    if api_stats_team_ids and not fixture_team_ids.issubset(api_stats_team_ids):
        return "API_TEAM_MAPPING_MISMATCH"
    if api_player_team_ids and not fixture_team_ids.issubset(api_player_team_ids):
        return "API_TEAM_MAPPING_MISMATCH"
    if any(team_id not in fixture_team_ids for team_id in api_event_card_team_ids):
        return "API_TEAM_MAPPING_MISMATCH"
    if any(team_id not in fixture_team_ids for team_id in api_player_card_team_ids):
        return "API_TEAM_MAPPING_MISMATCH"

    api_stats_has = any(
        bool(source.get("has_cards"))
        for source in (api_stats, api_half_ft_stats, api_half_1h_stats, api_half_2h_stats)
    )
    api_events_has = int(api_events.get("card_events") or 0) > 0
    api_players_has = bool(api_players.get("has_cards"))
    db_stats_has = any(has_numeric_cards(source) for source in (db_stats, db_stats_1h, db_stats_2h))
    db_events_has = int(db_events.get("card_events") or 0) > 0
    db_players_has = bool(db_players.get("has_cards"))
    db_facts_has = db_fact.get("cards_for") is not None
    api_has = api_stats_has or api_events_has or api_players_has

    if db_stats_has and not db_facts_has:
        return "STATS_HAVE_CARDS_BUT_FACTS_MISSING"
    if (api_events_has or db_events_has) and not db_facts_has:
        return "EVENTS_HAVE_CARDS_BUT_FACTS_MISSING"
    if db_players_has and not db_facts_has:
        return "PLAYER_STATS_HAVE_CARDS_BUT_FACTS_MISSING"
    if api_has and not db_facts_has:
        return "API_HAS_CARDS_BUT_DB_MISSING"
    if api_has and db_facts_has:
        return "API_HAS_CARDS_AND_DB_HAS_CARDS"
    if not api_has and db_facts_has:
        return "DB_HAS_CARDS_BUT_API_MISSING"
    return "API_MISSING_CARDS"


def build_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Cards API Coverage Audit",
        "",
        "## Request Budget",
        "",
        f"- planned_api_requests: {result['request_budget']['planned_api_requests']}",
        f"- used_api_requests: {result['request_budget']['used_api_requests']}",
        f"- max_api_requests: {result['request_budget']['max_api_requests']}",
        f"- over_request_budget: {result['request_budget']['over_request_budget']}",
        f"- mode: {result['request_budget']['mode']}",
        f"- include_half_statistics: {result['request_budget']['include_half_statistics']}",
        f"- include_players: {result['request_budget']['include_players']}",
        f"- include_player_card_tops: {result['request_budget'].get('include_player_card_tops', False)}",
        f"- dry_run: {result['request_budget']['dry_run']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in result["summary"].items():
        if isinstance(value, dict):
            continue
        lines.append(f"| {key} | {value} |")

    lines.extend(["", "## Classifications", "", "| Classification | Count |", "| --- | ---: |"])
    for key, value in result["summary"]["classification_counts"].items():
        lines.append(f"| {key} | {value} |")

    if result.get("league_player_card_tops"):
        lines.extend(
            [
                "",
                "## League Player Card Tops",
                "",
                "| league | top_yellow_rows | top_red_rows | yellow_error | red_error |",
                "| ---: | ---: | ---: | --- | --- |",
            ]
        )
        for row in result["league_player_card_tops"]:
            lines.append(
                "| {league_id} | {top_yellow_cards_rows} | {top_red_cards_rows} | {top_yellow_cards_error} | {top_red_cards_error} |".format(
                    **row
                )
            )

    lines.extend(
        [
            "",
            "## Fixture/Team Audit",
            "",
            "| classification | league | fixture_id | status | team_id | team | bucket | hydrated_stats | hydrated_events | hydrated_players | api_stats | api_half_ft | api_events | api_players | db_stats | db_events | db_players | db_facts |",
            "| --- | ---: | ---: | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result["fixture_team_audits"]:
        lines.append(
            "| {classification} | {league_id} | {fixture_id} | {status_short} | {team_id} | {team_name} | {sample_bucket} | {hydrated_statistics} | {hydrated_events} | {hydrated_players} | {api_stats_total_cards} | {api_half_ft_total_cards} | {api_event_card_events} | {api_player_total_cards} | {db_stats_cards} | {db_event_card_events} | {db_player_total_cards} | {db_fact_cards_for} |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


async def main_async() -> None:
    args = parse_args()
    leagues = parse_target_leagues(args.leagues) or DEFAULT_LEAGUES
    settings = load_settings()
    supabase = create_supabase_client(settings)

    facts = select_all(
        lambda: supabase.table("team_fixture_facts")
        .select(
            "fixture_id,team_id,opponent_team_id,league_id,season,played_at,venue_scope,"
            "yellow_cards_for,red_cards_for,cards_for,yellow_cards_against,red_cards_against,cards_against,total_cards"
        )
        .in_("league_id", list(leagues))
        .eq("season", args.season)
        .order("league_id", desc=False)
        .order("played_at", desc=False)
    )
    if args.team_id is not None:
        facts = [row for row in facts if int(row["team_id"]) == args.team_id]
    if not facts:
        raise RuntimeError("No team_fixture_facts rows found for the requested scope.")

    fixture_plan = build_fixture_plan(
        facts,
        leagues=leagues,
        season=args.season,
        mode=args.mode,
        fixtures_per_league=args.fixtures_per_league,
        missing_ratio=args.missing_ratio,
        explicit_fixture_ids=tuple(args.fixture_ids or ()),
    )
    fixture_ids = sorted({pick.fixture_id for pick in fixture_plan})
    endpoints_per_fixture = 2
    if args.include_half_statistics:
        endpoints_per_fixture += 1
    if args.include_players:
        endpoints_per_fixture += 1
    league_level_requests = len(leagues) * 2 if args.include_player_card_tops else 0
    planned_requests = (len(fixture_ids) * endpoints_per_fixture) + league_level_requests
    over_request_budget = planned_requests > args.max_api_requests

    facts_by_fixture_team = {
        (int(row["fixture_id"]), int(row["team_id"])): row
        for row in facts
        if row.get("fixture_id") is not None and row.get("team_id") is not None
    }
    request_budget = {
        "max_api_requests": args.max_api_requests,
        "planned_api_requests": planned_requests,
        "used_api_requests": 0,
        "fixtures_planned": len(fixture_ids),
        "endpoints_per_fixture": endpoints_per_fixture,
        "league_level_requests": league_level_requests,
        "mode": args.mode,
        "include_half_statistics": bool(args.include_half_statistics),
        "include_players": bool(args.include_players),
        "include_player_card_tops": bool(args.include_player_card_tops),
        "over_request_budget": over_request_budget,
        "dry_run": bool(args.dry_run),
    }

    if args.dry_run:
        planned_fixture_id_set = set(fixture_ids)
        fixture_team_rows_planned = sum(
            1 for fixture_id, _team_id in facts_by_fixture_team if fixture_id in planned_fixture_id_set
        )
        result = {
            "request_budget": request_budget,
            "fixture_plan": [pick.__dict__ for pick in fixture_plan],
            "summary": {
                "leagues": list(leagues),
                "season": args.season,
                "fixtures_planned": len(fixture_ids),
                "fixture_team_rows_planned": fixture_team_rows_planned,
                "classification_counts": {name: 0 for name in CLASSIFICATIONS},
            },
            "league_player_card_tops": [],
            "fixture_team_audits": [],
        }
        print(json.dumps({"request_budget": request_budget, "summary": result["summary"]}, indent=2, ensure_ascii=False))
        if args.write_files:
            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            json_path = output_dir / "result_check_cards_api_coverage.json"
            md_path = output_dir / "result_check_cards_api_coverage.md"
            json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            md_path.write_text(build_markdown(result), encoding="utf-8")
            print(f"Wrote {json_path}")
            print(f"Wrote {md_path}")
        return

    if over_request_budget:
        raise RuntimeError(
            f"Planned API requests={planned_requests} exceeds --max-api-requests={args.max_api_requests}. "
            "This rigorous audit should wait for a larger API-Football quota, or rerun only after explicitly "
            "choosing --mode sample for a smoke test."
        )

    summary_rows = select_in_chunks(
        supabase,
        "fixture_team_summary",
        "fixture_id,date,status_short,home_team_id,home_team_name,away_team_id,away_team_name,home_cards,away_cards",
        in_column="fixture_id",
        values=fixture_ids,
    )
    summary_by_fixture = {int(row["fixture_id"]): row for row in summary_rows}

    fixture_rows = select_in_chunks(
        supabase,
        "fixtures",
        "id,date,status_short,league_id,season,hydrated_statistics,hydrated_events,hydrated_players,hydrated_lineups",
        in_column="id",
        values=fixture_ids,
    )
    fixture_by_id = {int(row["id"]): row for row in fixture_rows}

    db_stat_rows = select_in_chunks(
        supabase,
        "fixture_statistics",
        "fixture_id,team_id,period,yellow_cards,red_cards,cards,booking_points",
        in_column="fixture_id",
        values=fixture_ids,
    )
    db_stats_by_fixture_team_period: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in db_stat_rows:
        if row.get("team_id") is None:
            continue
        db_stats_by_fixture_team_period[(int(row["fixture_id"]), int(row["team_id"]), str(row.get("period") or "FT"))] = row

    db_event_rows = select_in_chunks(
        supabase,
        "fixture_events",
        "fixture_id,team_id,type,detail,elapsed,extra_time,comments",
        in_column="fixture_id",
        values=fixture_ids,
    )
    db_events_by_fixture: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in db_event_rows:
        db_events_by_fixture[int(row["fixture_id"])].append(row)

    db_player_rows: list[dict[str, Any]] = []
    if args.include_players:
        db_player_rows = select_in_chunks(
            supabase,
            "player_fixture_stats",
            "fixture_id,team_id,player_id,yellow_cards,red_cards",
            in_column="fixture_id",
            values=fixture_ids,
        )
    db_players_by_fixture: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in db_player_rows:
        db_players_by_fixture[int(row["fixture_id"])].append(row)

    api_stats_payloads: dict[int, dict[str, Any] | None] = {}
    api_half_payloads: dict[int, dict[str, Any] | None] = {}
    api_events_payloads: dict[int, dict[str, Any] | None] = {}
    api_players_payloads: dict[int, dict[str, Any] | None] = {}
    api_player_card_top_payloads: dict[tuple[int, str], dict[str, Any] | None] = {}

    if not args.dry_run:
        async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
            if args.include_player_card_tops:
                for league_id in leagues:
                    for endpoint in ("players/topyellowcards", "players/topredcards"):
                        if request_budget["used_api_requests"] + 1 > args.max_api_requests:
                            raise RuntimeError("API request budget exhausted before completing league card-top checks.")
                        api_player_card_top_payloads[(league_id, endpoint)] = await api_client.fetch(
                            endpoint,
                            {"league": league_id, "season": args.season},
                            retries=1,
                        )
                        request_budget["used_api_requests"] += 1

            for fixture_id in fixture_ids:
                if request_budget["used_api_requests"] + endpoints_per_fixture > args.max_api_requests:
                    raise RuntimeError("API request budget exhausted before completing fixture plan.")
                api_stats_payloads[fixture_id] = await api_client.fetch(
                    "fixtures/statistics",
                    {"fixture": fixture_id},
                    retries=1,
                )
                request_budget["used_api_requests"] += 1
                if args.include_half_statistics:
                    api_half_payloads[fixture_id] = await api_client.fetch(
                        "fixtures/statistics",
                        {"fixture": fixture_id, "half": "true"},
                        retries=1,
                    )
                    request_budget["used_api_requests"] += 1
                api_events_payloads[fixture_id] = await api_client.fetch(
                    "fixtures/events",
                    {"fixture": fixture_id},
                    retries=1,
                )
                request_budget["used_api_requests"] += 1
                if args.include_players:
                    api_players_payloads[fixture_id] = await api_client.fetch(
                        "fixtures/players",
                        {"fixture": fixture_id},
                        retries=1,
                    )
                    request_budget["used_api_requests"] += 1

    fixture_team_audits: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    fixture_pick_by_id = {pick.fixture_id: pick for pick in fixture_plan}

    for fixture_id in fixture_ids:
        summary = summary_by_fixture.get(fixture_id, {})
        fixture_row = fixture_by_id.get(fixture_id, {})
        status_short = fixture_row.get("status_short") or summary.get("status_short")
        fixture_team_ids = {
            parse_optional_int(summary.get("home_team_id")),
            parse_optional_int(summary.get("away_team_id")),
        }
        fixture_team_ids = {team_id for team_id in fixture_team_ids if team_id is not None}
        if not fixture_team_ids:
            fixture_team_ids = {
                int(row["team_id"])
                for (row_fixture_id, _), row in facts_by_fixture_team.items()
                if row_fixture_id == fixture_id
            }

        api_stats, api_stats_error = extract_api_statistics(api_stats_payloads.get(fixture_id))
        api_half_ft_stats: dict[int, dict[str, Any]] = {}
        api_half_1h_stats: dict[int, dict[str, Any]] = {}
        api_half_2h_stats: dict[int, dict[str, Any]] = {}
        api_half_error = None
        if args.include_half_statistics:
            api_half_payload = api_half_payloads.get(fixture_id)
            api_half_ft_stats, api_half_ft_error = extract_api_statistics(api_half_payload, "statistics")
            api_half_1h_stats, api_half_1h_error = extract_api_statistics(api_half_payload, "statistics_1h")
            api_half_2h_stats, api_half_2h_error = extract_api_statistics(api_half_payload, "statistics_2h")
            half_errors = [error for error in (api_half_ft_error, api_half_1h_error, api_half_2h_error) if error]
            api_half_error = "; ".join(dict.fromkeys(half_errors)) if half_errors else None
        api_events, api_events_error = extract_api_events(api_events_payloads.get(fixture_id))
        api_players, api_players_error = ({}, None)
        if args.include_players:
            api_players, api_players_error = extract_api_players(api_players_payloads.get(fixture_id))
        api_stats_team_ids = (
            set(api_stats.keys())
            | set(api_half_ft_stats.keys())
            | set(api_half_1h_stats.keys())
            | set(api_half_2h_stats.keys())
        )
        api_event_card_team_ids = set(api_events.keys())
        api_player_team_ids = set(api_players.keys())
        api_player_card_team_ids = {
            api_team_id for api_team_id, api_team in api_players.items() if api_team.get("has_cards")
        }
        api_combined_error = api_stats_error or api_half_error

        db_events = db_event_counts(db_events_by_fixture.get(fixture_id, []))
        db_players = db_player_card_counts(db_players_by_fixture.get(fixture_id, []))
        pick = fixture_pick_by_id[fixture_id]

        for team_id in sorted(fixture_team_ids):
            fact = facts_by_fixture_team.get((fixture_id, team_id), {})
            db_stat_ft = db_stats_by_fixture_team_period.get((fixture_id, team_id, "FT"), {})
            db_stat_1h = db_stats_by_fixture_team_period.get((fixture_id, team_id, "1H"), {})
            db_stat_2h = db_stats_by_fixture_team_period.get((fixture_id, team_id, "2H"), {})
            db_stats_cards = safe_sum(
                parse_optional_int(db_stat_ft.get("yellow_cards")),
                parse_optional_int(db_stat_ft.get("red_cards")),
            )
            db_stats_1h_cards = safe_sum(
                parse_optional_int(db_stat_1h.get("yellow_cards")),
                parse_optional_int(db_stat_1h.get("red_cards")),
            )
            db_stats_2h_cards = safe_sum(
                parse_optional_int(db_stat_2h.get("yellow_cards")),
                parse_optional_int(db_stat_2h.get("red_cards")),
            )
            api_stat = api_stats.get(team_id, {})
            api_half_ft_stat = api_half_ft_stats.get(team_id, {})
            api_half_1h_stat = api_half_1h_stats.get(team_id, {})
            api_half_2h_stat = api_half_2h_stats.get(team_id, {})
            api_event = api_events.get(team_id, {})
            api_player = api_players.get(team_id, {})
            db_event = db_events.get(team_id, {})
            db_player = db_players.get(team_id, {})
            classification = classify_row(
                fixture_is_final=is_final_status(status_short),
                api_stats_error=api_combined_error,
                api_events_error=api_events_error,
                api_players_error=api_players_error,
                api_stats_team_ids=api_stats_team_ids,
                api_event_card_team_ids=api_event_card_team_ids,
                api_player_team_ids=api_player_team_ids,
                api_player_card_team_ids=api_player_card_team_ids,
                fixture_team_ids=fixture_team_ids,
                team_id=team_id,
                api_stats=api_stat,
                api_half_ft_stats=api_half_ft_stat,
                api_half_1h_stats=api_half_1h_stat,
                api_half_2h_stats=api_half_2h_stat,
                api_events=api_event,
                api_players=api_player,
                db_stats=db_stat_ft,
                db_stats_1h=db_stat_1h,
                db_stats_2h=db_stat_2h,
                db_events=db_event,
                db_players=db_player,
                db_fact=fact,
            )
            classification_counts[classification] += 1
            team_name = None
            if team_id == summary.get("home_team_id"):
                team_name = summary.get("home_team_name")
            elif team_id == summary.get("away_team_id"):
                team_name = summary.get("away_team_name")

            fixture_team_audits.append(
                {
                    "classification": classification,
                    "league_id": pick.league_id,
                    "season": pick.season,
                    "fixture_id": fixture_id,
                    "date": fixture_row.get("date") or summary.get("date") or pick.played_at,
                    "status_short": status_short,
                    "fixture_is_final": is_final_status(status_short),
                    "hydrated_statistics": fixture_row.get("hydrated_statistics"),
                    "hydrated_events": fixture_row.get("hydrated_events"),
                    "hydrated_players": fixture_row.get("hydrated_players"),
                    "hydrated_lineups": fixture_row.get("hydrated_lineups"),
                    "sample_bucket": pick.sample_bucket,
                    "team_id": team_id,
                    "team_name": team_name,
                    "fixture_team_ids": sorted(fixture_team_ids),
                    "api_stats_team_ids": sorted(api_stats_team_ids),
                    "api_event_card_team_ids": sorted(api_event_card_team_ids),
                    "api_player_team_ids": sorted(api_player_team_ids),
                    "api_player_card_team_ids": sorted(api_player_card_team_ids),
                    "api_stats_error": api_combined_error,
                    "api_events_error": api_events_error,
                    "api_players_error": api_players_error,
                    "api_stats_yellow_cards": api_stat.get("yellow_cards"),
                    "api_stats_red_cards": api_stat.get("red_cards"),
                    "api_stats_cards_field": api_stat.get("cards"),
                    "api_stats_total_cards": api_stat.get("total_cards"),
                    "api_stats_card_items": api_stat.get("card_items", []),
                    "api_stats_card_stat_types_seen": [
                        stat_type for stat_type in api_stat.get("stat_types", []) if "card" in normalize_token(stat_type)
                    ],
                    "api_half_ft_total_cards": api_half_ft_stat.get("total_cards"),
                    "api_half_1h_total_cards": api_half_1h_stat.get("total_cards"),
                    "api_half_2h_total_cards": api_half_2h_stat.get("total_cards"),
                    "api_event_card_events": api_event.get("card_events", 0),
                    "api_event_yellow_card_events": api_event.get("yellow_card_events", 0),
                    "api_event_red_card_events": api_event.get("red_card_events", 0),
                    "api_event_second_yellow_card_events": api_event.get("second_yellow_card_events", 0),
                    "api_event_items": api_event.get("items", []),
                    "api_player_yellow_cards": api_player.get("yellow_cards"),
                    "api_player_red_cards": api_player.get("red_cards"),
                    "api_player_total_cards": api_player.get("total_cards"),
                    "api_players_with_cards": api_player.get("players_with_cards", []),
                    "db_stats_yellow_cards": db_stat_ft.get("yellow_cards"),
                    "db_stats_red_cards": db_stat_ft.get("red_cards"),
                    "db_stats_cards": db_stats_cards,
                    "db_stats_1h_cards": db_stats_1h_cards,
                    "db_stats_2h_cards": db_stats_2h_cards,
                    "db_event_card_events": db_event.get("card_events", 0),
                    "db_event_yellow_card_events": db_event.get("yellow_card_events", 0),
                    "db_event_red_card_events": db_event.get("red_card_events", 0),
                    "db_event_second_yellow_card_events": db_event.get("second_yellow_card_events", 0),
                    "db_player_yellow_cards": db_player.get("yellow_cards"),
                    "db_player_red_cards": db_player.get("red_cards"),
                    "db_player_total_cards": db_player.get("total_cards"),
                    "db_players_with_cards": db_player.get("players_with_cards", []),
                    "db_fact_yellow_cards_for": fact.get("yellow_cards_for"),
                    "db_fact_red_cards_for": fact.get("red_cards_for"),
                    "db_fact_cards_for": fact.get("cards_for"),
                    "db_fact_yellow_cards_against": fact.get("yellow_cards_against"),
                    "db_fact_red_cards_against": fact.get("red_cards_against"),
                    "db_fact_cards_against": fact.get("cards_against"),
                    "db_fact_total_cards": fact.get("total_cards"),
                }
            )

    summary = {
        "leagues": list(leagues),
        "season": args.season,
        "fixtures_checked": len(fixture_ids) if not args.dry_run else 0,
        "fixtures_planned": len(fixture_ids),
        "fixture_team_rows_checked": len(fixture_team_audits) if not args.dry_run else 0,
        "final_fixture_team_rows_checked": sum(1 for row in fixture_team_audits if row["fixture_is_final"]),
        "non_final_fixture_team_rows_checked": sum(1 for row in fixture_team_audits if not row["fixture_is_final"]),
        "status_counts": dict(Counter(str(row["status_short"] or "UNKNOWN") for row in fixture_team_audits)),
        "hydrated_statistics_true": sum(1 for row in fixture_team_audits if row["hydrated_statistics"] is True),
        "hydrated_events_true": sum(1 for row in fixture_team_audits if row["hydrated_events"] is True),
        "hydrated_players_true": sum(1 for row in fixture_team_audits if row["hydrated_players"] is True),
        "api_stats_has_cards": sum(1 for row in fixture_team_audits if row["api_stats_total_cards"] is not None),
        "api_half_ft_has_cards": sum(1 for row in fixture_team_audits if row["api_half_ft_total_cards"] is not None),
        "api_half_1h_has_cards": sum(1 for row in fixture_team_audits if row["api_half_1h_total_cards"] is not None),
        "api_half_2h_has_cards": sum(1 for row in fixture_team_audits if row["api_half_2h_total_cards"] is not None),
        "api_events_has_cards": sum(1 for row in fixture_team_audits if row["api_event_card_events"] > 0),
        "api_players_has_cards": sum(1 for row in fixture_team_audits if row["api_player_total_cards"] not in (None, 0)),
        "db_stats_has_cards": sum(1 for row in fixture_team_audits if row["db_stats_cards"] is not None),
        "db_stats_1h_has_cards": sum(1 for row in fixture_team_audits if row["db_stats_1h_cards"] is not None),
        "db_stats_2h_has_cards": sum(1 for row in fixture_team_audits if row["db_stats_2h_cards"] is not None),
        "db_events_has_cards": sum(1 for row in fixture_team_audits if row["db_event_card_events"] > 0),
        "db_players_has_cards": sum(1 for row in fixture_team_audits if row["db_player_total_cards"] not in (None, 0)),
        "db_facts_has_cards": sum(1 for row in fixture_team_audits if row["db_fact_cards_for"] is not None),
        "classification_counts": {name: classification_counts[name] for name in CLASSIFICATIONS},
    }
    league_player_card_tops = []
    if args.include_player_card_tops:
        for league_id in leagues:
            yellow_summary = summarize_player_card_top_payload(
                api_player_card_top_payloads.get((league_id, "players/topyellowcards"))
            )
            red_summary = summarize_player_card_top_payload(
                api_player_card_top_payloads.get((league_id, "players/topredcards"))
            )
            league_player_card_tops.append(
                {
                    "league_id": league_id,
                    "season": args.season,
                    "top_yellow_cards_rows": yellow_summary["rows"],
                    "top_yellow_cards_error": yellow_summary["error"],
                    "top_yellow_cards_sample": yellow_summary.get("sample", []),
                    "top_red_cards_rows": red_summary["rows"],
                    "top_red_cards_error": red_summary["error"],
                    "top_red_cards_sample": red_summary.get("sample", []),
                }
            )
    result = {
        "request_budget": request_budget,
        "fixture_plan": [pick.__dict__ for pick in fixture_plan],
        "summary": summary,
        "league_player_card_tops": league_player_card_tops,
        "fixture_team_audits": fixture_team_audits,
    }

    print(json.dumps({"request_budget": request_budget, "summary": summary}, indent=2, ensure_ascii=False))

    if args.write_files:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "result_check_cards_api_coverage.json"
        md_path = output_dir / "result_check_cards_api_coverage.md"
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(build_markdown(result), encoding="utf-8")
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
