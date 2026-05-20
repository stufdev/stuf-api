from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pipeline_core import StufRepository, is_final_status, utcnow


@dataclass(frozen=True)
class TeamMatchContext:
    fixture_id: int
    team_id: int
    opponent_team_id: int
    league_id: int
    season: int
    played_at: str
    scope: str
    result: str | None
    goals_for: float
    goals_against: float
    total_match_goals: float
    goals_for_1h: float
    goals_against_1h: float
    total_1h_goals: float
    goals_for_2h: float
    goals_against_2h: float
    total_2h_goals: float
    corners_for: float
    corners_against: float
    total_corners: float
    cards_for: float
    cards_against: float
    total_cards: float
    booking_points_for: float
    booking_points_against: float
    total_booking_points: float
    fouls_committed: float
    fouls_won: float
    total_fouls: float
    offsides_for: float
    offsides_against: float
    total_offsides: float
    total_shots_for: float
    total_shots_against: float
    shots_on_target_for: float
    shots_on_target_against: float


@dataclass(frozen=True)
class MarketRule:
    key: str
    category: str
    sort_order: int
    value_getter: Callable[[TeamMatchContext], float]
    predicate: Callable[[TeamMatchContext], bool]


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


def _over(value_getter: Callable[[TeamMatchContext], float], line: float) -> Callable[[TeamMatchContext], bool]:
    return lambda context: value_getter(context) > line


def _under(value_getter: Callable[[TeamMatchContext], float], line: float) -> Callable[[TeamMatchContext], bool]:
    return lambda context: value_getter(context) < line


def _is_win(context: TeamMatchContext) -> bool:
    if context.result:
        return context.result == "win"
    return context.goals_for > context.goals_against


def _is_draw(context: TeamMatchContext) -> bool:
    if context.result:
        return context.result == "draw"
    return context.goals_for == context.goals_against


def _is_loss(context: TeamMatchContext) -> bool:
    if context.result:
        return context.result == "loss"
    return context.goals_for < context.goals_against


