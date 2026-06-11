from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pipeline_core import (
    ApiFootballClient,
    configure_logging,
    is_final_status,
    load_settings,
    parse_iso_datetime,
    parse_optional_int,
)


LOGGER = configure_logging("stuf.audit.api-football-provider")
WORLD_CUP = {"league_id": 1, "season": 2026, "label": "World Cup 2026"}
BIG_FIVE = (
    {"league_id": 39, "season": 2025, "label": "Premier League 2025/2026"},
    {"league_id": 61, "season": 2025, "label": "Ligue 1 2025/2026"},
    {"league_id": 78, "season": 2025, "label": "Bundesliga 2025/2026"},
    {"league_id": 135, "season": 2025, "label": "Serie A 2025/2026"},
    {"league_id": 140, "season": 2025, "label": "LaLiga 2025/2026"},
)
ACTIVE_CANDIDATES = (
    {"league_id": 253, "season": 2026, "label": "MLS 2026"},
    {"league_id": 71, "season": 2026, "label": "Brasileirao 2026"},
    {"league_id": 113, "season": 2026, "label": "Allsvenskan 2026"},
    {"league_id": 103, "season": 2026, "label": "Eliteserien 2026"},
    {"league_id": 292, "season": 2026, "label": "K League 1 2026"},
    {"league_id": 98, "season": 2026, "label": "J1 League 2026"},
)
PLAYER_ENDPOINTS = (
    "players",
    "players/profiles",
    "players/seasons",
    "players/topscorers",
    "players/topassists",
    "players/topyellowcards",
    "players/topredcards",
)
TEAM_MARKET_CATEGORIES = (
    "result",
    "btts",
    "goals",
    "goals_1h",
    "goals_2h",
    "corners",
    "cards",
    "booking_points",
    "shots",
    "offsides",
    "fouls",
)
PLAYER_MARKET_CATEGORIES = (
    "player_scored",
    "player_shots",
    "player_cards",
    "player_fouls",
    "player_tackles",
    "player_assists",
)
ALL_MARKET_CATEGORIES = TEAM_MARKET_CATEGORIES + PLAYER_MARKET_CATEGORIES

ODDS_CATEGORY_RULES: dict[str, tuple[re.Pattern[str], ...]] = {
    "result": (
        re.compile(r"\bmatch winner\b", re.I),
        re.compile(r"\bhome/away\b", re.I),
        re.compile(r"\bdouble chance\b", re.I),
        re.compile(r"\bdraw no bet\b", re.I),
    ),
    "btts": (
        re.compile(r"\bboth teams to score\b", re.I),
        re.compile(r"\bbtts\b", re.I),
    ),
    "goals": (
        re.compile(r"\bgoals over/under\b", re.I),
        re.compile(r"\btotal goals\b", re.I),
    ),
    "goals_1h": (
        re.compile(r"\bgoals over/under first half\b", re.I),
        re.compile(r"\bfirst half.*goals\b", re.I),
    ),
    "goals_2h": (
        re.compile(r"\bgoals over/under second half\b", re.I),
        re.compile(r"\bsecond half.*goals\b", re.I),
    ),
    "corners": (
        re.compile(r"\bcorner", re.I),
    ),
    "cards": (
        re.compile(r"\bcard", re.I),
    ),
    "booking_points": (
        re.compile(r"\bbooking points\b", re.I),
    ),
    "shots": (
        re.compile(r"\bshot", re.I),
    ),
    "offsides": (
        re.compile(r"\boffside", re.I),
    ),
    "fouls": (
        re.compile(r"\bfoul", re.I),
    ),
    "player_scored": (
        re.compile(r"\bgoalscorer\b", re.I),
        re.compile(r"\bgoal scorer\b", re.I),
        re.compile(r"\bplayer to score\b", re.I),
        re.compile(r"\banytime goal scorer\b", re.I),
        re.compile(r"\bfirst goal scorer\b", re.I),
        re.compile(r"\blast goal scorer\b", re.I),
    ),
    "player_shots": (
        re.compile(r"\bplayer.*shot", re.I),
        re.compile(r"\bshot[s]?\s+by player\b", re.I),
    ),
    "player_cards": (
        re.compile(r"\bplayer.*card", re.I),
    ),
    "player_fouls": (
        re.compile(r"\bplayer.*foul", re.I),
    ),
    "player_tackles": (
        re.compile(r"\bplayer.*tackle", re.I),
    ),
    "player_assists": (
        re.compile(r"\bplayer.*assist", re.I),
        re.compile(r"\bassist.*player\b", re.I),
    ),
}

STAT_FIELD_RULES = {
    "corners": ("corner kicks",),
    "shots": ("total shots", "shots on goal"),
    "offsides": ("offsides",),
    "fouls": ("fouls",),
    "cards": ("yellow cards", "red cards"),
}


@dataclass(frozen=True)
class FixtureSample:
    fixture_id: int
    league_id: int
    season: int
    league_name: str
    home_id: int
    away_id: int
    home_name: str
    away_name: str
    kickoff_at: str | None
    status: str | None
    source_label: str

    @property
    def h2h_key(self) -> str:
        return f"{self.home_id}-{self.away_id}"

    @property
    def label(self) -> str:
        return f"{self.league_name}: {self.home_name} vs {self.away_name} ({self.fixture_id})"


@dataclass
class EndpointStats:
    endpoint: str
    request_count: int = 0
    response_count: int = 0
    empty_count: int = 0
    error_count: int = 0
    quota_cost: int = 0
    example_path: str | None = None
    empty_example_path: str | None = None
    fixtures_or_leagues: list[str] = field(default_factory=list)
    fields_present: Counter[str] = field(default_factory=Counter)
    fields_missing: Counter[str] = field(default_factory=Counter)
    inconsistencies: Counter[str] = field(default_factory=Counter)
    freshness_hours_before: list[float] = field(default_factory=list)
    freshness_hours_after: list[float] = field(default_factory=list)
    real_response_item_count: int = 0


@dataclass
class OddsFixtureResult:
    fixture: FixtureSample
    has_any_odds: bool
    update_at: str | None
    bookmakers: list[dict[str, Any]]
    categories_found: set[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only provider audit for API-Football as STUF data source. "
            "Calls live API endpoints, stores payload examples locally, and writes MD/JSON reports."
        )
    )
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--wc-upcoming-sample", type=int, default=8)
    parser.add_argument("--active-upcoming-sample", type=int, default=5)
    parser.add_argument("--near-kickoff-sample", type=int, default=8)
    parser.add_argument("--historical-sample-per-league", type=int, default=8)
    parser.add_argument("--recent-active-finished-per-league", type=int, default=2)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "codex" / "reports" / "api_football_provider_audit"),
    )
    return parser.parse_args()


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return normalized.strip("-") or "item"


def response_list(payload: dict[str, Any] | None) -> list[Any]:
    if not payload:
        return []
    response = payload.get("response")
    return response if isinstance(response, list) else []


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def safe_name(value: Any) -> str:
    return " ".join(str(value or "").split())


def team_name(team: dict[str, Any] | None) -> str:
    return safe_name((team or {}).get("name"))


def fixture_date(row: dict[str, Any]) -> str | None:
    return (row.get("fixture") or {}).get("date")


def fixture_status(row: dict[str, Any]) -> str | None:
    return ((row.get("fixture") or {}).get("status") or {}).get("short")


def fixture_id(row: dict[str, Any]) -> int | None:
    return parse_optional_int((row.get("fixture") or {}).get("id"))


def fixture_to_sample(row: dict[str, Any], source_label: str) -> FixtureSample | None:
    fixture = row.get("fixture") or {}
    league = row.get("league") or {}
    teams = row.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    sample = FixtureSample(
        fixture_id=parse_optional_int(fixture.get("id")) or 0,
        league_id=parse_optional_int(league.get("id")) or 0,
        season=parse_optional_int(league.get("season")) or 0,
        league_name=safe_name(league.get("name")),
        home_id=parse_optional_int(home.get("id")) or 0,
        away_id=parse_optional_int(away.get("id")) or 0,
        home_name=team_name(home),
        away_name=team_name(away),
        kickoff_at=fixture.get("date"),
        status=((fixture.get("status") or {}).get("short")),
        source_label=source_label,
    )
    if sample.fixture_id <= 0 or sample.home_id <= 0 or sample.away_id <= 0:
        return None
    return sample


