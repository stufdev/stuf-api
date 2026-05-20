from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pipeline_core import StufRepository, utcnow


@dataclass(frozen=True)
class RefereeMatchContext:
    fixture_id: int
    referee_id: int
    league_id: int
    season: int
    played_at: str
    home_cards: float
    away_cards: float
    total_cards: float
    home_booking_points: float
    away_booking_points: float
    total_booking_points: float
    total_yellow_cards: float
    total_red_cards: float
    total_fouls: float
    penalties: float


@dataclass(frozen=True)
class RefereeMarketRule:
    key: str
    category: str
    sort_order: int
    value_getter: Callable[[RefereeMatchContext], float]
    predicate: Callable[[RefereeMatchContext], bool]


def _num(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _over(value_getter: Callable[[RefereeMatchContext], float], line: float) -> Callable[[RefereeMatchContext], bool]:
    return lambda context: value_getter(context) > line


REFEREE_MARKET_RULES: tuple[RefereeMarketRule, ...] = (
    RefereeMarketRule("MATCH_OVER_15_BOOKING_POINTS", "booking_points", 10, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 15)),
    RefereeMarketRule("MATCH_OVER_25_BOOKING_POINTS", "booking_points", 20, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 25)),
    RefereeMarketRule("MATCH_OVER_35_BOOKING_POINTS", "booking_points", 30, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 35)),
    RefereeMarketRule("MATCH_OVER_45_BOOKING_POINTS", "booking_points", 40, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 45)),
    RefereeMarketRule("MATCH_OVER_55_BOOKING_POINTS", "booking_points", 50, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 55)),
    RefereeMarketRule("MATCH_OVER_65_BOOKING_POINTS", "booking_points", 60, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 65)),
    RefereeMarketRule("EACH_TEAM_OVER_5_BOOKING_POINTS", "booking_points", 70, lambda c: min(c.home_booking_points, c.away_booking_points), lambda c: c.home_booking_points > 5 and c.away_booking_points > 5),
    RefereeMarketRule("EACH_TEAM_OVER_15_BOOKING_POINTS", "booking_points", 80, lambda c: min(c.home_booking_points, c.away_booking_points), lambda c: c.home_booking_points > 15 and c.away_booking_points > 15),
    RefereeMarketRule("EACH_TEAM_OVER_25_BOOKING_POINTS", "booking_points", 90, lambda c: min(c.home_booking_points, c.away_booking_points), lambda c: c.home_booking_points > 25 and c.away_booking_points > 25),
    RefereeMarketRule("MATCH_OVER_1_5_CARDS", "cards", 100, lambda c: c.total_cards, _over(lambda c: c.total_cards, 1.5)),
    RefereeMarketRule("MATCH_OVER_2_5_CARDS", "cards", 110, lambda c: c.total_cards, _over(lambda c: c.total_cards, 2.5)),
    RefereeMarketRule("MATCH_OVER_3_5_CARDS", "cards", 120, lambda c: c.total_cards, _over(lambda c: c.total_cards, 3.5)),
    RefereeMarketRule("MATCH_OVER_4_5_CARDS", "cards", 130, lambda c: c.total_cards, _over(lambda c: c.total_cards, 4.5)),
    RefereeMarketRule("MATCH_OVER_5_5_CARDS", "cards", 140, lambda c: c.total_cards, _over(lambda c: c.total_cards, 5.5)),
    RefereeMarketRule("MATCH_OVER_6_5_CARDS", "cards", 150, lambda c: c.total_cards, _over(lambda c: c.total_cards, 6.5)),
    RefereeMarketRule("EACH_TEAM_OVER_0_5_CARDS", "cards", 160, lambda c: min(c.home_cards, c.away_cards), lambda c: c.home_cards > 0.5 and c.away_cards > 0.5),
    RefereeMarketRule("EACH_TEAM_OVER_1_5_CARDS", "cards", 170, lambda c: min(c.home_cards, c.away_cards), lambda c: c.home_cards > 1.5 and c.away_cards > 1.5),
    RefereeMarketRule("EACH_TEAM_OVER_2_5_CARDS", "cards", 180, lambda c: min(c.home_cards, c.away_cards), lambda c: c.home_cards > 2.5 and c.away_cards > 2.5),
)


