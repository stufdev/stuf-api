from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from pipeline_core import (
    ApiFootballClient,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_optional_int,
)


LOGGER = configure_logging("stuf.audit.offsides-api-coverage")
SCOPES = ("overall", "home", "away")
API_STAT_SECTIONS = (
    ("default_ft", "statistics"),
    ("half_ft", "statistics"),
    ("half_1h", "statistics_1h"),
    ("half_2h", "statistics_2h"),
)
CLASSIFICATIONS = (
    "API_HAS_OFFSIDES_AND_DB_HAS_OFFSIDES",
    "API_HAS_OFFSIDES_BUT_DB_MISSING",
    "API_MISSING_OFFSIDES",
    "API_TEAM_MAPPING_MISMATCH",
    "DB_HAS_OFFSIDES_BUT_API_MISSING",
    "API_RESPONSE_ERROR",
)


@dataclass(frozen=True)
class MissingCombo:
    league_id: int
    season: int
    team_id: int
    team_name: str | None
    scope: str
    missing_markets: tuple[str, ...]
    missing_rows: int
    valid_fact_sample: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit for Offsides coverage: compares API-Football "
            "/fixtures/statistics raw data against STUF stored statistics/facts."
        )
    )
    parser.add_argument("--league", type=int, default=61, help="league_id to audit. Default: 61.")
    parser.add_argument("--season", type=int, default=2025, help="Season YYYY. Default: 2025.")
    parser.add_argument("--team-id", type=int, help="Optional team_id filter.")
    parser.add_argument("--scope", choices=SCOPES, help="Optional scope filter.")
    parser.add_argument("--limit", type=int, help="Optional max fixtures to call in API-Football.")
    parser.add_argument("--request-delay", type=float, default=1.0, help="Delay between API requests.")
    parser.add_argument(
        "--write-files",
        action="store_true",
        help="Write result_check_offsides_api_coverage.json and .md.",
    )
    parser.add_argument("--output-dir", default=".", help="Output directory when --write-files is set.")
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


def is_offsides_stat_type(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())
    return normalized in {"offside", "offsides"}


def api_stat_types(team_stat: dict[str, Any], statistics_key: str) -> list[str]:
    return [
        str(item.get("type"))
        for item in (team_stat.get(statistics_key) or [])
        if item.get("type") is not None
    ]


def extract_api_team_stats(
    payload: dict[str, Any] | None,
    statistics_key: str = "statistics",
) -> tuple[dict[int, dict[str, Any]], str | None]:
    if payload is None:
        return {}, "API client returned no payload."
    errors = payload.get("errors")
    if errors:
        return {}, f"API payload errors: {errors}"
    if not isinstance(payload.get("response"), list):
        return {}, "API payload has no response list."

    teams: dict[int, dict[str, Any]] = {}
    for team_stat in payload.get("response") or []:
        team = team_stat.get("team") or {}
        team_id = parse_optional_int(team.get("id"))
        if team_id is None:
            continue

        offsides_items = [
            {
                "type": item.get("type"),
                "value": item.get("value"),
                "parsed_value": parse_optional_int(item.get("value")),
            }
            for item in (team_stat.get(statistics_key) or [])
            if is_offsides_stat_type(item.get("type"))
        ]
        parsed_values = [
            item["parsed_value"]
            for item in offsides_items
            if item["parsed_value"] is not None
        ]
        teams[team_id] = {
            "team_id": team_id,
            "team_name": team.get("name"),
            "offsides_items": offsides_items,
            "offsides_value": parsed_values[0] if parsed_values else None,
            "stat_types": api_stat_types(team_stat, statistics_key),
        }

    return teams, None


def extract_api_stat_sections(
    *,
    default_payload: dict[str, Any] | None,
    half_payload: dict[str, Any] | None,
) -> tuple[dict[str, dict[int, dict[str, Any]]], dict[str, str | None]]:
    sections: dict[str, dict[int, dict[str, Any]]] = {}
    errors: dict[str, str | None] = {}

    for section_name, statistics_key in API_STAT_SECTIONS:
        payload = default_payload if section_name == "default_ft" else half_payload
        teams, error = extract_api_team_stats(payload, statistics_key)
        sections[section_name] = teams
        errors[section_name] = error

    return sections, errors