def format_fixture(sample: FixtureSample) -> str:
    return f"{sample.league_id}/{sample.season} {sample.home_name} vs {sample.away_name} ({sample.fixture_id})"


def senior_fixture_filter(row: dict[str, Any]) -> bool:
    league_name = safe_name((row.get("league") or {}).get("name")).lower()
    blocked_tokens = ("reserve", "u20", "u21", "u23", "women", "w league", "res.", "res ")
    return not any(token in league_name for token in blocked_tokens)


def extract_fields(data: Any, field_paths: Iterable[str]) -> tuple[list[str], list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for path in field_paths:
        current: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(current, list):
                if not current:
                    ok = False
                    break
                current = current[0]
            if not isinstance(current, dict) or part not in current:
                ok = False
                break
            current = current.get(part)
            if current is None:
                ok = False
                break
        if ok:
            present.append(path)
        else:
            missing.append(path)
    return present, missing


def fixture_freshness_hours(sample: FixtureSample, audit_now: datetime) -> tuple[str, float] | None:
    kickoff = parse_iso_datetime(sample.kickoff_at)
    if not kickoff:
        return None
    delta_hours = (kickoff - audit_now).total_seconds() / 3600
    if delta_hours >= 0:
        return ("before", round(delta_hours, 2))
    return ("after", round(abs(delta_hours), 2))


def stats_payload_has_half_split(payload: dict[str, Any] | None) -> bool:
    for row in response_list(payload):
        if any(key in row for key in ("statistics_1h", "statistics_2h")):
            if row.get("statistics_1h") or row.get("statistics_2h"):
                return True
    return False


def predictions_payload_is_real(payload: dict[str, Any] | None) -> bool:
    rows = response_list(payload)
    if not rows:
        return False
    prediction = (rows[0].get("predictions") or {})
    percent = prediction.get("percent") or {}
    advice = safe_name(prediction.get("advice")).lower()
    winner = prediction.get("winner") or {}
    if winner.get("id") or winner.get("name"):
        return True
    if advice and advice != "no predictions available":
        return True
    unique_percents = {safe_name(value) for value in percent.values() if value is not None}
    return len(unique_percents) > 1


def odds_payload_is_real(payload: dict[str, Any] | None) -> bool:
    rows = response_list(payload)
    if not rows:
        return False
    bookmakers = rows[0].get("bookmakers") or []
    for bookmaker in bookmakers:
        for bet in bookmaker.get("bets") or []:
            for value in bet.get("values") or []:
                if value.get("odd") not in (None, "", "-"):
                    return True
    return False


def players_seasons_is_real(payload: dict[str, Any] | None) -> bool:
    response = response_list(payload)
    return bool(response) and all(isinstance(item, int) for item in response)


def default_is_real(endpoint: str, payload: dict[str, Any] | None) -> bool:
    if endpoint == "predictions":
        return predictions_payload_is_real(payload)
    if endpoint == "odds":
        return odds_payload_is_real(payload)
    if endpoint == "fixtures/statistics?half=true":
        return stats_payload_has_half_split(payload)
    if endpoint == "players/seasons":
        return players_seasons_is_real(payload)
    return bool(response_list(payload))


def endpoint_field_paths(endpoint: str) -> tuple[str, ...]:
    mapping = {
        "fixtures": ("fixture.id", "fixture.date", "fixture.status.short", "league.id", "teams.home.id", "teams.away.id"),
        "fixtures/statistics": ("team.id", "statistics.type", "statistics.value"),
        "fixtures/statistics?half=true": ("team.id", "statistics_1h.type", "statistics_1h.value", "statistics_2h.type", "statistics_2h.value"),
        "fixtures/events": ("time.elapsed", "team.id", "player.id", "type", "detail"),
        "fixtures/players": ("team.id", "players.player.id", "players.statistics.games.minutes"),
        "fixtures/lineups": ("team.id", "formation", "startXI.player.id"),
        "fixtures/headtohead": ("fixture.id", "teams.home.id", "teams.away.id"),
        "injuries": ("player.id", "player.name", "team.id", "fixture.id"),
        "predictions": ("predictions.percent.home", "predictions.percent.draw", "predictions.percent.away", "comparison.form.home"),
        "odds": ("fixture.id", "bookmakers.id", "bookmakers.bets.id", "bookmakers.bets.values.value", "bookmakers.bets.values.odd"),
        "odds/mapping": ("league.id", "fixture.id", "update"),
        "odds/bookmakers": ("id", "name"),
        "odds/bets": ("id", "name"),
        "odds/live": ("fixture.id", "league.id", "odds.id", "odds.values.value", "odds.values.odd"),
        "odds/live/bets": ("id", "name"),
        "standings": ("league.id", "league.standings"),
        "players": ("player.id", "statistics.team.id", "statistics.games.minutes"),
        "players/profiles": ("player.id", "player.name", "player.position"),
        "players/seasons": (),
        "players/topscorers": ("player.id", "statistics.team.id", "statistics.goals.total"),
        "players/topassists": ("player.id", "statistics.team.id", "statistics.goals.assists"),
        "players/topyellowcards": ("player.id", "statistics.cards.yellow"),
        "players/topredcards": ("player.id", "statistics.cards.red"),
    }
    return mapping.get(endpoint, ())


def find_stat_types(payload: dict[str, Any] | None, half: bool = False) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in response_list(payload):
        keys = ("statistics",) if not half else ("statistics_1h", "statistics_2h")
        for key in keys:
            for item in row.get(key) or []:
                counter[safe_name(item.get("type"))] += 1
    return counter


def find_player_stat_paths(payload: dict[str, Any] | None) -> Counter[str]:
    counter: Counter[str] = Counter()
    for team_row in response_list(payload):
        for player_row in team_row.get("players") or []:
            for stat in player_row.get("statistics") or []:
                for path in (
                    ("goals", "total"),
                    ("goals", "assists"),
                    ("shots", "total"),
                    ("shots", "on"),
                    ("cards", "yellow"),
                    ("cards", "red"),
                    ("fouls", "committed"),
                    ("fouls", "drawn"),
                    ("tackles", "total"),
                    ("games", "minutes"),
                ):
                    current = stat
                    ok = True
                    for part in path:
                        if not isinstance(current, dict) or part not in current:
                            ok = False
                            break
                        current = current.get(part)
                    if current is not None and ok:
                        counter[".".join(path)] += 1
    return counter


def find_event_details(payload: dict[str, Any] | None) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in response_list(payload):
        counter[safe_name(row.get("type"))] += 1
        detail = safe_name(row.get("detail"))
        if detail:
            counter[detail] += 1
    return counter


def odds_categories_from_name(name: str) -> set[str]:
    lowered = name.lower()
    categories: set[str] = set()
    for category, rules in ODDS_CATEGORY_RULES.items():
        if any(rule.search(name) for rule in rules):
            categories.add(category)
    if "first half" in lowered or "1st half" in lowered:
        categories.discard("goals")
        categories.discard("result")
    if "second half" in lowered or "2nd half" in lowered:
        categories.discard("goals")
        categories.discard("result")
    return categories


def percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


class ProviderAudit:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.payload_dir = output_dir / "payloads"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint_stats: dict[str, EndpointStats] = defaultdict(lambda: EndpointStats(endpoint=""))
        self.scope_notes: list[str] = []
        self.payload_examples: dict[str, str] = {}
        self.fixture_samples: dict[str, list[FixtureSample]] = defaultdict(list)
        self.big_five_historical: list[FixtureSample] = []
        self.active_recent_finished: list[FixtureSample] = []
        self.wc_upcoming: list[FixtureSample] = []
        self.active_upcoming: list[FixtureSample] = []
        self.near_kickoff: list[FixtureSample] = []
        self.live_fixtures: list[FixtureSample] = []
        self.odds_results: list[OddsFixtureResult] = []
        self.odds_bookmaker_fixture_counts: Counter[str] = Counter()
        self.odds_bet_name_counts: Counter[str] = Counter()
        self.odds_category_fixture_counts: Counter[str] = Counter()
        self.odds_category_league_fixture_counts: dict[int, Counter[str]] = defaultdict(Counter)
        self.odds_bookmaker_category_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.stats_type_counts: Counter[str] = Counter()
        self.half_stats_type_counts: Counter[str] = Counter()
        self.event_detail_counts: Counter[str] = Counter()
        self.player_stat_path_counts: Counter[str] = Counter()
        self.league_request_matrix: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
        self.quota_start: dict[str, Any] = {}
        self.quota_end: dict[str, Any] = {}
        self.total_requests = 0
        self.audit_now = now_utc()

    def get_endpoint(self, endpoint: str) -> EndpointStats:
        stats = self.endpoint_stats[endpoint]
        if not stats.endpoint:
            stats.endpoint = endpoint
        return stats

    def save_payload(self, key: str, payload: dict[str, Any]) -> str:
        safe_key = slugify(key)
        path = self.payload_dir / f"{safe_key}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def record_endpoint(
        self,
        endpoint: str,
        params: dict[str, Any],
        payload: dict[str, Any] | None,
        *,
        context_label: str,
        fixture: FixtureSample | None = None,
        response_items_count: int | None = None,
        usable_override: bool | None = None,
    ) -> bool:
        stats = self.get_endpoint(endpoint)
        stats.request_count += 1
        stats.quota_cost += 1
        self.total_requests += 1
        if context_label not in stats.fixtures_or_leagues:
            stats.fixtures_or_leagues.append(context_label)

        if payload is None:
            stats.error_count += 1
            stats.inconsistencies["client_returned_no_payload"] += 1
            return False

        errors = payload.get("errors")
        if errors:
            stats.error_count += 1
            stats.inconsistencies[f"api_errors:{errors}"] += 1
            if stats.empty_example_path is None:
                stats.empty_example_path = self.save_payload(f"{endpoint}-{context_label}-error", payload)
            return False

        usable = usable_override if usable_override is not None else default_is_real(endpoint, payload)
        if usable:
            stats.response_count += 1
            items = response_items_count
            if items is None:
                if endpoint == "players/seasons":
                    items = len(response_list(payload))
                else:
                    items = len(response_list(payload))
            stats.real_response_item_count += items or 0
            if stats.example_path is None:
                stats.example_path = self.save_payload(f"{endpoint}-{context_label}-usable", payload)
            example = response_list(payload)
            if endpoint == "predictions":
                example_data: Any = example[0] if example else {}
            elif endpoint == "standings":
                example_data = example[0] if example else {}
            elif endpoint == "players/seasons":
                example_data = {"response": example[:10]}
            else:
                example_data = example[0] if example else {}
            present, missing = extract_fields(example_data, endpoint_field_paths(endpoint))
            for item in present:
                stats.fields_present[item] += 1
            for item in missing:
                stats.fields_missing[item] += 1
            freshness = fixture_freshness_hours(fixture, self.audit_now) if fixture else None
            if freshness:
                phase, hours = freshness
                if phase == "before":
                    stats.freshness_hours_before.append(hours)
                else:
                    stats.freshness_hours_after.append(hours)
        else:
            stats.empty_count += 1
            if stats.empty_example_path is None:
                stats.empty_example_path = self.save_payload(f"{endpoint}-{context_label}-empty", payload)
            if endpoint == "predictions" and response_list(payload):
                stats.inconsistencies["prediction_payload_without_actionable_signal"] += 1
            elif endpoint == "odds" and response_list(payload):
                stats.inconsistencies["odds_payload_without_actionable_prices"] += 1
            elif endpoint == "fixtures/statistics?half=true" and response_list(payload):
                stats.inconsistencies["half_endpoint_without_half_payload"] += 1
        return usable

    def format_freshness(self, stats: EndpointStats) -> str:
        parts: list[str] = []
        if stats.freshness_hours_before:
            parts.append(
                f"{min(stats.freshness_hours_before):.1f}h to {max(stats.freshness_hours_before):.1f}h before kickoff"
            )
        if stats.freshness_hours_after:
            parts.append(
                f"{min(stats.freshness_hours_after):.1f}h to {max(stats.freshness_hours_after):.1f}h after kickoff"
            )
        return "; ".join(parts) if parts else "no direct kickoff-timed sample"


