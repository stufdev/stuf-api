from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pipeline_core import (
    ApiFootballClient,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_iso_datetime,
    parse_optional_int,
)


LOGGER = configure_logging("stuf.audit.worldcup-2026")
WORLD_CUP_LEAGUE_ID = 1
DEFAULT_SEASON = 2026
EXPECTED_WORLD_CUP_2026_FIXTURES = 104
FINAL_STATUSES = {"FT", "AET", "PEN"}
UPCOMING_STATUSES = {"NS", "TBD"}
PLACEHOLDER_PATTERNS = (
    "tbd",
    "to be decided",
    "winner",
    "runner-up",
    "runner up",
    "play-off",
    "playoff",
    "group ",
)


@dataclass(frozen=True)
class ApiCall:
    endpoint: str
    params: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only World Cup 2026 audit for API-Football and STUF DB. "
            "Writes local JSON/MD reports only; never mutates remote data."
        )
    )
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--league", type=int, default=WORLD_CUP_LEAGUE_ID)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--team-check-limit", type=int, default=8)
    parser.add_argument("--history-team-limit", type=int, default=5)
    parser.add_argument("--history-months", type=int, default=24)
    parser.add_argument(
        "--history-seasons",
        default=None,
        help="CSV season list for national-team history. Default: season, season-1, season-2.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "codex" / "reports"),
    )
    return parser.parse_args()


def response_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload or not isinstance(payload.get("response"), list):
        return []
    return [item for item in payload["response"] if isinstance(item, dict)]