MARKET_RULES: tuple[MarketRule, ...] = (
    MarketRule("WIN", "result", 10, lambda c: c.goals_for - c.goals_against, _is_win),
    MarketRule("DRAW", "result", 20, lambda c: c.goals_for - c.goals_against, _is_draw),
    MarketRule("LOSS", "result", 30, lambda c: c.goals_for - c.goals_against, _is_loss),
    MarketRule("UNBEATEN", "result", 40, lambda c: c.goals_for - c.goals_against, lambda c: not _is_loss(c)),
    MarketRule("WINLESS", "result", 50, lambda c: c.goals_for - c.goals_against, lambda c: not _is_win(c)),
    MarketRule("BTTS_YES", "btts", 100, lambda c: min(c.goals_for, c.goals_against), lambda c: c.goals_for > 0 and c.goals_against > 0),
    MarketRule("MATCH_OVER_1_5_GOALS", "goals", 200, lambda c: c.total_match_goals, _over(lambda c: c.total_match_goals, 1.5)),
    MarketRule("MATCH_OVER_2_5_GOALS", "goals", 210, lambda c: c.total_match_goals, _over(lambda c: c.total_match_goals, 2.5)),
    MarketRule("MATCH_OVER_3_5_GOALS", "goals", 220, lambda c: c.total_match_goals, _over(lambda c: c.total_match_goals, 3.5)),
    MarketRule("MATCH_UNDER_2_5_GOALS", "goals", 230, lambda c: c.total_match_goals, _under(lambda c: c.total_match_goals, 2.5)),
    MarketRule("MATCH_UNDER_3_5_GOALS", "goals", 240, lambda c: c.total_match_goals, _under(lambda c: c.total_match_goals, 3.5)),
    MarketRule("TEAM_OVER_0_5_GOALS_FOR", "goals", 250, lambda c: c.goals_for, _over(lambda c: c.goals_for, 0.5)),
    MarketRule("TEAM_OVER_1_5_GOALS_FOR", "goals", 260, lambda c: c.goals_for, _over(lambda c: c.goals_for, 1.5)),
    MarketRule("TEAM_OVER_0_5_GOALS_AGAINST", "goals", 270, lambda c: c.goals_against, _over(lambda c: c.goals_against, 0.5)),
    MarketRule("TEAM_OVER_1_5_GOALS_AGAINST", "goals", 280, lambda c: c.goals_against, _over(lambda c: c.goals_against, 1.5)),
    MarketRule("MATCH_OVER_0_5_1H_GOALS", "half", 300, lambda c: c.total_1h_goals, _over(lambda c: c.total_1h_goals, 0.5)),
    MarketRule("MATCH_OVER_1_5_1H_GOALS", "half", 310, lambda c: c.total_1h_goals, _over(lambda c: c.total_1h_goals, 1.5)),
    MarketRule("TEAM_OVER_0_5_1H_GOALS_FOR", "half", 320, lambda c: c.goals_for_1h, _over(lambda c: c.goals_for_1h, 0.5)),
    MarketRule("TEAM_OVER_0_5_1H_GOALS_AGAINST", "half", 330, lambda c: c.goals_against_1h, _over(lambda c: c.goals_against_1h, 0.5)),
    MarketRule("MATCH_OVER_0_5_2H_GOALS", "half", 340, lambda c: c.total_2h_goals, _over(lambda c: c.total_2h_goals, 0.5)),
    MarketRule("MATCH_OVER_1_5_2H_GOALS", "half", 350, lambda c: c.total_2h_goals, _over(lambda c: c.total_2h_goals, 1.5)),
    MarketRule("TEAM_OVER_0_5_2H_GOALS_FOR", "half", 360, lambda c: c.goals_for_2h, _over(lambda c: c.goals_for_2h, 0.5)),
    MarketRule("TEAM_OVER_0_5_2H_GOALS_AGAINST", "half", 370, lambda c: c.goals_against_2h, _over(lambda c: c.goals_against_2h, 0.5)),
    MarketRule("MATCH_OVER_8_5_CORNERS", "corners", 400, lambda c: c.total_corners, _over(lambda c: c.total_corners, 8.5)),
    MarketRule("MATCH_OVER_9_5_CORNERS", "corners", 410, lambda c: c.total_corners, _over(lambda c: c.total_corners, 9.5)),
    MarketRule("MATCH_OVER_10_5_CORNERS", "corners", 420, lambda c: c.total_corners, _over(lambda c: c.total_corners, 10.5)),
    MarketRule("TEAM_OVER_2_5_CORNERS_FOR", "corners", 430, lambda c: c.corners_for, _over(lambda c: c.corners_for, 2.5)),
    MarketRule("TEAM_OVER_3_5_CORNERS_FOR", "corners", 440, lambda c: c.corners_for, _over(lambda c: c.corners_for, 3.5)),
    MarketRule("TEAM_OVER_4_5_CORNERS_FOR", "corners", 450, lambda c: c.corners_for, _over(lambda c: c.corners_for, 4.5)),
    MarketRule("TEAM_OVER_5_5_CORNERS_FOR", "corners", 460, lambda c: c.corners_for, _over(lambda c: c.corners_for, 5.5)),
    MarketRule("TEAM_OVER_2_5_CORNERS_AGAINST", "corners", 470, lambda c: c.corners_against, _over(lambda c: c.corners_against, 2.5)),
    MarketRule("TEAM_OVER_3_5_CORNERS_AGAINST", "corners", 480, lambda c: c.corners_against, _over(lambda c: c.corners_against, 3.5)),
    MarketRule("MOST_CORNERS", "corners", 490, lambda c: c.corners_for - c.corners_against, lambda c: c.corners_for > c.corners_against),
    MarketRule("MATCH_OVER_15_BOOKING_POINTS", "booking_points", 490, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 15)),
    MarketRule("MATCH_OVER_25_BOOKING_POINTS", "booking_points", 500, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 25)),
    MarketRule("MATCH_OVER_35_BOOKING_POINTS", "booking_points", 510, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 35)),
    MarketRule("MATCH_OVER_45_BOOKING_POINTS", "booking_points", 520, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 45)),
    MarketRule("MATCH_OVER_55_BOOKING_POINTS", "booking_points", 525, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 55)),
    MarketRule("MATCH_OVER_65_BOOKING_POINTS", "booking_points", 526, lambda c: c.total_booking_points, _over(lambda c: c.total_booking_points, 65)),
    MarketRule("TEAM_OVER_15_BOOKING_POINTS_FOR", "booking_points", 530, lambda c: c.booking_points_for, _over(lambda c: c.booking_points_for, 15)),
    MarketRule("TEAM_OVER_25_BOOKING_POINTS_FOR", "booking_points", 540, lambda c: c.booking_points_for, _over(lambda c: c.booking_points_for, 25)),
    MarketRule("TEAM_OVER_15_BOOKING_POINTS_AGAINST", "booking_points", 550, lambda c: c.booking_points_against, _over(lambda c: c.booking_points_against, 15)),
    MarketRule("TEAM_OVER_25_BOOKING_POINTS_AGAINST", "booking_points", 560, lambda c: c.booking_points_against, _over(lambda c: c.booking_points_against, 25)),
    MarketRule("EACH_TEAM_OVER_5_BOOKING_POINTS", "booking_points", 565, lambda c: min(c.booking_points_for, c.booking_points_against), lambda c: c.booking_points_for > 5 and c.booking_points_against > 5),
    MarketRule("EACH_TEAM_OVER_15_BOOKING_POINTS", "booking_points", 570, lambda c: min(c.booking_points_for, c.booking_points_against), lambda c: c.booking_points_for > 15 and c.booking_points_against > 15),
    MarketRule("EACH_TEAM_OVER_25_BOOKING_POINTS", "booking_points", 575, lambda c: min(c.booking_points_for, c.booking_points_against), lambda c: c.booking_points_for > 25 and c.booking_points_against > 25),
    MarketRule("MATCH_OVER_1_5_CARDS", "cards", 590, lambda c: c.total_cards, _over(lambda c: c.total_cards, 1.5)),
    MarketRule("MATCH_OVER_2_5_CARDS", "cards", 600, lambda c: c.total_cards, _over(lambda c: c.total_cards, 2.5)),
    MarketRule("MATCH_OVER_3_5_CARDS", "cards", 610, lambda c: c.total_cards, _over(lambda c: c.total_cards, 3.5)),
    MarketRule("MATCH_OVER_4_5_CARDS", "cards", 620, lambda c: c.total_cards, _over(lambda c: c.total_cards, 4.5)),
    MarketRule("MATCH_OVER_5_5_CARDS", "cards", 625, lambda c: c.total_cards, _over(lambda c: c.total_cards, 5.5)),
    MarketRule("MATCH_OVER_6_5_CARDS", "cards", 626, lambda c: c.total_cards, _over(lambda c: c.total_cards, 6.5)),
    MarketRule("TEAM_OVER_1_5_CARDS_FOR", "cards", 630, lambda c: c.cards_for, _over(lambda c: c.cards_for, 1.5)),
    MarketRule("TEAM_OVER_2_5_CARDS_FOR", "cards", 640, lambda c: c.cards_for, _over(lambda c: c.cards_for, 2.5)),
    MarketRule("OPPONENT_OVER_1_5_CARDS", "cards", 650, lambda c: c.cards_against, _over(lambda c: c.cards_against, 1.5)),
    MarketRule("OPPONENT_OVER_2_5_CARDS", "cards", 660, lambda c: c.cards_against, _over(lambda c: c.cards_against, 2.5)),
    MarketRule("EACH_TEAM_OVER_0_5_CARDS", "cards", 670, lambda c: min(c.cards_for, c.cards_against), lambda c: c.cards_for > 0.5 and c.cards_against > 0.5),
    MarketRule("EACH_TEAM_OVER_1_5_CARDS", "cards", 680, lambda c: min(c.cards_for, c.cards_against), lambda c: c.cards_for > 1.5 and c.cards_against > 1.5),
    MarketRule("EACH_TEAM_OVER_2_5_CARDS", "cards", 690, lambda c: min(c.cards_for, c.cards_against), lambda c: c.cards_for > 2.5 and c.cards_against > 2.5),
    MarketRule("TEAM_OVER_10_5_SHOTS_FOR", "shots", 700, lambda c: c.total_shots_for, _over(lambda c: c.total_shots_for, 10.5)),
    MarketRule("TEAM_OVER_3_5_SHOTS_ON_TARGET_FOR", "shots", 710, lambda c: c.shots_on_target_for, _over(lambda c: c.shots_on_target_for, 3.5)),
    MarketRule("MATCH_OVER_20_5_FOULS", "fouls", 800, lambda c: c.total_fouls, _over(lambda c: c.total_fouls, 20.5)),
    MarketRule("TEAM_OVER_10_5_FOULS_COMMITTED", "fouls", 810, lambda c: c.fouls_committed, _over(lambda c: c.fouls_committed, 10.5)),
    MarketRule("TEAM_OVER_1_5_OFFSIDES_FOR", "offsides", 900, lambda c: c.offsides_for, _over(lambda c: c.offsides_for, 1.5)),
    MarketRule("MATCH_OVER_2_5_OFFSIDES", "offsides", 910, lambda c: c.total_offsides, _over(lambda c: c.total_offsides, 2.5)),
)