def _context_from_fact(row: dict) -> RefereeMatchContext:
    return RefereeMatchContext(
        fixture_id=int(row["fixture_id"]),
        referee_id=int(row["referee_id"]),
        league_id=int(row["league_id"]),
        season=int(row["season"]),
        played_at=str(row.get("played_at") or ""),
        home_cards=_num(row.get("home_cards")),
        away_cards=_num(row.get("away_cards")),
        total_cards=_num(row.get("total_cards")),
        home_booking_points=_num(row.get("home_booking_points")),
        away_booking_points=_num(row.get("away_booking_points")),
        total_booking_points=_num(row.get("total_booking_points")),
        total_yellow_cards=_num(row.get("total_yellow_cards")),
        total_red_cards=_num(row.get("total_red_cards")),
        total_fouls=_num(row.get("total_fouls")),
        penalties=_num(row.get("penalties")),
    )


def _percentage(hits: int, sample: int) -> float:
    return round((hits / sample) * 100, 2) if sample else 0.0


def _current_streak(rows: list[dict]) -> int:
    streak = 0
    for row in rows:
        if not row["result"]:
            break
        streak += 1
    return streak


def _longest_streak(rows: list[dict]) -> int:
    longest = 0
    current = 0
    for row in rows:
        if row["result"]:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _card_count(row: dict) -> int:
    yellow = int(_num(row.get("yellow_cards")))
    red = int(_num(row.get("red_cards")))
    cards = int(_num(row.get("cards")))
    return cards if cards > 0 else yellow + red


def _count_penalties(events: list[dict]) -> int:
    penalties = 0
    for event in events:
        event_text = " ".join(
            str(part or "")
            for part in (event.get("type"), event.get("detail"), event.get("comments"))
        ).lower()
        if "penalty" in event_text and "shootout" not in event_text:
            penalties += 1
    return penalties


def _build_referee_fixture_fact_rows(
    fixtures: list[dict],
    stats_rows: list[dict],
    event_rows: list[dict],
) -> tuple[list[dict], int]:
    stats_by_fixture: dict[int, dict[int, dict]] = {}
    for row in stats_rows:
        fixture_id = row.get("fixture_id")
        team_id = row.get("team_id")
        if fixture_id is None or team_id is None:
            continue
        fixture_bucket = stats_by_fixture.setdefault(int(fixture_id), {})
        fixture_bucket[int(team_id)] = row

    events_by_fixture: dict[int, list[dict]] = {}
    for row in event_rows:
        fixture_id = row.get("fixture_id")
        if fixture_id is None:
            continue
        events_by_fixture.setdefault(int(fixture_id), []).append(row)

    built_rows: list[dict] = []
    skipped = 0
    now_iso = utcnow().isoformat()

    for fixture in fixtures:
        fixture_id = int(fixture["id"])
        home_team_id = fixture.get("home_team_id")
        away_team_id = fixture.get("away_team_id")
        referee_id = fixture.get("referee_id")
        if home_team_id is None or away_team_id is None or referee_id is None:
            skipped += 1
            continue

        fixture_stats = stats_by_fixture.get(fixture_id) or {}
        home_stats = fixture_stats.get(int(home_team_id)) or {}
        away_stats = fixture_stats.get(int(away_team_id)) or {}
        if not home_stats or not away_stats:
            skipped += 1
            continue

        home_cards = _card_count(home_stats)
        away_cards = _card_count(away_stats)
        home_yellows = int(_num(home_stats.get("yellow_cards")))
        away_yellows = int(_num(away_stats.get("yellow_cards")))
        home_reds = int(_num(home_stats.get("red_cards")))
        away_reds = int(_num(away_stats.get("red_cards")))
        home_booking_points = int(_num(home_stats.get("booking_points")))
        away_booking_points = int(_num(away_stats.get("booking_points")))
        total_fouls = int(_num(home_stats.get("fouls"))) + int(_num(away_stats.get("fouls")))
        penalties = _count_penalties(events_by_fixture.get(fixture_id) or [])

        built_rows.append(
            {
                "fixture_id": fixture_id,
                "referee_id": int(referee_id),
                "league_id": fixture.get("league_id"),
                "season": fixture.get("season"),
                "played_at": fixture.get("date"),
                "home_team_id": int(home_team_id),
                "away_team_id": int(away_team_id),
                "home_cards": home_cards,
                "away_cards": away_cards,
                "total_cards": home_cards + away_cards,
                "home_booking_points": home_booking_points,
                "away_booking_points": away_booking_points,
                "total_booking_points": home_booking_points + away_booking_points,
                "total_yellow_cards": home_yellows + away_yellows,
                "total_red_cards": home_reds + away_reds,
                "total_fouls": total_fouls,
                "penalties": penalties,
                "updated_at": now_iso,
            }
        )

    return built_rows, skipped