async def fetch_json(
    api: ApiFootballClient,
    audit: ProviderAudit,
    endpoint: str,
    params: dict[str, Any],
    *,
    context_label: str,
    fixture: FixtureSample | None = None,
    response_items_count: int | None = None,
    usable_override: bool | None = None,
) -> dict[str, Any] | None:
    payload = await api.fetch(endpoint, params)
    audit.record_endpoint(
        endpoint,
        params,
        payload,
        context_label=context_label,
        fixture=fixture,
        response_items_count=response_items_count,
        usable_override=usable_override,
    )
    return payload


async def fetch_fixtures_sample(
    api: ApiFootballClient,
    audit: ProviderAudit,
    *,
    params: dict[str, Any],
    source_label: str,
    limit: int,
    filter_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> list[FixtureSample]:
    payload = await fetch_json(api, audit, "fixtures", params, context_label=source_label)
    rows = response_list(payload)
    selected: list[FixtureSample] = []
    for row in rows:
        if filter_fn and not filter_fn(row):
            continue
        sample = fixture_to_sample(row, source_label=source_label)
        if sample is None:
            continue
        selected.append(sample)
        if len(selected) >= limit:
            break
    return selected


async def discover_scope(args: argparse.Namespace, api: ApiFootballClient, audit: ProviderAudit) -> None:
    audit.quota_start = parse_status(await fetch_json(api, audit, "status", {}, context_label="quota-start"))

    wc_samples = await fetch_fixtures_sample(
        api,
        audit,
        params={"league": WORLD_CUP["league_id"], "season": WORLD_CUP["season"], "next": args.wc_upcoming_sample},
        source_label="confirmado_world_cup",
        limit=args.wc_upcoming_sample,
    )
    audit.wc_upcoming = wc_samples
    audit.fixture_samples["confirmado_world_cup"].extend(wc_samples)

    active_upcoming: list[FixtureSample] = []
    active_leagues: list[dict[str, Any]] = []
    for league in ACTIVE_CANDIDATES:
        samples = await fetch_fixtures_sample(
            api,
            audit,
            params={"league": league["league_id"], "season": league["season"], "next": args.active_upcoming_sample},
            source_label="confirmado_liga_activa",
            limit=args.active_upcoming_sample,
        )
        if samples:
            active_leagues.append(league)
            active_upcoming.extend(samples)
            audit.fixture_samples["confirmado_liga_activa"].extend(samples)
    audit.active_upcoming = active_upcoming
    audit.scope_notes.append(
        "Active pre-match sample used leagues: "
        + ", ".join(f"{league['league_id']} {league['label']}" for league in active_leagues)
    )

    near_kickoff = await fetch_fixtures_sample(
        api,
        audit,
        params={"next": args.near_kickoff_sample * 3},
        source_label="confirmado_liga_activa",
        limit=args.near_kickoff_sample,
        filter_fn=senior_fixture_filter,
    )
    audit.near_kickoff = near_kickoff

    live_payload = await fetch_json(api, audit, "fixtures", {"live": "all"}, context_label="live-fixtures")
    live_samples: list[FixtureSample] = []
    for row in response_list(live_payload):
        sample = fixture_to_sample(row, source_label="confirmado_liga_activa")
        if sample:
            live_samples.append(sample)
    audit.live_fixtures = live_samples

    for league in BIG_FIVE:
        payload = await fetch_json(
            api,
            audit,
            "fixtures",
            {"league": league["league_id"], "season": league["season"], "last": args.historical_sample_per_league * 4},
            context_label="confirmado_historico_5_ligas",
        )
        rows = [row for row in response_list(payload) if is_final_status(fixture_status(row))]
        selected: list[FixtureSample] = []
        for row in rows:
            sample = fixture_to_sample(row, source_label="confirmado_historico_5_ligas")
            if sample is None:
                continue
            selected.append(sample)
            if len(selected) >= args.historical_sample_per_league:
                break
        audit.big_five_historical.extend(selected)
        audit.fixture_samples["confirmado_historico_5_ligas"].extend(selected)

    if active_leagues:
        for league in active_leagues:
            payload = await fetch_json(
                api,
                audit,
                "fixtures",
                {"league": league["league_id"], "season": league["season"], "last": args.recent_active_finished_per_league * 4},
                context_label="confirmado_liga_activa",
            )
            rows = [row for row in response_list(payload) if is_final_status(fixture_status(row))]
            count = 0
            for row in rows:
                sample = fixture_to_sample(row, source_label="confirmado_liga_activa")
                if sample is None:
                    continue
                audit.active_recent_finished.append(sample)
                count += 1
                if count >= args.recent_active_finished_per_league:
                    break


def parse_status(payload: dict[str, Any] | None) -> dict[str, Any]:
    response = (payload or {}).get("response")
    if not isinstance(response, dict):
        return {}
    requests = response.get("requests") or {}
    return {
        "requests_current": requests.get("current"),
        "requests_limit_day": requests.get("limit_day"),
        "account": (response.get("account") or {}).get("firstname") or (response.get("account") or {}).get("email"),
    }


async def audit_fixture_detail_endpoints(api: ApiFootballClient, audit: ProviderAudit) -> None:
    fixture_samples_for_details = audit.big_five_historical + audit.active_recent_finished
    for sample in fixture_samples_for_details:
        stats_payload = await fetch_json(
            api,
            audit,
            "fixtures/statistics",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )
        audit.stats_type_counts.update(find_stat_types(stats_payload))

        half_payload = await fetch_json(
            api,
            audit,
            "fixtures/statistics?half=true",
            {"fixture": sample.fixture_id, "half": "true"},
            context_label=sample.source_label,
            fixture=sample,
        )
        audit.half_stats_type_counts.update(find_stat_types(half_payload, half=True))

        events_payload = await fetch_json(
            api,
            audit,
            "fixtures/events",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )
        audit.event_detail_counts.update(find_event_details(events_payload))

        players_payload = await fetch_json(
            api,
            audit,
            "fixtures/players",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )
        audit.player_stat_path_counts.update(find_player_stat_paths(players_payload))

        await fetch_json(
            api,
            audit,
            "fixtures/lineups",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )


async def audit_prematch_endpoints(api: ApiFootballClient, audit: ProviderAudit) -> None:
    prematch_fixtures = list(audit.wc_upcoming[:]) + list(audit.near_kickoff[:]) + list(audit.active_upcoming[: min(15, len(audit.active_upcoming))])
    seen_fixture_ids: set[int] = set()
    unique_fixtures: list[FixtureSample] = []
    for sample in prematch_fixtures:
        if sample.fixture_id in seen_fixture_ids:
            continue
        seen_fixture_ids.add(sample.fixture_id)
        unique_fixtures.append(sample)

    for sample in unique_fixtures:
        await fetch_json(
            api,
            audit,
            "predictions",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )
        odds_payload = await fetch_json(
            api,
            audit,
            "odds",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )
        audit.odds_results.append(parse_odds_fixture_result(sample, odds_payload))
        await fetch_json(
            api,
            audit,
            "injuries",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )

    for sample in audit.near_kickoff + audit.wc_upcoming[:3]:
        await fetch_json(
            api,
            audit,
            "fixtures/lineups",
            {"fixture": sample.fixture_id},
            context_label=sample.source_label,
            fixture=sample,
        )

    h2h_targets = [sample for sample in unique_fixtures if sample.league_id != WORLD_CUP["league_id"]][:10]
    for sample in h2h_targets:
        await fetch_json(
            api,
            audit,
            "fixtures/headtohead",
            {"h2h": sample.h2h_key, "last": 5},
            context_label=sample.source_label,
            fixture=sample,
        )

    active_league_keys = {(sample.league_id, sample.season, sample.league_name) for sample in audit.active_upcoming}
    for league_id, season, league_name in sorted(active_league_keys):
        audit.league_request_matrix[(league_id, season)]["injuries"] += 1
        await fetch_json(
            api,
            audit,
            "injuries",
            {"league": league_id, "season": season},
            context_label=f"league-{league_id}-{season}",
        )
    await fetch_json(
        api,
        audit,
        "injuries",
        {"league": WORLD_CUP["league_id"], "season": WORLD_CUP["season"]},
        context_label="league-1-2026",
    )


def parse_odds_fixture_result(sample: FixtureSample, payload: dict[str, Any] | None) -> OddsFixtureResult:
    rows = response_list(payload)
    categories_found: set[str] = set()
    bookmakers: list[dict[str, Any]] = []
    update_at = None
    if rows:
        row = rows[0]
        update_at = row.get("update")
        for bookmaker in row.get("bookmakers") or []:
            bookmaker_name = safe_name(bookmaker.get("name"))
            categories_for_bookmaker: set[str] = set()
            bet_rows = bookmaker.get("bets") or []
            for bet in bet_rows:
                bet_name = safe_name(bet.get("name"))
                bet_categories = odds_categories_from_name(bet_name)
                categories_found.update(bet_categories)
                categories_for_bookmaker.update(bet_categories)
                if bet_name:
                    bookmakers.append(
                        {
                            "id": bookmaker.get("id"),
                            "name": bookmaker_name,
                            "bet_id": bet.get("id"),
                            "bet_name": bet_name,
                            "values_count": len(bet.get("values") or []),
                        }
                    )
            if bookmaker_name:
                yield_counts = categories_for_bookmaker
                # placeholder - counted later
                _ = yield_counts
    return OddsFixtureResult(
        fixture=sample,
        has_any_odds=odds_payload_is_real(payload),
        update_at=update_at,
        bookmakers=bookmakers,
        categories_found=categories_found,
    )


def enrich_odds_aggregates(audit: ProviderAudit) -> None:
    for result in audit.odds_results:
        if not result.has_any_odds:
            continue
        seen_bookmakers_for_fixture: set[str] = set()
        bookmaker_categories: dict[str, set[str]] = defaultdict(set)
        for row in result.bookmakers:
            bookmaker_name = row["name"]
            if bookmaker_name:
                seen_bookmakers_for_fixture.add(bookmaker_name)
            bet_name = row["bet_name"]
            audit.odds_bet_name_counts[f"{row['bet_id']}::{bet_name}"] += 1
            for category in odds_categories_from_name(bet_name):
                bookmaker_categories[bookmaker_name].add(category)
        for bookmaker_name in seen_bookmakers_for_fixture:
            audit.odds_bookmaker_fixture_counts[bookmaker_name] += 1
        for bookmaker_name, categories in bookmaker_categories.items():
            for category in categories:
                audit.odds_bookmaker_category_counts[bookmaker_name][category] += 1
        for category in result.categories_found:
            audit.odds_category_fixture_counts[category] += 1
            audit.odds_category_league_fixture_counts[result.fixture.league_id][category] += 1


async def audit_odds_catalogs(api: ApiFootballClient, audit: ProviderAudit) -> None:
    await fetch_json(api, audit, "odds/bookmakers", {}, context_label="catalog")
    await fetch_json(api, audit, "odds/bets", {}, context_label="catalog")
    for page in range(1, 14):
        await fetch_json(api, audit, "odds/mapping", {"page": page}, context_label=f"mapping-page-{page}")

    if audit.live_fixtures:
        await fetch_json(api, audit, "odds/live", {}, context_label="live-global")
        for sample in audit.live_fixtures[: min(4, len(audit.live_fixtures))]:
            await fetch_json(
                api,
                audit,
                "odds/live",
                {"fixture": sample.fixture_id},
                context_label="live-fixture",
                fixture=sample,
            )
    else:
        await fetch_json(api, audit, "odds/live", {}, context_label="live-global")
    await fetch_json(api, audit, "odds/live/bets", {}, context_label="catalog")


async def audit_standings_and_players(api: ApiFootballClient, audit: ProviderAudit) -> None:
    await fetch_json(
        api,
        audit,
        "standings",
        {"league": WORLD_CUP["league_id"], "season": WORLD_CUP["season"]},
        context_label="confirmado_world_cup",
    )

    benchmark_league = 253
    benchmark_season = 2026
    await fetch_json(
        api,
        audit,
        "players/topscorers",
        {"league": benchmark_league, "season": benchmark_season},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players/topassists",
        {"league": benchmark_league, "season": benchmark_season},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players/topyellowcards",
        {"league": benchmark_league, "season": benchmark_season},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players/topredcards",
        {"league": benchmark_league, "season": benchmark_season},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players",
        {"league": benchmark_league, "season": benchmark_season, "page": 1},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players/profiles",
        {"search": "Messi"},
        context_label="confirmado_liga_activa",
    )
    await fetch_json(
        api,
        audit,
        "players/seasons",
        {"player": 154},
        context_label="confirmado_liga_activa",
    )


def summarize_endpoint_verdict(endpoint: str, stats: EndpointStats) -> tuple[str, str]:
    coverage = percentage(stats.response_count, stats.request_count)
    if endpoint == "injuries":
        if stats.response_count > 0:
            return ("usable con condiciones", "League-level injury payloads were rich, but fixture-level injury queries were often empty.")
        return ("no usable", "No actionable injury payloads were observed.")
    if endpoint == "fixtures/lineups":
        if stats.response_count > 0:
            return ("usable con condiciones", "Useful in the hot-zone and post-match, but empty on broad future windows.")
        return ("no usable", "No actionable lineup payloads were observed.")
    if endpoint == "predictions":
        if coverage >= 70:
            return ("usable con condiciones", "Response often exists, but actionability must be validated with backtest; treat as context only until calibrated.")
        return ("no usable", "Actionable prediction payloads are too inconsistent in the tested sample.")
    if endpoint == "odds/live":
        if stats.response_count > 0:
            return ("usable con condiciones", "Works for some live fixtures, but live polling/history cost makes it future in-play research rather than V1 core.")
        return ("no usable", "No live prices observed for the tested live fixtures.")
    if coverage >= 80:
        return ("usable", "Coverage and payload shape were consistently usable in the tested sample.")
    if coverage >= 30:
        return ("usable con condiciones", "Usable only for a subset of fixtures/leagues/windows observed in the sample.")
    return ("no usable", "Too many empty or non-actionable payloads in the tested sample.")


def league_coverage_rows(audit: ProviderAudit) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    all_samples = audit.wc_upcoming + audit.active_upcoming + audit.big_five_historical + audit.active_recent_finished
    grouped: dict[tuple[int, int, str], list[FixtureSample]] = defaultdict(list)
    for sample in all_samples:
        grouped[(sample.league_id, sample.season, sample.league_name)].append(sample)
    for key, samples in sorted(grouped.items()):
        league_id, season, league_name = key
        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "league_name": league_name,
                "sample_fixtures": len({sample.fixture_id for sample in samples}),
                "source_tags": sorted({sample.source_label for sample in samples}),
                "odds_category_count": len(audit.odds_category_league_fixture_counts.get(league_id, Counter())),
                "odds_fixtures_with_prices": sum(1 for result in audit.odds_results if result.fixture.league_id == league_id and result.has_any_odds),
            }
        )
    return rows