def _context_from_fact(row: dict) -> TeamMatchContext:
    return TeamMatchContext(
        fixture_id=int(row["fixture_id"]),
        team_id=int(row["team_id"]),
        opponent_team_id=int(row["opponent_team_id"]),
        league_id=int(row["league_id"]),
        season=int(row["season"]),
        played_at=str(row["played_at"]),
        scope=str(row.get("venue_scope") or "overall"),
        result=row.get("result"),
        goals_for=_num(row.get("goals_for")),
        goals_against=_num(row.get("goals_against")),
        total_match_goals=_num(row.get("total_match_goals")),
        goals_for_1h=_num(row.get("goals_for_1h")),
        goals_against_1h=_num(row.get("goals_against_1h")),
        total_1h_goals=_num(row.get("total_1h_goals")),
        goals_for_2h=_num(row.get("goals_for_2h")),
        goals_against_2h=_num(row.get("goals_against_2h")),
        total_2h_goals=_num(row.get("total_2h_goals")),
        corners_for=_num(row.get("corners_for")),
        corners_against=_num(row.get("corners_against")),
        total_corners=_num(row.get("total_corners")),
        cards_for=_num(row.get("cards_for")),
        cards_against=_num(row.get("cards_against")),
        total_cards=_num(row.get("total_cards")),
        booking_points_for=_num(row.get("booking_points_for")),
        booking_points_against=_num(row.get("booking_points_against")),
        total_booking_points=_num(row.get("total_booking_points")),
        fouls_committed=_num(row.get("fouls_committed")),
        fouls_won=_num(row.get("fouls_won")),
        total_fouls=_num(row.get("total_fouls")),
        offsides_for=_num(row.get("offsides_for")),
        offsides_against=_num(row.get("offsides_against")),
        total_offsides=_num(row.get("total_offsides")),
        total_shots_for=_num(row.get("total_shots_for")),
        total_shots_against=_num(row.get("total_shots_against")),
        shots_on_target_for=_num(row.get("shots_on_target_for")),
        shots_on_target_against=_num(row.get("shots_on_target_against")),
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
    for row in reversed(rows):
        if row["result"]:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def rebuild_team_trend_rollups(repository: StufRepository, team_id: int, league_id: int, season: int) -> None:
    fact_rows = repository.get_team_fixture_fact_rows(team_id, league_id, season)
    contexts = [_context_from_fact(row) for row in fact_rows]
    contexts.sort(key=lambda item: item.played_at, reverse=True)

    market_results: list[dict] = []
    grouped: dict[tuple[str, str], list[dict]] = {}

    for context in contexts:
        for scope_name in ("overall", context.scope):
            for rule in MARKET_RULES:
                row = {
                    "fixture_id": context.fixture_id,
                    "team_id": context.team_id,
                    "opponent_team_id": context.opponent_team_id,
                    "league_id": context.league_id,
                    "season": context.season,
                    "played_at": context.played_at,
                    "scope": scope_name,
                    "market_key": rule.key,
                    "result": bool(rule.predicate(context)),
                    "numeric_value": rule.value_getter(context),
                    "created_at": utcnow().isoformat(),
                }
                market_results.append(row)
                grouped.setdefault((scope_name, rule.key), []).append(row)

    season_stats: list[dict] = []
    for rule in MARKET_RULES:
        for scope_name in ("overall", "home", "away"):
            group = grouped.get((scope_name, rule.key), [])
            if not group:
                continue

            sample = len(group)
            hits = sum(1 for item in group if item["result"])
            last_5 = group[:5]
            last_10 = group[:10]
            last_5_hits = sum(1 for item in last_5 if item["result"])
            last_10_hits = sum(1 for item in last_10 if item["result"])

            season_stats.append(
                {
                    "team_id": team_id,
                    "league_id": league_id,
                    "season": season,
                    "scope": scope_name,
                    "market_key": rule.key,
                    "category": rule.category,
                    "sample": sample,
                    "hits": hits,
                    "percentage": _percentage(hits, sample),
                    "current_streak": _current_streak(group),
                    "longest_streak": _longest_streak(group),
                    "last_5_sample": len(last_5),
                    "last_5_hits": last_5_hits,
                    "last_5_percentage": _percentage(last_5_hits, len(last_5)),
                    "last_10_sample": len(last_10),
                    "last_10_hits": last_10_hits,
                    "last_10_percentage": _percentage(last_10_hits, len(last_10)),
                    "updated_at": utcnow().isoformat(),
                }
            )

    repository.replace_team_market_results(team_id, league_id, season, market_results)
    repository.replace_team_season_market_stats(team_id, league_id, season, season_stats)


def refresh_trends_for_fixture(repository: StufRepository, fixture_id: int) -> None:
    fixture_row = repository.get_fixture_context_row(fixture_id)
    if not fixture_row or not is_final_status(fixture_row.get("status_short")):
        return

    league_id = fixture_row.get("league_id")
    season = fixture_row.get("season")
    home_team_id = fixture_row.get("home_team_id")
    away_team_id = fixture_row.get("away_team_id")
    if not league_id or not season or not home_team_id or not away_team_id:
        return

    for team_id in (home_team_id, away_team_id):
        rebuild_team_trend_rollups(repository, team_id, league_id, season)