def merge_api_team_stats(
    api_sections: dict[str, dict[int, dict[str, Any]]],
) -> dict[int, dict[str, Any]]:
    team_ids = sorted(
        {
            team_id
            for section_stats in api_sections.values()
            for team_id in section_stats.keys()
        }
    )
    merged: dict[int, dict[str, Any]] = {}
    for team_id in team_ids:
        first_section = next(
            (
                section_stats[team_id]
                for section_stats in api_sections.values()
                if team_id in section_stats
            ),
            {},
        )
        values_by_section = {
            section_name: section_stats.get(team_id, {}).get("offsides_value")
            for section_name, section_stats in api_sections.items()
        }
        merged[team_id] = {
            "team_id": team_id,
            "team_name": first_section.get("team_name"),
            "offsides_value": next(
                (value for value in values_by_section.values() if value is not None),
                None,
            ),
            "offsides_by_section": values_by_section,
            "sections": {
                section_name: section_stats.get(team_id, {})
                for section_name, section_stats in api_sections.items()
            },
        }
    return merged


def combine_api_errors(errors_by_section: dict[str, str | None]) -> str | None:
    errors = {
        section_name: error
        for section_name, error in errors_by_section.items()
        if error
    }
    if not errors:
        return None
    return json.dumps(errors, ensure_ascii=False, sort_keys=True)


def valid_sample_for_market(fact: dict[str, Any], market_key: str) -> bool:
    if market_key.startswith("MATCH_") and market_key.endswith("_OFFSIDES"):
        return (
            fact.get("offsides_for") is not None
            and fact.get("offsides_against") is not None
            and fact.get("total_offsides") is not None
        )
    if market_key.endswith("_OFFSIDES_FOR"):
        return fact.get("offsides_for") is not None
    if market_key.endswith("_OFFSIDES_AGAINST"):
        return fact.get("offsides_against") is not None
    return False


def db_has_target_offsides(
    *,
    target_team_id: int,
    fixture_stat_by_team: dict[int, dict[str, Any]],
    fact: dict[str, Any],
    summary: dict[str, Any] | None,
) -> tuple[bool, bool]:
    target_fixture_stat = fixture_stat_by_team.get(target_team_id, {})
    own_values = [
        target_fixture_stat.get("offsides"),
        fact.get("offsides_for"),
    ]
    opponent_values = [fact.get("offsides_against")]

    if summary:
        if target_team_id == summary.get("home_team_id"):
            own_values.append(summary.get("home_offsides"))
            opponent_values.append(summary.get("away_offsides"))
        elif target_team_id == summary.get("away_team_id"):
            own_values.append(summary.get("away_offsides"))
            opponent_values.append(summary.get("home_offsides"))

    return any(value is not None for value in own_values), any(value is not None for value in opponent_values)


def classify_fixture(
    *,
    target_team_id: int,
    opponent_team_id: int,
    api_error: str | None,
    api_team_stats: dict[int, dict[str, Any]],
    fixture_stat_by_team: dict[int, dict[str, Any]],
    fact: dict[str, Any],
    summary: dict[str, Any] | None,
) -> str:
    if api_error:
        return "API_RESPONSE_ERROR"

    if target_team_id not in api_team_stats or opponent_team_id not in api_team_stats:
        return "API_TEAM_MAPPING_MISMATCH"

    api_target = api_team_stats[target_team_id].get("offsides_value")
    api_opponent = api_team_stats[opponent_team_id].get("offsides_value")
    api_has_target = api_target is not None
    api_has_opponent = api_opponent is not None
    api_has_any = api_has_target or api_has_opponent

    db_has_target, db_has_opponent = db_has_target_offsides(
        target_team_id=target_team_id,
        fixture_stat_by_team=fixture_stat_by_team,
        fact=fact,
        summary=summary,
    )
    db_has_any = db_has_target or db_has_opponent

    if api_has_any:
        if (api_has_target and not db_has_target) or (api_has_opponent and not db_has_opponent):
            return "API_HAS_OFFSIDES_BUT_DB_MISSING"
        return "API_HAS_OFFSIDES_AND_DB_HAS_OFFSIDES"

    if db_has_any:
        return "DB_HAS_OFFSIDES_BUT_API_MISSING"

    return "API_MISSING_OFFSIDES"