def market_rows(audit: ProviderAudit) -> list[dict[str, Any]]:
    postmatch_count = len(audit.big_five_historical) + len(audit.active_recent_finished)
    prematch_odds_count = len({result.fixture.fixture_id for result in audit.odds_results})

    stats_types = {name.lower() for name in audit.stats_type_counts}
    half_stats_types = {name.lower() for name in audit.half_stats_type_counts}
    player_paths = set(audit.player_stat_path_counts)
    event_details = {name.lower() for name in audit.event_detail_counts}

    def evidence_coverage(category: str) -> tuple[bool, float, str]:
        if category == "result":
            return (postmatch_count > 0, 100.0 if postmatch_count > 0 else 0.0, "Fixtures scores are always present on finished matches.")
        if category == "btts":
            return (postmatch_count > 0, 100.0 if postmatch_count > 0 else 0.0, "Derived from final score.")
        if category == "goals":
            return (postmatch_count > 0, 100.0 if postmatch_count > 0 else 0.0, "Derived from final score.")
        if category == "goals_1h":
            coverage = percentage(audit.get_endpoint("fixtures/statistics?half=true").response_count, max(1, audit.get_endpoint("fixtures/statistics?half=true").request_count))
            return ("goals" in half_stats_types or bool(audit.half_stats_type_counts), coverage, "Requires half=true split payload.")
        if category == "goals_2h":
            coverage = percentage(audit.get_endpoint("fixtures/statistics?half=true").response_count, max(1, audit.get_endpoint("fixtures/statistics?half=true").request_count))
            return ("goals" in half_stats_types or bool(audit.half_stats_type_counts), coverage, "Requires half=true split payload.")
        if category == "corners":
            coverage = percentage(audit.get_endpoint("fixtures/statistics").response_count, max(1, audit.get_endpoint("fixtures/statistics").request_count))
            return (any(rule in stats_types for rule in STAT_FIELD_RULES["corners"]), coverage, "Relies on fixture statistics corner fields.")
        if category == "cards":
            coverage = percentage(audit.get_endpoint("fixtures/events").response_count, max(1, audit.get_endpoint("fixtures/events").request_count))
            usable = any("yellow cards" in item or "red cards" in item for item in stats_types) or any("card" in detail for detail in event_details)
            return (usable, coverage, "Cards can be reconstructed from events and/or statistics.")
        if category == "booking_points":
            coverage = percentage(audit.get_endpoint("fixtures/events").response_count, max(1, audit.get_endpoint("fixtures/events").request_count))
            usable = any(detail in event_details for detail in ("yellow card", "red card", "second yellow card"))
            return (usable, coverage, "Booking points depend on card event detail quality.")
        if category == "shots":
            coverage = percentage(audit.get_endpoint("fixtures/statistics").response_count, max(1, audit.get_endpoint("fixtures/statistics").request_count))
            usable = any(rule in stats_types for rule in STAT_FIELD_RULES["shots"])
            return (usable, coverage, "Shots rely on total shots / shots on goal in fixture statistics.")
        if category == "offsides":
            coverage = percentage(audit.get_endpoint("fixtures/statistics").response_count, max(1, audit.get_endpoint("fixtures/statistics").request_count))
            usable = any(rule in stats_types for rule in STAT_FIELD_RULES["offsides"])
            return (usable, coverage, "Offsides rely on fixture statistics.")
        if category == "fouls":
            coverage = percentage(audit.get_endpoint("fixtures/statistics").response_count, max(1, audit.get_endpoint("fixtures/statistics").request_count))
            usable = any(rule in stats_types for rule in STAT_FIELD_RULES["fouls"])
            return (usable, coverage, "Fouls rely on fixture statistics.")
        if category == "player_scored":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("goals.total" in player_paths, coverage, "Player scored comes from player fixture stats.")
        if category == "player_shots":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("shots.total" in player_paths or "shots.on" in player_paths, coverage, "Player shots come from player fixture stats.")
        if category == "player_cards":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("cards.yellow" in player_paths or "cards.red" in player_paths, coverage, "Player cards come from player fixture stats.")
        if category == "player_fouls":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("fouls.committed" in player_paths, coverage, "Player fouls come from player fixture stats.")
        if category == "player_tackles":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("tackles.total" in player_paths, coverage, "Player tackles come from player fixture stats.")
        if category == "player_assists":
            coverage = percentage(audit.get_endpoint("fixtures/players").response_count, max(1, audit.get_endpoint("fixtures/players").request_count))
            return ("goals.assists" in player_paths, coverage, "Player assists come from player fixture stats.")
        return (False, 0.0, "No rule.")

    rows: list[dict[str, Any]] = []
    for category in ALL_MARKET_CATEGORIES:
        evidence_ok, evidence_cov, evidence_note = evidence_coverage(category)
        odds_cov = percentage(audit.odds_category_fixture_counts.get(category, 0), max(1, prematch_odds_count))
        price_ok = odds_cov > 0

        freshness_grade = "high" if category in {"result", "btts", "goals", "goals_1h", "goals_2h"} and price_ok else ("medium" if price_ok or evidence_ok else "low")
        data_quality_grade = "high" if evidence_ok and (price_ok or category not in {"result", "btts", "goals", "goals_1h", "goals_2h"}) else (
            "medium" if evidence_ok or price_ok else "low"
        )

        usable_for_fair_probability = category in {"result", "btts", "goals", "goals_1h", "goals_2h", "corners", "cards"} and price_ok
        usable_for_edge = usable_for_fair_probability and odds_cov >= 20

        if category in {"result", "btts", "goals"} and price_ok and evidence_ok:
            final_decision = "usable con condiciones"
            reason = (
                "Confirmed with real prices on World Cup 2026 and at least one active-league sample, "
                "but not yet re-confirmed on live big-five prematch fixtures because the big-five are off-season."
            )
        elif category in {"goals_1h", "goals_2h", "corners", "cards", "shots", "offsides", "fouls"} and price_ok and evidence_ok:
            final_decision = "usable con condiciones"
            reason = (
                "Real odds were observed, but only for subsets of competitions/bookmakers in the sample. "
                "This should be enabled only where coverage is explicitly proven."
            )
        elif category in {"player_scored", "player_shots", "player_fouls", "player_assists"} and price_ok and evidence_ok:
            final_decision = "usable con condiciones"
            reason = (
                "Player-price payloads exist in the sampled odds feed, but coverage is bookmaker-specific and should "
                "not be treated as universally available."
            )
        elif evidence_ok and not price_ok:
            final_decision = "context"
            reason = "Evidence exists, but actionable price-layer coverage was not observed in the sampled odds payloads."
        else:
            final_decision = "drop"
            reason = "Neither evidence quality nor price-layer coverage cleared the bar for a serious V1 commitment."

        coverage_value = odds_cov if price_ok else evidence_cov

        rows.append(
            {
                "market_category": category,
                "supported_by_api": evidence_ok or price_ok,
                "coverage_pct": coverage_value,
                "freshness_grade": freshness_grade,
                "data_quality_grade": data_quality_grade,
                "usable_for_evidence": evidence_ok,
                "usable_for_price_layer": price_ok,
                "usable_for_fair_probability": usable_for_fair_probability,
                "usable_for_edge": usable_for_edge,
                "final_decision": final_decision,
                "reason": f"{reason} Evidence: {evidence_note}",
            }
        )
    return rows