def rebuild_referee_market_rollups(repository: StufRepository, referee_id: int, league_id: int, season: int) -> None:
    facts = repository.get_referee_fixture_fact_rows(referee_id, league_id, season)
    contexts = [_context_from_fact(row) for row in facts]

    rows = []
    for rule in sorted(REFEREE_MARKET_RULES, key=lambda item: item.sort_order):
        evaluated = [
            {
                "fixture_id": context.fixture_id,
                "played_at": context.played_at,
                "result": bool(rule.predicate(context)),
                "numeric_value": rule.value_getter(context),
            }
            for context in contexts
        ]
        sample = len(evaluated)
        hits = sum(1 for row in evaluated if row["result"])

        rows.append(
            {
                "referee_id": referee_id,
                "league_id": league_id,
                "season": season,
                "market_key": rule.key,
                "category": rule.category,
                "sample": sample,
                "hits": hits,
                "percentage": _percentage(hits, sample),
                "current_streak": _current_streak(evaluated),
                "longest_streak": _longest_streak(evaluated),
                "updated_at": utcnow().isoformat(),
            }
        )

    repository.replace_referee_market_stats(referee_id, league_id, season, rows)


def refresh_referee_stats_for_fixture(repository: StufRepository, fixture_id: int) -> None:
    fact = repository.replace_referee_fixture_fact(fixture_id)
    if not fact:
        return

    referee_id = fact.get("referee_id")
    league_id = fact.get("league_id")
    season = fact.get("season")
    if referee_id and league_id and season:
        rebuild_referee_market_rollups(repository, int(referee_id), int(league_id), int(season))


def rebuild_referee_stats_for_league(repository: StufRepository, league_id: int, season: int) -> None:
    repository.canonicalize_referees_for_league_season(league_id, season)
    fixtures = repository.get_referee_rebuild_fixture_rows(league_id, season)
    fixture_ids = [int(row["id"]) for row in fixtures]
    stats_rows = repository.get_fixture_statistics_rows_for_fixtures(fixture_ids, period="FT") if fixture_ids else []
    event_rows = repository.get_fixture_event_rows_for_fixtures(fixture_ids) if fixture_ids else []
    fact_rows, skipped = _build_referee_fixture_fact_rows(fixtures, stats_rows, event_rows)
    repository.replace_referee_fixture_facts_for_league(league_id, season, fact_rows)

    for referee_id in repository.get_referee_ids_for_league_season(league_id, season):
        rebuild_referee_market_rollups(repository, referee_id, league_id, season)

    repository.logger.info(
        "Rebuilt referee stats league=%s season=%s fixtures=%s facts=%s skipped=%s referees=%s",
        league_id,
        season,
        len(fixtures),
        len(fact_rows),
        skipped,
        len(repository.get_referee_ids_for_league_season(league_id, season)),
    )