def build_missing_combos(
    *,
    league_id: int,
    season: int,
    facts: list[dict[str, Any]],
    active_markets: list[str],
    stats_rows: list[dict[str, Any]],
    team_names: dict[int, str],
    team_id_filter: int | None,
    scope_filter: str | None,
) -> list[MissingCombo]:
    actual = {
        (int(row["team_id"]), str(row["scope"]), str(row["market_key"]))
        for row in stats_rows
        if row.get("team_id") is not None and row.get("scope") and row.get("market_key")
    }
    facts_by_team_scope: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    team_ids = sorted({int(row["team_id"]) for row in facts if row.get("team_id") is not None})
    for fact in facts:
        fact_team_id = int(fact["team_id"])
        facts_by_team_scope[(fact_team_id, "overall")].append(fact)
        facts_by_team_scope[(fact_team_id, str(fact["venue_scope"]))].append(fact)

    combos: list[MissingCombo] = []
    for team_id in team_ids:
        if team_id_filter is not None and team_id != team_id_filter:
            continue
        for scope in SCOPES:
            if scope_filter is not None and scope != scope_filter:
                continue

            missing_markets = [
                market_key
                for market_key in active_markets
                if (team_id, scope, market_key) not in actual
            ]
            if not missing_markets:
                continue

            scoped_facts = facts_by_team_scope.get((team_id, scope), [])
            valid_fact_sample = sum(
                1
                for market_key in missing_markets
                for fact in scoped_facts
                if valid_sample_for_market(fact, market_key)
            )
            combos.append(
                MissingCombo(
                    league_id=league_id,
                    season=season,
                    team_id=team_id,
                    team_name=team_names.get(team_id),
                    scope=scope,
                    missing_markets=tuple(missing_markets),
                    missing_rows=len(missing_markets),
                    valid_fact_sample=valid_fact_sample,
                )
            )

    return combos