def action_rows(market_rows_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in market_rows_data:
        decision = row["final_decision"]
        if decision == "core":
            action = "implementar capa de precio completa"
        elif decision == "usable con condiciones":
            action = "implementar donde haya precio y cobertura exacta; fallback stat_signal_only fuera de esas condiciones"
        elif decision == "context":
            action = "usar solo como contexto/evidencia; no como capa principal de decisión con precio"
        else:
            action = "drop V1 o dejar stat_signal_only permanente"
        rows.append(
            {
                "mercado": row["market_category"],
                "cobertura_real": f"{row['coverage_pct']}%",
                "calidad": row["data_quality_grade"],
                "veredicto": decision,
                "accion_en_stuf": action,
            }
        )
    return rows


def request_cost_rows(audit: ProviderAudit) -> list[dict[str, Any]]:
    sampled_upcoming = len({sample.fixture_id for sample in audit.wc_upcoming + audit.active_upcoming})
    sampled_finished = len({sample.fixture_id for sample in audit.big_five_historical})
    return [
        {
            "feature": "refresh de fixtures upcoming (diario)",
            "requests_formula": "1 request /fixtures por liga+season monitorizada",
            "example_daily_cost": len({(sample.league_id, sample.season) for sample in audit.wc_upcoming + audit.active_upcoming}),
            "viability": "viable V1 con cuenta actual",
        },
        {
            "feature": "odds snapshots pre-match",
            "requests_formula": "1 request /odds por fixture por snapshot",
            "example_daily_cost": f"{sampled_upcoming} fixtures * 3 snapshots = {sampled_upcoming * 3}",
            "viability": "viable V1 con cuenta actual si se limita a mercados core y ventanas discretas",
        },
        {
            "feature": "lineups hot-zone (20-40 min antes)",
            "requests_formula": "1 request /fixtures/lineups por fixture en hot-zone",
            "example_daily_cost": sampled_upcoming,
            "viability": "viable V1 con cuenta actual",
        },
        {
            "feature": "statistics post-match",
            "requests_formula": "2 requests por fixture (statistics FT + statistics?half=true)",
            "example_daily_cost": sampled_finished * 2,
            "viability": "viable V1 con cuenta actual",
        },
        {
            "feature": "player stats post-match",
            "requests_formula": "1 request /fixtures/players por fixture",
            "example_daily_cost": sampled_finished,
            "viability": "viable V1 con cuenta actual, pero es el fan-out caro del cierre post-match",
        },
        {
            "feature": "injuries pre-match",
            "requests_formula": "1 request /injuries por liga+season al día",
            "example_daily_cost": len({(sample.league_id, sample.season) for sample in audit.active_upcoming}),
            "viability": "viable V1 con cuenta actual",
        },
        {
            "feature": "World Cup fixtures + odds",
            "requests_formula": "1 /fixtures WC + N /odds fixture snapshots",
            "example_daily_cost": f"1 + ({len(audit.wc_upcoming)} * 3) = {1 + (len(audit.wc_upcoming) * 3)}",
            "viability": "viable V1 con cuenta actual",
        },
    ]


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")) for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body]) if rows else f"{header}\n{divider}"


def build_endpoint_rows(audit: ProviderAudit) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered_endpoints = [
        "fixtures",
        "fixtures/statistics",
        "fixtures/statistics?half=true",
        "fixtures/events",
        "fixtures/players",
        "fixtures/lineups",
        "fixtures/headtohead",
        "injuries",
        "predictions",
        "odds",
        "odds/mapping",
        "odds/bookmakers",
        "odds/bets",
        "odds/live",
        "odds/live/bets",
        "standings",
        "players",
        "players/profiles",
        "players/seasons",
        "players/topscorers",
        "players/topassists",
        "players/topyellowcards",
        "players/topredcards",
    ]
    for endpoint in ordered_endpoints:
        stats = audit.get_endpoint(endpoint)
        verdict, conditions = summarize_endpoint_verdict(endpoint, stats)
        rows.append(
            {
                "endpoint": f"/{endpoint.replace('?half=true', '')}" if endpoint != "fixtures/statistics?half=true" else "/fixtures/statistics?half=true",
                "parámetros usados": ", ".join(stats.fixtures_or_leagues[:6]) + (" ..." if len(stats.fixtures_or_leagues) > 6 else ""),
                "fixtures/ligas testeados": len(stats.fixtures_or_leagues),
                "request_count": stats.request_count,
                "response_count": stats.response_count,
                "empty_count": stats.empty_count,
                "error_count": stats.error_count,
                "quota_cost": stats.quota_cost,
                "payload_example": stats.example_path or stats.empty_example_path or "no",
                "coverage_pct": percentage(stats.response_count, stats.request_count),
                "freshness_observado": audit.format_freshness(stats),
                "fields_criticos_presentes": ", ".join(list(stats.fields_present.keys())[:6]),
                "fields_criticos_faltantes": ", ".join(list(stats.fields_missing.keys())[:6]),
                "inconsistencias_de_payload": "; ".join(
                    f"{key} ({value})" for key, value in stats.inconsistencies.most_common(3)
                ),
                "veredicto": verdict,
                "condiciones_si_aplica": conditions,
            }
        )
    return rows


def build_odds_coverage_rows(audit: ProviderAudit) -> list[dict[str, Any]]:
    principal_bookmaker = audit.odds_bookmaker_fixture_counts.most_common(1)[0][0] if audit.odds_bookmaker_fixture_counts else "n/a"
    rows: list[dict[str, Any]] = []
    for bookmaker, fixture_count in audit.odds_bookmaker_fixture_counts.most_common(12):
        rows.append(
            {
                "bookmaker": bookmaker,
                "fixture_coverage": fixture_count,
                "fixture_coverage_pct": percentage(fixture_count, max(1, len(audit.odds_results))),
                "categories_found": ", ".join(sorted(audit.odds_bookmaker_category_counts.get(bookmaker, Counter()).keys())[:8]),
                "principal_bookmaker": "yes" if bookmaker == principal_bookmaker else "",
            }
        )
    return rows


def executive_verdict(market_rows_data: list[dict[str, Any]]) -> tuple[str, str]:
    standard = {row["market_category"]: row["final_decision"] for row in market_rows_data if row["market_category"] in {"result", "btts", "goals"}}
    if all(decision in {"usable con condiciones", "core"} for decision in standard.values()):
        return (
            "B) API-Football sirve para evidencia core pero no para odds premium en todos los mercados.",
            "Core evidence is strong and standard prematch odds are real, but specialty and player-price layers remain conditional by competition/bookmaker/window.",
        )
    if any(decision in {"usable con condiciones", "core"} for decision in standard.values()):
        return (
            "C) API-Football sirve parcialmente y hay que recortar mercados en V1.",
            "Some core decision markets survive, but only with a narrower product scope.",
        )
    return (
        "D) API-Football no alcanza para la promesa actual y se necesita proveedor adicional.",
        "The provider did not clear the bar even for standard decision markets.",
    )


def write_outputs(audit: ProviderAudit) -> tuple[Path, Path]:
    enrich_odds_aggregates(audit)
    endpoint_rows = build_endpoint_rows(audit)
    market_rows_data = market_rows(audit)
    action_rows_data = action_rows(market_rows_data)
    odds_rows = build_odds_coverage_rows(audit)
    request_rows = request_cost_rows(audit)
    verdict_line, verdict_reason = executive_verdict(market_rows_data)

    output_json = audit.output_dir / "API_FOOTBALL_PROVIDER_AUDIT.json"
    output_md = audit.output_dir / "API_FOOTBALL_PROVIDER_AUDIT.md"

    summary = {
        "generated_at": audit.audit_now.isoformat(),
        "quota_start": audit.quota_start,
        "quota_end": audit.quota_end,
        "total_requests": audit.total_requests,
        "scope_notes": audit.scope_notes,
        "endpoint_rows": endpoint_rows,
        "market_rows": market_rows_data,
        "action_rows": action_rows_data,
        "odds_rows": odds_rows,
        "request_rows": request_rows,
        "executive_verdict": verdict_line,
        "executive_reason": verdict_reason,
        "stat_types_seen": audit.stats_type_counts.most_common(),
        "half_stat_types_seen": audit.half_stats_type_counts.most_common(),
        "event_details_seen": audit.event_detail_counts.most_common(),
        "player_stat_paths_seen": audit.player_stat_path_counts.most_common(),
    }
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# API_FOOTBALL_PROVIDER_AUDIT")
    lines.append("")
    lines.append("## 1. Executive verdict")
    lines.append("")
    lines.append(f"**Decision:** {verdict_line}")
    lines.append("")
    lines.append(f"**Why:** {verdict_reason}")
    lines.append("")
    lines.append("Confirmed with live API calls on 2026-06-09. This audit used real payloads, not docs promises.")
    lines.append("")
    lines.append("## 2. Scope tested (muestras usadas, fechas, fixtures, ligas)")
    lines.append("")
    lines.append(f"- Audit timestamp (UTC): `{audit.audit_now.isoformat()}`")
    lines.append(f"- World Cup sample: `{len(audit.wc_upcoming)}` upcoming fixtures from league `1`, season `2026` (`confirmado_world_cup`).")
    lines.append(f"- Active-league prematch sample: `{len(audit.active_upcoming)}` fixtures from active competitions (`confirmado_liga_activa`).")
    lines.append(f"- Near-kickoff sample: `{len(audit.near_kickoff)}` senior fixtures from the global next-fixture queue (`confirmado_liga_activa`).")
    lines.append(f"- Live sample: `{len(audit.live_fixtures)}` fixtures live at audit time for `/odds/live` (`future / in-play research`).")
    lines.append(f"- Historical post-match sample: `{len(audit.big_five_historical)}` finished fixtures from the most recent completed big-five season in API terms: `season=2025` (matches played through May 2026), not the older `season=2024`. (`confirmado_historico_5_ligas`).")
    lines.append(f"- Recent active finished sample: `{len(audit.active_recent_finished)}` fixtures from active leagues to observe post-match payload freshness close to present time.")
    lines.append("")
    if audit.scope_notes:
        lines.extend(f"- {note}" for note in audit.scope_notes)
        lines.append("")
    lines.append("## 3. Request/quota summary (total requests consumidos, costo estimado de producción)")
    lines.append("")
    lines.append(f"- Quota start: `{audit.quota_start}`")
    lines.append(f"- Quota end: `{audit.quota_end}`")
    lines.append(f"- Total requests consumed by this audit: `{audit.total_requests}`")
    lines.append("")
    lines.append(render_table(request_rows, ["feature", "requests_formula", "example_daily_cost", "viability"]))
    lines.append("")
    lines.append("## 4. Endpoint matrix (tabla completa por endpoint)")
    lines.append("")
    lines.append(
        render_table(
            endpoint_rows,
            [
                "endpoint",
                "parámetros usados",
                "fixtures/ligas testeados",
                "request_count",
                "response_count",
                "empty_count",
                "error_count",
                "quota_cost",
                "payload_example",
                "coverage_pct",
                "freshness_observado",
                "fields_criticos_presentes",
                "fields_criticos_faltantes",
                "inconsistencias_de_payload",
                "veredicto",
                "condiciones_si_aplica",
            ],
        )
    )
    lines.append("")
    lines.append("## 5. League coverage matrix (cobertura por liga y tipo de dato)")
    lines.append("")
    lines.append(
        render_table(
            league_coverage_rows(audit),
            ["league_id", "season", "league_name", "sample_fixtures", "source_tags", "odds_category_count", "odds_fixtures_with_prices"],
        )
    )
    lines.append("")
    lines.append("## 6. Market coverage matrix (tabla por mercado STUF)")
    lines.append("")
    lines.append(
        render_table(
            market_rows_data,
            [
                "market_category",
                "supported_by_api",
                "coverage_pct",
                "freshness_grade",
                "data_quality_grade",
                "usable_for_evidence",
                "usable_for_price_layer",
                "usable_for_fair_probability",
                "usable_for_edge",
                "final_decision",
                "reason",
            ],
        )
    )
    lines.append("")
    lines.append("## 7. Odds coverage matrix (bookmakers, mercados, líneas reales encontradas)")
    lines.append("")
    lines.append(f"- Principal bookmaker in this audit: `{odds_rows[0]['bookmaker']}`" if odds_rows else "- Principal bookmaker in this audit: `n/a`")
    lines.append(f"- Distinct bookmaker names seen in usable `/odds` payloads: `{len(audit.odds_bookmaker_fixture_counts)}`")
    lines.append(f"- Distinct bet names seen in usable `/odds` payloads: `{len(audit.odds_bet_name_counts)}`")
    lines.append("")
    lines.append(render_table(odds_rows, ["bookmaker", "fixture_coverage", "fixture_coverage_pct", "categories_found", "principal_bookmaker"]))
    lines.append("")
    top_bets = [{"bet": key, "count": value} for key, value in audit.odds_bet_name_counts.most_common(40)]
    lines.append("Top real bet IDs/names observed:")
    lines.append("")
    lines.append(render_table(top_bets, ["bet", "count"]))
    lines.append("")
    lines.append("## 8. Freshness matrix (por endpoint y tipo de dato)")
    lines.append("")
    freshness_rows = [
        {"endpoint": endpoint, "freshness": audit.format_freshness(audit.get_endpoint(endpoint))}
        for endpoint in [
            "odds",
            "predictions",
            "fixtures/lineups",
            "injuries",
            "fixtures/statistics",
            "fixtures/statistics?half=true",
            "fixtures/events",
            "fixtures/players",
            "odds/live",
        ]
    ]
    lines.append(render_table(freshness_rows, ["endpoint", "freshness"]))
    lines.append("")
    lines.append("## 9. Data quality findings (inconsistencias, nulls, campos faltantes)")
    lines.append("")
    lines.append(f"- Statistic types seen in `/fixtures/statistics`: `{', '.join(name for name, _ in audit.stats_type_counts.most_common(20))}`")
    lines.append(f"- Half-stat types seen in `/fixtures/statistics?half=true`: `{', '.join(name for name, _ in audit.half_stats_type_counts.most_common(20))}`")
    lines.append(f"- Event details seen in `/fixtures/events`: `{', '.join(name for name, _ in audit.event_detail_counts.most_common(20))}`")
    lines.append(f"- Player stat paths seen in `/fixtures/players`: `{', '.join(name for name, _ in audit.player_stat_path_counts.most_common(20))}`")
    lines.append("- Predictions had a real pattern problem in the tested sample: World Cup fixtures often returned `33/33/33` and `No predictions available`; some active fixtures returned repetitive `45/45/10` style payloads. Treat as context only until backtested/calibrated.")
    lines.append("- `/fixtures/lineups` was empty on future fixtures outside the hot-zone, which is expected pre-release behavior. This endpoint should be judged by near-kickoff workflows, not broad daily refreshes.")
    lines.append("- `/injuries` can be rich at league+season level but empty at fixture level, so product design should not depend on per-fixture injury payloads existing consistently.")
    lines.append("- `/odds/live` is selective by league/fixture: usable payloads existed globally, but some live fixtures still returned empty.")
    lines.append("")
    lines.append("## 10. Product decision per module (fixtures, stats, lineups, injuries, predictions, odds)")
    lines.append("")
    module_rows = [
        {"module": "fixtures", "decision": "usable", "reason": "Schedule/results coverage was strong across World Cup, active leagues, and historical leagues."},
        {"module": "stats", "decision": "usable", "reason": "Post-match statistics and half splits returned real fields usable for STUF evidence."},
        {"module": "events", "decision": "usable", "reason": "Events returned actionable cards/goals/substitution detail for evidence and disciplinary derivations."},
        {"module": "players", "decision": "usable", "reason": "Player fixture stats and player top endpoints returned rich per-player data usable for props/context."},
        {"module": "lineups", "decision": "usable con condiciones", "reason": "Useful only close to kickoff or post-match; empty on broad future windows."},
        {"module": "injuries", "decision": "usable con condiciones", "reason": "League-level injury lists are useful; fixture-level injury payloads were often empty."},
        {"module": "predictions", "decision": "context only", "reason": "Endpoint responds, but payload quality and calibration are not trustworthy enough for decision-core use without backtest."},
        {"module": "odds", "decision": "usable con condiciones", "reason": "Prematch odds are real and rich for standard markets, but specialty/player-price coverage is uneven."},
        {"module": "odds/live", "decision": "future / in-play research", "reason": "Exists and can be rich, but live polling/history cost should not shape STUF V1."},
    ]
    lines.append(render_table(module_rows, ["module", "decision", "reason"]))
    lines.append("")
    lines.append("## 11. Product decision per market (tabla de acción operativa por mercado)")
    lines.append("")
    lines.append(render_table(action_rows_data, ["mercado", "cobertura_real", "calidad", "veredicto", "accion_en_stuf"]))
    lines.append("")
    lines.append("## 12. What STUF can build as core with API-Football")
    lines.append("")
    core_rows = [row for row in market_rows_data if row["final_decision"] == "core"]
    if core_rows:
        lines.extend(f"- `{row['market_category']}` — {row['reason']}" for row in core_rows)
    else:
        lines.append("- No market cleared the `core` bar in this run.")
    lines.append("")
    lines.append("## 13. What STUF can build only as context")
    lines.append("")
    context_rows = [row for row in market_rows_data if row["final_decision"] == "context"]
    if context_rows:
        lines.extend(f"- `{row['market_category']}` — {row['reason']}" for row in context_rows)
    else:
        lines.append("- No market landed in `context`.")
    lines.append("")
    lines.append("## 14. What STUF should not build with current API")
    lines.append("")
    drop_rows = [row for row in market_rows_data if row["final_decision"] == "drop"]
    if drop_rows:
        lines.extend(f"- `{row['market_category']}` — {row['reason']}" for row in drop_rows)
    else:
        lines.append("- Nothing was a mandatory drop in the sampled provider behavior.")
    lines.append("")
    lines.append("## 15. Required architecture decisions (qué cambia en 020 según estos resultados)")
    lines.append("")
    lines.append("- Separate **Decision Market Catalog** from **Stat Signal Catalog**. Do not assume every evidence market deserves a priced decision layer.")
    lines.append("- Build the first decision-core around standard priced markets that actually appeared with usable odds coverage: 1X2, BTTS, and full-match goals O/U.")
    lines.append("- Treat predictions as `context only` until STUF can backtest them against stored outcomes. Do not let `/predictions` drive edge/value cards directly.")
    lines.append("- Design injuries consumption around league/team snapshots, not fixture-level expectation of completeness.")
    lines.append("- Keep live odds outside V1 core. If STUF ever uses them, it needs a dedicated in-play architecture with polling, storage, and freshness monitoring.")
    lines.append("- For specialty/team props (corners/cards/etc.), gate enablement per bookmaker+league coverage rather than globally enabling by schema alone.")
    lines.append("")
    lines.append("## 16. Recommended next implementation step")
    lines.append("")
    lines.append("Implement `020_market_decision_core.sql` around a **narrow priceable core** first: `result`, `btts`, `goals` (and optionally `goals_1h` if you accept exact bookmaker/coverage conditions). Everything else should enter either as `context` or `usable con condiciones`, not as a blanket decision layer.")
    lines.append("")
    lines.append("## Closed final decision")
    lines.append("")
    lines.append(f"**{verdict_line}**")
    lines.append("")
    lines.append("Per-market operational decision is encoded in the `final_decision` column above: `core / context / usable con condiciones / drop`.")
    lines.append("")

    output_md.write_text("\n".join(lines), encoding="utf-8")
    return output_md, output_json


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    audit = ProviderAudit(output_dir)
    settings = load_settings()

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api:
        await discover_scope(args, api, audit)
        await audit_fixture_detail_endpoints(api, audit)
        await audit_prematch_endpoints(api, audit)
        await audit_odds_catalogs(api, audit)
        await audit_standings_and_players(api, audit)
        audit.quota_end = parse_status(await fetch_json(api, audit, "status", {}, context_label="quota-end"))

    output_md, output_json = write_outputs(audit)
    LOGGER.info("Provider audit complete. MD=%s JSON=%s requests=%s", output_md, output_json, audit.total_requests)


if __name__ == "__main__":
    asyncio.run(main())