def api_errors(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return "no payload"
    return payload.get("errors")


def parse_api_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    response = payload.get("response") if payload else None
    if not isinstance(response, dict):
        return {"raw_response": response, "errors": api_errors(payload)}
    requests = response.get("requests") or {}
    subscription = response.get("subscription") or {}
    account = response.get("account") or {}
    return {
        "account": account.get("firstname") or account.get("email") or account,
        "plan_active": subscription.get("active"),
        "plan_name": subscription.get("plan"),
        "requests_current": requests.get("current"),
        "requests_limit_day": requests.get("limit_day"),
    }


def normalized_name(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def is_placeholder_team(team: dict[str, Any] | None) -> bool:
    if not team:
        return True
    team_id = parse_optional_int(team.get("id"))
    name = normalized_name(team.get("name"))
    if team_id is None or team_id <= 0:
        return True
    return any(pattern in name for pattern in PLACEHOLDER_PATTERNS)


def fixture_status(fixture_row: dict[str, Any]) -> str | None:
    fixture = fixture_row.get("fixture") or {}
    status = fixture.get("status") or {}
    return status.get("short")


def fixture_date(fixture_row: dict[str, Any]) -> str | None:
    fixture = fixture_row.get("fixture") or {}
    return fixture.get("date")


def fixture_id(fixture_row: dict[str, Any]) -> int | None:
    fixture = fixture_row.get("fixture") or {}
    return parse_optional_int(fixture.get("id"))


def fixture_teams(fixture_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    teams = fixture_row.get("teams") or {}
    return teams.get("home") or {}, teams.get("away") or {}


def fixture_league(fixture_row: dict[str, Any]) -> dict[str, Any]:
    return fixture_row.get("league") or {}


def collect_fixture_team_rows(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    teams_by_id: dict[int, dict[str, Any]] = {}
    for row in fixtures:
        for team in fixture_teams(row):
            team_id = parse_optional_int(team.get("id"))
            if team_id is None or team_id <= 0:
                continue
            teams_by_id[team_id] = {
                "id": team_id,
                "name": team.get("name"),
                "is_placeholder": is_placeholder_team(team),
            }
    return sorted(teams_by_id.values(), key=lambda item: (item["name"] or "", item["id"]))


def summarize_fixture_calendar(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(fixture_status(row) or "null" for row in fixtures)
    dates = sorted(
        date
        for date in (fixture_date(row) for row in fixtures)
        if date
    )
    team_rows = collect_fixture_team_rows(fixtures)
    real_team_rows = [row for row in team_rows if not row["is_placeholder"]]
    placeholder_team_rows = [row for row in team_rows if row["is_placeholder"]]
    return {
        "fixture_count": len(fixtures),
        "expected_full_tournament_fixtures": EXPECTED_WORLD_CUP_2026_FIXTURES,
        "missing_or_unpublished_fixtures": max(0, EXPECTED_WORLD_CUP_2026_FIXTURES - len(fixtures)),
        "status_counts": dict(sorted(statuses.items())),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "distinct_teams": len(team_rows),
        "real_teams": len(real_team_rows),
        "placeholder_teams": len(placeholder_team_rows),
        "sample_real_teams": real_team_rows[:12],
        "sample_placeholder_teams": placeholder_team_rows[:12],
    }


def summarize_league_item(item: dict[str, Any]) -> dict[str, Any]:
    league = item.get("league") or {}
    country = item.get("country") or {}
    seasons = item.get("seasons") or []
    target_season = next(
        (season for season in seasons if parse_optional_int(season.get("year")) == DEFAULT_SEASON),
        None,
    )
    return {
        "league_id": league.get("id"),
        "name": league.get("name"),
        "type": league.get("type"),
        "country": country.get("name"),
        "season_years": [season.get("year") for season in seasons],
        "target_season": target_season,
        "raw": item,
    }


def summarize_standings(payload: dict[str, Any] | None) -> dict[str, Any]:
    items = response_items(payload)
    groups = 0
    teams = 0
    for league_item in items:
        league = league_item.get("league") or {}
        standings = league.get("standings") or []
        groups += len(standings)
        for group_rows in standings:
            if isinstance(group_rows, list):
                teams += len(group_rows)
    return {
        "response_items": len(items),
        "groups": groups,
        "teams": teams,
        "errors": api_errors(payload),
    }


def summarize_team_payload(team_id: int, payload: dict[str, Any] | None) -> dict[str, Any]:
    items = response_items(payload)
    team = (items[0].get("team") if items else {}) or {}
    return {
        "team_id": team_id,
        "api_name": team.get("name"),
        "national": team.get("national"),
        "country": team.get("country"),
        "errors": api_errors(payload),
    }


def endpoint_coverage_summary(endpoint: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    items = response_items(payload)
    return {
        "endpoint": endpoint,
        "response_count": len(items),
        "has_response": bool(items),
        "errors": api_errors(payload),
        "sample_keys": sorted(items[0].keys()) if items else [],
        "sample": items[0] if items else None,
    }


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
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ids = sorted({int(value) for value in values if value is not None})
    for id_chunk in chunked(ids, chunk_size):
        rows.extend(
            select_all(
                lambda id_chunk=id_chunk: supabase.table(table)
                .select(columns)
                .in_(in_column, list(id_chunk))
            )
        )
    return rows


def build_db_audit(supabase: Any, *, league_id: int, season: int) -> dict[str, Any]:
    league_rows = select_all(
        lambda: supabase.table("leagues")
        .select("id,name,type,country_name")
        .eq("id", league_id)
    )
    season_rows = select_all(
        lambda: supabase.table("league_seasons")
        .select("league_id,season,start_date,end_date,is_current")
        .eq("league_id", league_id)
        .order("season", desc=False)
    )
    supported_rows = select_all(
        lambda: supabase.table("supported_leagues")
        .select("league_id,season")
        .eq("league_id", league_id)
        .eq("season", season)
    )
    fixture_rows = select_all(
        lambda: supabase.table("fixtures")
        .select("id,league_id,season,status_short,date,home_team_id,away_team_id")
        .eq("league_id", league_id)
        .order("season", desc=False)
        .order("date", desc=False)
    )
    fixture_status_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in fixture_rows:
        fixture_status_counts[str(row.get("season"))][str(row.get("status_short") or "null")] += 1

    upcoming_2026 = [
        row
        for row in fixture_rows
        if parse_optional_int(row.get("season")) == season
        and row.get("status_short") in UPCOMING_STATUSES
    ]

    national_team_rows = select_all(
        lambda: supabase.table("teams")
        .select("id,name,national")
        .eq("national", True)
        .order("name", desc=False)
    )
    national_team_ids = [int(row["id"]) for row in national_team_rows if row.get("id") is not None]

    national_fixture_rows: list[dict[str, Any]] = []
    for side_column in ("home_team_id", "away_team_id"):
        national_fixture_rows.extend(
            select_in_chunks(
                supabase,
                "fixtures",
                "id,league_id,season,status_short,home_team_id,away_team_id",
                in_column=side_column,
                values=national_team_ids,
            )
        )
    unique_national_fixtures = {
        int(row["id"]): row
        for row in national_fixture_rows
        if row.get("id") is not None
    }
    national_fixtures_by_league = Counter(
        int(row["league_id"])
        for row in unique_national_fixtures.values()
        if row.get("league_id") is not None
    )
    league_catalog_rows = select_in_chunks(
        supabase,
        "leagues",
        "id,name,type,country_name",
        in_column="id",
        values=national_fixtures_by_league.keys(),
    )
    league_catalog = {
        int(row["id"]): row
        for row in league_catalog_rows
        if row.get("id") is not None
    }

    player_stat_rows = select_in_chunks(
        supabase,
        "player_fixture_stats",
        "fixture_id,team_id,league_id,season",
        in_column="team_id",
        values=national_team_ids,
    )

    return {
        "wc_league_catalog_present": {
            "actual": len(league_rows),
            "status": "PRESENT" if league_rows else "ABSENT",
            "rows": league_rows,
        },
        "wc_league_seasons": season_rows,
        "wc_2026_supported": {
            "actual": len(supported_rows),
            "status": "PRESENT" if supported_rows else "ABSENT",
            "rows": supported_rows,
        },
        "wc_fixtures_by_status": {
            season_key: dict(sorted(status_counts.items()))
            for season_key, status_counts in sorted(fixture_status_counts.items())
        },
        "wc_2026_upcoming_fixtures": {
            "actual": len(upcoming_2026),
            "status": "PRESENT" if upcoming_2026 else "ABSENT",
        },
        "national_teams_in_db": {
            "actual": len(national_team_rows),
            "sample": national_team_rows[:20],
        },
        "national_competitions_in_db": [
            {
                "league_id": league_id_item,
                "fixture_count": count,
                "league_name": (league_catalog.get(league_id_item) or {}).get("name"),
                "type": (league_catalog.get(league_id_item) or {}).get("type"),
            }
            for league_id_item, count in national_fixtures_by_league.most_common(30)
        ],
        "national_team_player_stats": {
            "actual": len(player_stat_rows),
            "sample": player_stat_rows[:20],
        },
    }


def summarize_history_fixture(row: dict[str, Any], cutoff: datetime) -> dict[str, Any] | None:
    date_value = parse_iso_datetime(fixture_date(row))
    if date_value is None or date_value < cutoff:
        return None
    status = fixture_status(row)
    if status not in FINAL_STATUSES:
        return None
    league = fixture_league(row)
    home, away = fixture_teams(row)
    return {
        "fixture_id": fixture_id(row),
        "date": fixture_date(row),
        "status": status,
        "league_id": league.get("id"),
        "league_name": league.get("name"),
        "season": league.get("season"),
        "home_team_id": home.get("id"),
        "home_team": home.get("name"),
        "away_team_id": away.get("id"),
        "away_team": away.get("name"),
    }


def determine_calendar_gate(calendar: dict[str, Any]) -> str:
    if calendar["fixture_count"] <= 0:
        return "NO"
    if calendar["fixture_count"] < calendar.get("expected_full_tournament_fixtures", EXPECTED_WORLD_CUP_2026_FIXTURES):
        return "PARTIAL(incomplete_calendar)"
    if calendar["placeholder_teams"] > 0:
        return "PARTIAL(placeholders)"
    return "YES"


def determine_stats_gate(coverage: dict[str, Any] | None) -> str:
    if not coverage:
        return "NONE"
    values = [
        endpoint.get("has_response")
        for endpoint in coverage.get("endpoints", {}).values()
    ]
    if values and all(values):
        return "FULL"
    if any(values):
        return "PARTIAL"
    return "NONE"


def build_markdown_report(result: dict[str, Any]) -> str:
    api = result["api_audit"]
    calendar = api["world_cup_calendar"]
    gates = result["gates"]
    db = result["db_audit"]
    detail = api.get("played_fixture_detail_coverage")

    lines = [
        "# World Cup 2026 Audit Report",
        "",
        "Read-only audit for STUF World Cup 2026 readiness. No ingests, rebuilds, migrations, or remote writes were run.",
        "",
        "## Gate Verdicts",
        "",
        "| Gate | Verdict |",
        "| --- | --- |",
        f"| WC_2026_CALENDAR_AVAILABLE | {gates['WC_2026_CALENDAR_AVAILABLE']} |",
        f"| NATIONAL_STATS_COVERAGE | {gates['NATIONAL_STATS_COVERAGE']} |",
        "",
        "## API Quota Snapshot",
        "",
        "| Moment | Requests current | Daily limit | Plan active | Plan |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for key in ("start", "end"):
        status = api["quota"].get(key) or {}
        lines.append(
            f"| {key} | {status.get('requests_current')} | {status.get('requests_limit_day')} | "
            f"{status.get('plan_active')} | {status.get('plan_name')} |"
        )

    lines.extend([
        "",
        "## API League Confirmation",
        "",
        "| Source | league_id | name | type | country | season_2026_present |",
        "| --- | ---: | --- | --- | --- | --- |",
    ])
    league_by_id = api["league_by_id"]
    lines.append(
        "| /leagues?id=1 | {league_id} | {name} | {type} | {country} | {present} |".format(
            league_id=league_by_id.get("league_id"),
            name=league_by_id.get("name"),
            type=league_by_id.get("type"),
            country=league_by_id.get("country"),
            present=bool(league_by_id.get("target_season")),
        )
    )
    lines.extend([
        "",
        "### World Cup Search Candidates",
        "",
        "| league_id | name | type | country | seasons |",
        "| ---: | --- | --- | --- | --- |",
    ])
    for row in api["world_cup_search_candidates"][:20]:
        lines.append(
            f"| {row.get('league_id')} | {row.get('name')} | {row.get('type')} | "
            f"{row.get('country')} | {', '.join(str(item) for item in row.get('season_years', []))} |"
        )

    lines.extend([
        "",
        "## 2026 Fixture Calendar",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| fixtures | {calendar['fixture_count']} |",
        f"| expected full tournament fixtures | {calendar['expected_full_tournament_fixtures']} |",
        f"| missing/unpublished fixtures | {calendar['missing_or_unpublished_fixtures']} |",
        f"| distinct teams | {calendar['distinct_teams']} |",
        f"| real teams | {calendar['real_teams']} |",
        f"| placeholder teams | {calendar['placeholder_teams']} |",
        f"| date min | {calendar['date_min']} |",
        f"| date max | {calendar['date_max']} |",
        "",
        "Status breakdown:",
        "",
        "| status | count |",
        "| --- | ---: |",
    ])
    for status, count in calendar["status_counts"].items():
        lines.append(f"| {status} | {count} |")

    lines.extend([
        "",
        "## Standings",
        "",
        f"- response_items: {api['standings'].get('response_items')}",
        f"- groups: {api['standings'].get('groups')}",
        f"- teams: {api['standings'].get('teams')}",
        f"- errors: {api['standings'].get('errors')}",
        "",
        "## National Team Spot Checks",
        "",
        "| team_id | API name | national | country | errors |",
        "| ---: | --- | --- | --- | --- |",
    ])
    for row in api["team_spot_checks"]:
        lines.append(
            f"| {row.get('team_id')} | {row.get('api_name')} | {row.get('national')} | "
            f"{row.get('country')} | {row.get('errors')} |"
        )

    lines.extend([
        "",
        "## Played Fixture Coverage",
        "",
    ])
    if detail:
        selected = detail["selected_fixture"]
        lines.extend([
            f"- Fixture: {selected.get('fixture_id')} ({selected.get('home_team')} vs {selected.get('away_team')})",
            f"- League: {selected.get('league_name')} ({selected.get('league_id')})",
            f"- Date/status: {selected.get('date')} / {selected.get('status')}",
            "",
            "| Endpoint | response_count | has_response | errors | sample_keys |",
            "| --- | ---: | --- | --- | --- |",
        ])
        for endpoint, row in detail["endpoints"].items():
            lines.append(
                f"| {endpoint} | {row.get('response_count')} | {row.get('has_response')} | "
                f"{row.get('errors')} | {', '.join(row.get('sample_keys') or [])} |"
            )
    else:
        lines.append("- No finished national-team fixture was found in the sampled history window.")

    lines.extend([
        "",
        "## National-Team History Sources",
        "",
        "| team_id | team | finished fixtures last window | leagues |",
        "| ---: | --- | ---: | --- |",
    ])
    for row in api["national_history_samples"]:
        league_summary = ", ".join(
            f"{item['league_name']} ({item['league_id']}): {item['finished_count']}"
            for item in row["league_counts"]
        )
        lines.append(
            f"| {row.get('team_id')} | {row.get('team_name')} | "
            f"{row.get('finished_count')} | {league_summary} |"
        )

    lines.extend([
        "",
        "## DB Read-Only Audit",
        "",
        "| Check | Status/Actual | Notes |",
        "| --- | --- | --- |",
        f"| wc_league_catalog_present | {db['wc_league_catalog_present']['status']} / {db['wc_league_catalog_present']['actual']} | leagues.id=1 |",
        f"| wc_league_seasons | {len(db['wc_league_seasons'])} | seasons rows for league 1 |",
        f"| wc_2026_supported | {db['wc_2026_supported']['status']} / {db['wc_2026_supported']['actual']} | supported_leagues (1,2026) |",
        f"| wc_2026_upcoming_fixtures | {db['wc_2026_upcoming_fixtures']['status']} / {db['wc_2026_upcoming_fixtures']['actual']} | fixtures status NS/TBD |",
        f"| national_teams_in_db | {db['national_teams_in_db']['actual']} | teams.national=true |",
        f"| national_team_player_stats | {db['national_team_player_stats']['actual']} | player_fixture_stats for national teams |",
        "",
        "Top national competitions in DB:",
        "",
        "| league_id | name | type | fixtures |",
        "| ---: | --- | --- | ---: |",
    ])
    for row in db["national_competitions_in_db"][:20]:
        lines.append(
            f"| {row.get('league_id')} | {row.get('league_name')} | {row.get('type')} | {row.get('fixture_count')} |"
        )

    lines.extend([
        "",
        "## Notes",
        "",
        "- This report is evidence-gathering only. It does not authorize ingestion, product scope changes, schema changes, or rebuilds.",
        "- If calendar is partial because of placeholder teams, the next step is product gating, not blind ingestion.",
        "- If played fixture coverage is partial, STUF should not assume Player Props or market history coverage until targeted national-team samples are validated.",
        "",
    ])
    return "\n".join(lines)


async def run_api_audit(args: argparse.Namespace) -> dict[str, Any]:
    api_call_log: list[ApiCall] = []
    if args.history_seasons:
        history_seasons = [
            int(part.strip())
            for part in str(args.history_seasons).split(",")
            if part.strip()
        ]
    else:
        history_seasons = [args.season, args.season - 1, args.season - 2]

    async def fetch(api_client: ApiFootballClient, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        normalized_params = params or {}
        api_call_log.append(ApiCall(endpoint=endpoint, params=dict(normalized_params)))
        return await api_client.fetch(endpoint, normalized_params)

    async with ApiFootballClient(load_settings(), LOGGER, request_delay_seconds=args.request_delay) as api_client:
        status_start = parse_api_status(await fetch(api_client, "status"))
        league_payload = await fetch(api_client, "leagues", {"id": args.league})
        search_payload = await fetch(api_client, "leagues", {"search": "World Cup"})
        fixtures_payload = await fetch(api_client, "fixtures", {"league": args.league, "season": args.season})
        standings_payload = await fetch(api_client, "standings", {"league": args.league, "season": args.season})

        league_items = response_items(league_payload)
        league_by_id = summarize_league_item(league_items[0]) if league_items else {}
        search_candidates = [summarize_league_item(item) for item in response_items(search_payload)]
        fixtures = response_items(fixtures_payload)
        calendar = summarize_fixture_calendar(fixtures)
        standings = summarize_standings(standings_payload)

        fixture_teams_rows = collect_fixture_team_rows(fixtures)
        real_fixture_teams = [row for row in fixture_teams_rows if not row["is_placeholder"]]
        team_spot_checks: list[dict[str, Any]] = []
        for row in real_fixture_teams[: max(0, args.team_check_limit)]:
            payload = await fetch(api_client, "teams", {"id": row["id"]})
            team_spot_checks.append(summarize_team_payload(row["id"], payload))

        search_terms = (
            "World Cup",
            "World Cup Qualification",
            "Friendlies",
            "Nations League",
            "Euro Championship",
            "Copa America",
        )
        national_searches: dict[str, list[dict[str, Any]]] = {}
        for term in search_terms:
            payload = await fetch(api_client, "leagues", {"search": term})
            national_searches[term] = [
                summarize_league_item(item)
                for item in response_items(payload)
            ]

        cutoff = datetime.now(timezone.utc) - timedelta(days=30 * args.history_months)
        history_samples: list[dict[str, Any]] = []
        selected_finished_fixture: dict[str, Any] | None = None
        selected_finished_raw: dict[str, Any] | None = None
        for row in real_fixture_teams[: max(0, args.history_team_limit)]:
            season_payloads: list[dict[str, Any] | None] = []
            api_error_payloads: list[Any] = []
            all_fixture_rows: list[dict[str, Any]] = []
            for history_season in history_seasons:
                payload = await fetch(
                    api_client,
                    "fixtures",
                    {
                        "team": row["id"],
                        "season": history_season,
                    },
                )
                season_payloads.append(payload)
                api_error_payloads.append({"season": history_season, "errors": api_errors(payload)})
                all_fixture_rows.extend(response_items(payload))

            finished_rows = [
                summary
                for fixture_row in all_fixture_rows
                if (summary := summarize_history_fixture(fixture_row, cutoff)) is not None
            ]
            finished_rows.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
            league_counts: dict[int, dict[str, Any]] = {}
            raw_rows_by_fixture_id: dict[int, dict[str, Any]] = {
                int(fixture_id_value): fixture_row
                for fixture_row in all_fixture_rows
                if (fixture_id_value := fixture_id(fixture_row)) is not None
            }
            for item in finished_rows:
                league_id = parse_optional_int(item.get("league_id"))
                if league_id is None:
                    continue
                if league_id not in league_counts:
                    league_counts[league_id] = {
                        "league_id": league_id,
                        "league_name": item.get("league_name"),
                        "finished_count": 0,
                    }
                league_counts[league_id]["finished_count"] += 1

            if selected_finished_fixture is None and finished_rows:
                selected_finished_fixture = finished_rows[0]
                selected_finished_raw = raw_rows_by_fixture_id.get(int(finished_rows[0]["fixture_id"]))

            history_samples.append(
                {
                    "team_id": row["id"],
                    "team_name": row["name"],
                    "finished_count": len(finished_rows),
                    "league_counts": sorted(
                        league_counts.values(),
                        key=lambda item: (-item["finished_count"], str(item["league_name"] or "")),
                    ),
                    "sample_fixtures": finished_rows[:8],
                    "api_errors": api_error_payloads,
                }
            )

        played_fixture_detail_coverage: dict[str, Any] | None = None
        if selected_finished_fixture:
            selected_fixture_id = selected_finished_fixture["fixture_id"]
            endpoint_payloads = {
                "fixtures/statistics": await fetch(api_client, "fixtures/statistics", {"fixture": selected_fixture_id}),
                "fixtures/events": await fetch(api_client, "fixtures/events", {"fixture": selected_fixture_id}),
                "fixtures/players": await fetch(api_client, "fixtures/players", {"fixture": selected_fixture_id}),
                "fixtures/lineups": await fetch(api_client, "fixtures/lineups", {"fixture": selected_fixture_id}),
            }
            if selected_finished_raw:
                home, away = fixture_teams(selected_finished_raw)
                selected_finished_fixture = {
                    **selected_finished_fixture,
                    "home_team": home.get("name"),
                    "away_team": away.get("name"),
                }
            played_fixture_detail_coverage = {
                "selected_fixture": selected_finished_fixture,
                "endpoints": {
                    endpoint: endpoint_coverage_summary(endpoint, payload)
                    for endpoint, payload in endpoint_payloads.items()
                },
            }

        status_end = parse_api_status(await fetch(api_client, "status"))

    return {
        "quota": {"start": status_start, "end": status_end},
        "league_by_id": league_by_id,
        "world_cup_search_candidates": search_candidates,
        "world_cup_calendar": calendar,
        "standings": standings,
        "team_spot_checks": team_spot_checks,
        "national_searches": national_searches,
        "national_history_samples": history_samples,
        "played_fixture_detail_coverage": played_fixture_detail_coverage,
        "api_calls": [
            {"endpoint": call.endpoint, "params": call.params}
            for call in api_call_log
        ],
    }


async def main_async() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)

    api_audit = await run_api_audit(args)
    db_audit = build_db_audit(supabase, league_id=args.league, season=args.season)
    gates = {
        "WC_2026_CALENDAR_AVAILABLE": determine_calendar_gate(api_audit["world_cup_calendar"]),
        "NATIONAL_STATS_COVERAGE": determine_stats_gate(api_audit.get("played_fixture_detail_coverage")),
    }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "league": args.league,
            "season": args.season,
            "request_delay": args.request_delay,
            "team_check_limit": args.team_check_limit,
            "history_team_limit": args.history_team_limit,
            "history_months": args.history_months,
            "history_seasons": args.history_seasons,
        },
        "gates": gates,
        "api_audit": api_audit,
        "db_audit": db_audit,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "WORLDCUP_2026_AUDIT.json"
    md_path = output_dir / "WORLDCUP_2026_REPORT.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(build_markdown_report(result), encoding="utf-8")

    print(json.dumps(
        {
            "gates": gates,
            "fixtures": api_audit["world_cup_calendar"]["fixture_count"],
            "expected_full_tournament_fixtures": api_audit["world_cup_calendar"]["expected_full_tournament_fixtures"],
            "missing_or_unpublished_fixtures": api_audit["world_cup_calendar"]["missing_or_unpublished_fixtures"],
            "status_counts": api_audit["world_cup_calendar"]["status_counts"],
            "real_teams": api_audit["world_cup_calendar"]["real_teams"],
            "placeholder_teams": api_audit["world_cup_calendar"]["placeholder_teams"],
            "national_stats_coverage": gates["NATIONAL_STATS_COVERAGE"],
            "api_calls": len(api_audit["api_calls"]),
            "report": str(md_path),
            "json": str(json_path),
        },
        indent=2,
        ensure_ascii=False,
    ))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