def build_markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Offsides API Coverage Audit",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in result["summary"].items():
        lines.append(f"| {key} | {value} |")

    lines.extend([
        "",
        "## Missing Team/Scope Combinations",
        "",
        "| league_id | season | team_id | team_name | scope | missing_rows | valid_fact_sample | fixtures_expected |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ])
    for combo in result["missing_combinations"]:
        lines.append(
            "| {league_id} | {season} | {team_id} | {team_name} | {scope} | {missing_rows} | {valid_fact_sample} | {fixtures_expected_count} |".format(
                **combo
            )
        )

    lines.extend([
        "",
        "## Fixture Classifications",
        "",
        "| classification | fixture_id | team_id | team_name | scope | opponent | date | api_default | api_half_ft | api_1h | api_2h | db_fact_for | db_ft | db_1h | db_2h |",
        "| --- | ---: | ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in result["fixture_audits"]:
        lines.append(
            "| {classification} | {fixture_id} | {team_id} | {team_name} | {scope} | {opponent_name} | {date} | {api_target_default_ft_offsides} | {api_target_half_ft_offsides} | {api_target_half_1h_offsides} | {api_target_half_2h_offsides} | {db_fact_offsides_for} | {db_fixture_statistics_ft_offsides} | {db_fixture_statistics_1h_offsides} | {db_fixture_statistics_2h_offsides} |".format(
                **row
            )
        )
    lines.append("")
    return "\n".join(lines)


async def main_async() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)

    active_market_rows = select_all(
        lambda: supabase.table("market_definitions")
        .select("key,display_order")
        .eq("category", "offsides")
        .eq("is_active", True)
        .order("display_order", desc=False)
        .order("key", desc=False)
    )
    active_markets = [str(row["key"]) for row in active_market_rows if row.get("key")]
    if not active_markets:
        raise RuntimeError("No active offsides market definitions found.")

    facts = select_all(
        lambda: supabase.table("team_fixture_facts")
        .select(
            "fixture_id,team_id,opponent_team_id,league_id,season,played_at,is_home,venue_scope,"
            "offsides_for,offsides_against,total_offsides"
        )
        .eq("league_id", args.league)
        .eq("season", args.season)
        .order("team_id", desc=False)
        .order("played_at", desc=False)
    )
    if args.team_id is not None:
        facts = [row for row in facts if int(row["team_id"]) == args.team_id]
    if not facts:
        raise RuntimeError(f"No team_fixture_facts found for league={args.league} season={args.season}.")

    stats_rows = select_all(
        lambda: supabase.table("team_season_market_stats")
        .select("team_id,market_key,scope")
        .eq("category", "offsides")
        .eq("league_id", args.league)
        .eq("season", args.season)
    )
    if args.team_id is not None:
        stats_rows = [row for row in stats_rows if int(row["team_id"]) == args.team_id]

    all_team_ids = {
        int(row["team_id"])
        for row in facts
        if row.get("team_id") is not None
    } | {
        int(row["opponent_team_id"])
        for row in facts
        if row.get("opponent_team_id") is not None
    }
    team_rows = select_in_chunks(supabase, "teams", "id,name", in_column="id", values=all_team_ids)
    team_names = {
        int(row["id"]): str(row["name"])
        for row in team_rows
        if row.get("id") is not None
    }

    missing_combos = build_missing_combos(
        league_id=args.league,
        season=args.season,
        facts=facts,
        active_markets=active_markets,
        stats_rows=stats_rows,
        team_names=team_names,
        team_id_filter=args.team_id,
        scope_filter=args.scope,
    )

    facts_by_team_scope: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        team_id = int(fact["team_id"])
        facts_by_team_scope[(team_id, "overall")].append(fact)
        facts_by_team_scope[(team_id, str(fact["venue_scope"]))].append(fact)

    fixture_plan: list[dict[str, Any]] = []
    seen_fixture_team_scope: set[tuple[int, int, str]] = set()
    for combo in missing_combos:
        scoped_facts = sorted(
            facts_by_team_scope.get((combo.team_id, combo.scope), []),
            key=lambda row: str(row.get("played_at") or ""),
        )
        for fact in scoped_facts:
            key = (int(fact["fixture_id"]), combo.team_id, combo.scope)
            if key in seen_fixture_team_scope:
                continue
            seen_fixture_team_scope.add(key)
            fixture_plan.append({"combo": combo, "fact": fact})

    total_fixture_candidates = len(fixture_plan)
    if args.limit is not None:
        fixture_plan = fixture_plan[: max(0, args.limit)]

    fixture_ids = sorted({int(item["fact"]["fixture_id"]) for item in fixture_plan})
    fixture_stat_rows = select_in_chunks(
        supabase,
        "fixture_statistics",
        "fixture_id,team_id,period,offsides,raw_payload",
        in_column="fixture_id",
        values=fixture_ids,
    )
    fixture_stats_by_fixture: dict[int, dict[int, dict[str, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    for row in fixture_stat_rows:
        if row.get("team_id") is None:
            continue
        period = str(row.get("period") or "FT").upper()
        fixture_stats_by_fixture[int(row["fixture_id"])][int(row["team_id"])][period] = row

    summary_rows = select_in_chunks(
        supabase,
        "fixture_team_summary",
        (
            "fixture_id,date,status_short,home_team_id,home_team_name,away_team_id,away_team_name,"
            "home_offsides,away_offsides"
        ),
        in_column="fixture_id",
        values=fixture_ids,
    )
    summary_by_fixture = {
        int(row["fixture_id"]): row
        for row in summary_rows
        if row.get("fixture_id") is not None
    }

    api_payload_by_fixture: dict[int, dict[str, Any] | None] = {}
    api_half_payload_by_fixture: dict[int, dict[str, Any] | None] = {}
    fixture_audits: list[dict[str, Any]] = []

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        for fixture_id in fixture_ids:
            api_payload_by_fixture[fixture_id] = await api_client.fetch(
                "fixtures/statistics",
                {"fixture": fixture_id},
            )
            api_half_payload_by_fixture[fixture_id] = await api_client.fetch(
                "fixtures/statistics",
                {"fixture": fixture_id, "half": "true"},
            )

    for item in fixture_plan:
        combo: MissingCombo = item["combo"]
        fact = item["fact"]
        fixture_id = int(fact["fixture_id"])
        target_team_id = int(fact["team_id"])
        opponent_team_id = int(fact["opponent_team_id"])
        summary = summary_by_fixture.get(fixture_id)
        api_sections, api_errors_by_section = extract_api_stat_sections(
            default_payload=api_payload_by_fixture.get(fixture_id),
            half_payload=api_half_payload_by_fixture.get(fixture_id),
        )
        api_team_stats = merge_api_team_stats(api_sections)
        api_error = combine_api_errors(api_errors_by_section)
        fixture_stat_by_team_period = fixture_stats_by_fixture.get(fixture_id, {})
        fixture_stat_by_team = {
            team_id: periods.get("FT", {})
            for team_id, periods in fixture_stat_by_team_period.items()
        }
        classification = classify_fixture(
            target_team_id=target_team_id,
            opponent_team_id=opponent_team_id,
            api_error=api_error,
            api_team_stats=api_team_stats,
            fixture_stat_by_team=fixture_stat_by_team,
            fact=fact,
            summary=summary,
        )
        target_api = api_team_stats.get(target_team_id, {})
        opponent_api = api_team_stats.get(opponent_team_id, {})
        target_api_sections = target_api.get("sections", {})
        opponent_api_sections = opponent_api.get("sections", {})
        target_api_by_section = target_api.get("offsides_by_section", {})
        opponent_api_by_section = opponent_api.get("offsides_by_section", {})
        target_fixture_stats_by_period = fixture_stat_by_team_period.get(target_team_id, {})
        target_fixture_stat_ft = target_fixture_stats_by_period.get("FT", {})
        target_fixture_stat_1h = target_fixture_stats_by_period.get("1H", {})
        target_fixture_stat_2h = target_fixture_stats_by_period.get("2H", {})

        if summary and target_team_id == summary.get("home_team_id"):
            summary_target = summary.get("home_offsides")
            summary_opponent = summary.get("away_offsides")
            opponent_name = summary.get("away_team_name") or team_names.get(opponent_team_id)
        elif summary and target_team_id == summary.get("away_team_id"):
            summary_target = summary.get("away_offsides")
            summary_opponent = summary.get("home_offsides")
            opponent_name = summary.get("home_team_name") or team_names.get(opponent_team_id)
        else:
            summary_target = None
            summary_opponent = None
            opponent_name = team_names.get(opponent_team_id)

        fixture_audits.append(
            {
                "classification": classification,
                "league_id": combo.league_id,
                "season": combo.season,
                "team_id": target_team_id,
                "team_name": combo.team_name or team_names.get(target_team_id),
                "scope": combo.scope,
                "fixture_id": fixture_id,
                "date": summary.get("date") if summary else fact.get("played_at"),
                "opponent_team_id": opponent_team_id,
                "opponent_name": opponent_name,
                "api_error": api_error,
                "api_errors_by_section": api_errors_by_section,
                "api_team_ids_present": sorted(api_team_stats.keys()),
                "api_target_offsides": target_api.get("offsides_value"),
                "api_target_offsides_by_section": target_api_by_section,
                "api_target_default_ft_offsides": target_api_by_section.get("default_ft"),
                "api_target_half_ft_offsides": target_api_by_section.get("half_ft"),
                "api_target_half_1h_offsides": target_api_by_section.get("half_1h"),
                "api_target_half_2h_offsides": target_api_by_section.get("half_2h"),
                "api_target_default_ft_items": target_api_sections.get("default_ft", {}).get("offsides_items", []),
                "api_target_half_ft_items": target_api_sections.get("half_ft", {}).get("offsides_items", []),
                "api_target_half_1h_items": target_api_sections.get("half_1h", {}).get("offsides_items", []),
                "api_target_half_2h_items": target_api_sections.get("half_2h", {}).get("offsides_items", []),
                "api_target_default_ft_stat_types": target_api_sections.get("default_ft", {}).get("stat_types", []),
                "api_target_half_ft_stat_types": target_api_sections.get("half_ft", {}).get("stat_types", []),
                "api_target_half_1h_stat_types": target_api_sections.get("half_1h", {}).get("stat_types", []),
                "api_target_half_2h_stat_types": target_api_sections.get("half_2h", {}).get("stat_types", []),
                "api_opponent_offsides": opponent_api.get("offsides_value"),
                "api_opponent_offsides_by_section": opponent_api_by_section,
                "api_opponent_default_ft_offsides": opponent_api_by_section.get("default_ft"),
                "api_opponent_half_ft_offsides": opponent_api_by_section.get("half_ft"),
                "api_opponent_half_1h_offsides": opponent_api_by_section.get("half_1h"),
                "api_opponent_half_2h_offsides": opponent_api_by_section.get("half_2h"),
                "api_opponent_default_ft_items": opponent_api_sections.get("default_ft", {}).get("offsides_items", []),
                "api_opponent_half_ft_items": opponent_api_sections.get("half_ft", {}).get("offsides_items", []),
                "api_opponent_half_1h_items": opponent_api_sections.get("half_1h", {}).get("offsides_items", []),
                "api_opponent_half_2h_items": opponent_api_sections.get("half_2h", {}).get("offsides_items", []),
                "api_opponent_default_ft_stat_types": opponent_api_sections.get("default_ft", {}).get("stat_types", []),
                "api_opponent_half_ft_stat_types": opponent_api_sections.get("half_ft", {}).get("stat_types", []),
                "api_opponent_half_1h_stat_types": opponent_api_sections.get("half_1h", {}).get("stat_types", []),
                "api_opponent_half_2h_stat_types": opponent_api_sections.get("half_2h", {}).get("stat_types", []),
                "db_fixture_statistics_offsides": target_fixture_stat_ft.get("offsides"),
                "db_fixture_statistics_ft_offsides": target_fixture_stat_ft.get("offsides"),
                "db_fixture_statistics_1h_offsides": target_fixture_stat_1h.get("offsides"),
                "db_fixture_statistics_2h_offsides": target_fixture_stat_2h.get("offsides"),
                "db_fact_offsides_for": fact.get("offsides_for"),
                "db_fact_offsides_against": fact.get("offsides_against"),
                "db_fact_total_offsides": fact.get("total_offsides"),
                "db_summary_target_offsides": summary_target,
                "db_summary_opponent_offsides": summary_opponent,
            }
        )

    classification_counts = Counter(row["classification"] for row in fixture_audits)
    api_has_offsides = sum(
        1
        for row in fixture_audits
        if row["api_target_offsides"] is not None or row["api_opponent_offsides"] is not None
    )
    api_default_has_offsides = sum(
        1
        for row in fixture_audits
        if (
            row["api_target_default_ft_offsides"] is not None
            or row["api_opponent_default_ft_offsides"] is not None
        )
    )
    api_half_has_offsides = sum(
        1
        for row in fixture_audits
        if any(
            row[key] is not None
            for key in (
                "api_target_half_ft_offsides",
                "api_target_half_1h_offsides",
                "api_target_half_2h_offsides",
                "api_opponent_half_ft_offsides",
                "api_opponent_half_1h_offsides",
                "api_opponent_half_2h_offsides",
            )
        )
    )
    summary = {
        "league_id": args.league,
        "season": args.season,
        "active_offsides_markets": len(active_markets),
        "missing_team_scope_combinations": len(missing_combos),
        "total_fixture_candidates": total_fixture_candidates,
        "total_unique_fixture_candidates": len(fixture_ids),
        "total_fixtures_checked": len(fixture_ids),
        "total_fixture_audit_rows": len(fixture_audits),
        "api_default_has_offsides": api_default_has_offsides,
        "api_half_has_offsides": api_half_has_offsides,
        "api_has_offsides": api_has_offsides,
        "api_missing_offsides": len(fixture_audits) - api_has_offsides,
        "db_missing_when_api_has": classification_counts["API_HAS_OFFSIDES_BUT_DB_MISSING"],
        "mapping_mismatches": classification_counts["API_TEAM_MAPPING_MISMATCH"],
        "response_errors": classification_counts["API_RESPONSE_ERROR"],
        "classification_counts": {name: classification_counts[name] for name in CLASSIFICATIONS},
    }

    combo_payload = []
    for combo in missing_combos:
        scoped_facts = sorted(
            facts_by_team_scope.get((combo.team_id, combo.scope), []),
            key=lambda row: str(row.get("played_at") or ""),
        )
        combo_payload.append(
            {
                "league_id": combo.league_id,
                "season": combo.season,
                "team_id": combo.team_id,
                "team_name": combo.team_name,
                "scope": combo.scope,
                "missing_rows": combo.missing_rows,
                "missing_markets": list(combo.missing_markets),
                "valid_fact_sample": combo.valid_fact_sample,
                "fixtures_expected_count": len(scoped_facts),
                "fixtures": [
                    {
                        "fixture_id": int(fact["fixture_id"]),
                        "date": fact.get("played_at"),
                        "opponent_team_id": int(fact["opponent_team_id"]),
                        "opponent_name": team_names.get(int(fact["opponent_team_id"])),
                    }
                    for fact in scoped_facts
                ],
            }
        )

    result = {
        "summary": summary,
        "missing_combinations": combo_payload,
        "fixture_audits": fixture_audits,
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nMissing team/scope combinations:")
    for combo in combo_payload:
        print(
            f"- league={combo['league_id']} season={combo['season']} "
            f"team={combo['team_id']} {combo['team_name']} scope={combo['scope']} "
            f"missing_rows={combo['missing_rows']} fixtures={combo['fixtures_expected_count']} "
            f"valid_fact_sample={combo['valid_fact_sample']}"
        )

    print("\nClassification counts:")
    for name in CLASSIFICATIONS:
        print(f"- {name}: {classification_counts[name]}")

    if args.write_files:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "result_check_offsides_api_coverage.json"
        md_path = output_dir / "result_check_offsides_api_coverage.md"
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(build_markdown_report(result), encoding="utf-8")
        print(f"\nWrote {json_path}")
        print(f"Wrote {md_path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
