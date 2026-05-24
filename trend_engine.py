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
    goals_for_1h: float | None
    goals_against_1h: float | None
    total_1h_goals: float | None
    goals_for_2h: float | None
    goals_against_2h: float | None
    total_2h_goals: float | None
    corners_for: float
    corners_against: float
    total_corners: float
    corners_for_1h: float | None
    corners_against_1h: float | None
    total_corners_1h: float | None
    corners_for_2h: float | None
    corners_against_2h: float | None
    total_corners_2h: float | None
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
    total_shots_for: float | None
    total_shots_against: float | None
    shots_on_target_for: float | None
    shots_on_target_against: float | None


def _always_sample(_: TeamMatchContext) -> bool:
    return True


@dataclass(frozen=True)
class MarketRule:
    key: str
    category: str
    sort_order: int
    value_getter: Callable[[TeamMatchContext], float]
    predicate: Callable[[TeamMatchContext], bool]
    sample_predicate: Callable[[TeamMatchContext], bool] = _always_sample


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


def _optional_num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _value(value: float | None) -> float:
    return 0.0 if value is None else value


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


def _line_key(line: float) -> str:
    return str(line).replace(".", "_")


def _has_1h_goals(context: TeamMatchContext) -> bool:
    return (
        context.goals_for_1h is not None
        and context.goals_against_1h is not None
        and context.total_1h_goals is not None
    )


def _has_2h_goals(context: TeamMatchContext) -> bool:
    return (
        context.goals_for_2h is not None
        and context.goals_against_2h is not None
        and context.total_2h_goals is not None
    )


def _has_both_half_goals(context: TeamMatchContext) -> bool:
    return _has_1h_goals(context) and _has_2h_goals(context)


def _total_1h_goals(context: TeamMatchContext) -> float:
    return _value(context.total_1h_goals)


def _total_2h_goals(context: TeamMatchContext) -> float:
    return _value(context.total_2h_goals)


def _team_goals_for_1h(context: TeamMatchContext) -> float:
    return _value(context.goals_for_1h)


def _team_goals_for_2h(context: TeamMatchContext) -> float:
    return _value(context.goals_for_2h)


def _team_goals_against_1h(context: TeamMatchContext) -> float:
    return _value(context.goals_against_1h)


def _team_goals_against_2h(context: TeamMatchContext) -> float:
    return _value(context.goals_against_2h)


def _match_goal_in_both_halves(context: TeamMatchContext) -> bool:
    return _total_1h_goals(context) > 0 and _total_2h_goals(context) > 0


def _team_scored_both_halves(context: TeamMatchContext) -> bool:
    return _team_goals_for_1h(context) > 0 and _team_goals_for_2h(context) > 0


def _team_conceded_both_halves(context: TeamMatchContext) -> bool:
    return _team_goals_against_1h(context) > 0 and _team_goals_against_2h(context) > 0


def _goal_market_rules() -> tuple[MarketRule, ...]:
    rules: list[MarketRule] = []
    order = 200

    for line in (1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"MATCH_OVER_{key_line}_GOALS", "goals", order, lambda c: c.total_match_goals, _over(lambda c: c.total_match_goals, line)))
        order += 1
        rules.append(MarketRule(f"MATCH_UNDER_{key_line}_GOALS", "goals", order, lambda c: c.total_match_goals, _under(lambda c: c.total_match_goals, line)))
        order += 1

    rules.extend((
        MarketRule("MATCH_GOAL_RANGE_0_1", "goals", order, lambda c: c.total_match_goals, lambda c: c.total_match_goals <= 1),
        MarketRule("MATCH_GOAL_RANGE_2_3", "goals", order + 1, lambda c: c.total_match_goals, lambda c: 2 <= c.total_match_goals <= 3),
        MarketRule("MATCH_GOAL_RANGE_4_PLUS", "goals", order + 2, lambda c: c.total_match_goals, lambda c: c.total_match_goals >= 4),
        MarketRule(
            "MATCH_GOAL_IN_BOTH_HALVES",
            "goals",
            order + 3,
            lambda c: min(_total_1h_goals(c), _total_2h_goals(c)),
            _match_goal_in_both_halves,
            _has_both_half_goals,
        ),
    ))
    order += 4

    for line in (0.5, 1.5, 2.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_GOALS_FOR", "goals", order, lambda c: c.goals_for, _over(lambda c: c.goals_for, line)))
        order += 1
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_GOALS_AGAINST", "goals", order, lambda c: c.goals_against, _over(lambda c: c.goals_against, line)))
        order += 1

    rules.extend((
        MarketRule(
            "TEAM_SCORED_BOTH_HALVES",
            "goals",
            order,
            lambda c: min(_team_goals_for_1h(c), _team_goals_for_2h(c)),
            _team_scored_both_halves,
            _has_both_half_goals,
        ),
        MarketRule(
            "TEAM_CONCEDED_BOTH_HALVES",
            "goals",
            order + 1,
            lambda c: min(_team_goals_against_1h(c), _team_goals_against_2h(c)),
            _team_conceded_both_halves,
            _has_both_half_goals,
        ),
    ))
    order += 2

    for line in (0.5, 1.5):
        key_line = _line_key(line)
        rules.extend((
            MarketRule(f"TEAM_1H_OVER_{key_line}_GOALS_FOR", "goals", order, _team_goals_for_1h, _over(_team_goals_for_1h, line), _has_1h_goals),
            MarketRule(f"TEAM_1H_OVER_{key_line}_GOALS_AGAINST", "goals", order + 1, _team_goals_against_1h, _over(_team_goals_against_1h, line), _has_1h_goals),
            MarketRule(f"TEAM_2H_OVER_{key_line}_GOALS_FOR", "goals", order + 2, _team_goals_for_2h, _over(_team_goals_for_2h, line), _has_2h_goals),
            MarketRule(f"TEAM_2H_OVER_{key_line}_GOALS_AGAINST", "goals", order + 3, _team_goals_against_2h, _over(_team_goals_against_2h, line), _has_2h_goals),
            MarketRule(f"MATCH_1H_OVER_{key_line}_GOALS", "goals", order + 4, _total_1h_goals, _over(_total_1h_goals, line), _has_1h_goals),
            MarketRule(f"MATCH_2H_OVER_{key_line}_GOALS", "goals", order + 5, _total_2h_goals, _over(_total_2h_goals, line), _has_2h_goals),
        ))
        order += 6

    return tuple(rules)


def _has_total_shots(context: TeamMatchContext) -> bool:
    return context.total_shots_for is not None and context.total_shots_against is not None


def _has_team_shots_for(context: TeamMatchContext) -> bool:
    return context.total_shots_for is not None


def _has_team_shots_against(context: TeamMatchContext) -> bool:
    return context.total_shots_against is not None


def _has_total_shots_on_target(context: TeamMatchContext) -> bool:
    return context.shots_on_target_for is not None and context.shots_on_target_against is not None


def _has_team_shots_on_target_for(context: TeamMatchContext) -> bool:
    return context.shots_on_target_for is not None


def _has_team_shots_on_target_against(context: TeamMatchContext) -> bool:
    return context.shots_on_target_against is not None


def _total_shots(context: TeamMatchContext) -> float:
    return _value(context.total_shots_for) + _value(context.total_shots_against)


def _total_shots_on_target(context: TeamMatchContext) -> float:
    return _value(context.shots_on_target_for) + _value(context.shots_on_target_against)


def _each_team_shots_on_target(context: TeamMatchContext) -> float:
    return min(_value(context.shots_on_target_for), _value(context.shots_on_target_against))


def _shot_market_rules() -> tuple[MarketRule, ...]:
    rules: list[MarketRule] = []
    order = 700

    for line in (19.5, 21.5, 23.5, 25.5, 27.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"MATCH_OVER_{key_line}_SHOTS", "shots", order, _total_shots, _over(_total_shots, line), _has_total_shots))
        order += 1
        rules.append(MarketRule(f"MATCH_UNDER_{key_line}_SHOTS", "shots", order, _total_shots, _under(_total_shots, line), _has_total_shots))
        order += 1

    for line in (7.5, 9.5, 11.5, 13.5, 15.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_SHOTS_FOR", "shots", order, lambda c, _line=line: _value(c.total_shots_for), _over(lambda c: _value(c.total_shots_for), line), _has_team_shots_for))
        order += 1
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_SHOTS_AGAINST", "shots", order, lambda c, _line=line: _value(c.total_shots_against), _over(lambda c: _value(c.total_shots_against), line), _has_team_shots_against))
        order += 1

    for line in (5.5, 6.5, 7.5, 8.5, 9.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"MATCH_OVER_{key_line}_SHOTS_ON_TARGET", "shots", order, _total_shots_on_target, _over(_total_shots_on_target, line), _has_total_shots_on_target))
        order += 1

    for line in (2.5, 3.5, 4.5, 5.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_SHOTS_ON_TARGET_FOR", "shots", order, lambda c, _line=line: _value(c.shots_on_target_for), _over(lambda c: _value(c.shots_on_target_for), line), _has_team_shots_on_target_for))
        order += 1
        rules.append(MarketRule(f"TEAM_OVER_{key_line}_SHOTS_ON_TARGET_AGAINST", "shots", order, lambda c, _line=line: _value(c.shots_on_target_against), _over(lambda c: _value(c.shots_on_target_against), line), _has_team_shots_on_target_against))
        order += 1

    for line in (1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rules.append(MarketRule(f"EACH_TEAM_OVER_{key_line}_SHOTS_ON_TARGET", "shots", order, _each_team_shots_on_target, lambda c, _line=line: _each_team_shots_on_target(c) > _line, _has_total_shots_on_target))
        order += 1

    return tuple(rules)


MARKET_RULES: tuple[MarketRule, ...] = (
    MarketRule("WIN", "result", 10, lambda c: c.goals_for - c.goals_against, _is_win),
    MarketRule("DRAW", "result", 20, lambda c: c.goals_for - c.goals_against, _is_draw),
    MarketRule("LOSS", "result", 30, lambda c: c.goals_for - c.goals_against, _is_loss),
    MarketRule("UNBEATEN", "result", 40, lambda c: c.goals_for - c.goals_against, lambda c: not _is_loss(c)),
    MarketRule("WINLESS", "result", 50, lambda c: c.goals_for - c.goals_against, lambda c: not _is_win(c)),
    MarketRule("BTTS_YES", "btts", 100, lambda c: min(c.goals_for, c.goals_against), lambda c: c.goals_for > 0 and c.goals_against > 0),
    *_goal_market_rules(),
    MarketRule("MATCH_OVER_7_5_CORNERS", "corners", 395, lambda c: c.total_corners, _over(lambda c: c.total_corners, 7.5)),
    MarketRule("MATCH_OVER_8_5_CORNERS", "corners", 400, lambda c: c.total_corners, _over(lambda c: c.total_corners, 8.5)),
    MarketRule("MATCH_OVER_9_5_CORNERS", "corners", 410, lambda c: c.total_corners, _over(lambda c: c.total_corners, 9.5)),
    MarketRule("MATCH_OVER_10_5_CORNERS", "corners", 420, lambda c: c.total_corners, _over(lambda c: c.total_corners, 10.5)),
    MarketRule("MATCH_OVER_11_5_CORNERS", "corners", 425, lambda c: c.total_corners, _over(lambda c: c.total_corners, 11.5)),
    MarketRule("MATCH_OVER_12_5_CORNERS", "corners", 426, lambda c: c.total_corners, _over(lambda c: c.total_corners, 12.5)),
    MarketRule("MATCH_UNDER_7_5_CORNERS", "corners", 427, lambda c: c.total_corners, _under(lambda c: c.total_corners, 7.5)),
    MarketRule("MATCH_UNDER_8_5_CORNERS", "corners", 428, lambda c: c.total_corners, _under(lambda c: c.total_corners, 8.5)),
    MarketRule("MATCH_UNDER_9_5_CORNERS", "corners", 429, lambda c: c.total_corners, _under(lambda c: c.total_corners, 9.5)),
    MarketRule("MATCH_UNDER_10_5_CORNERS", "corners", 430, lambda c: c.total_corners, _under(lambda c: c.total_corners, 10.5)),
    MarketRule("MATCH_UNDER_11_5_CORNERS", "corners", 431, lambda c: c.total_corners, _under(lambda c: c.total_corners, 11.5)),
    MarketRule("MATCH_UNDER_12_5_CORNERS", "corners", 432, lambda c: c.total_corners, _under(lambda c: c.total_corners, 12.5)),
    MarketRule("TEAM_OVER_2_5_CORNERS_FOR", "corners", 430, lambda c: c.corners_for, _over(lambda c: c.corners_for, 2.5)),
    MarketRule("TEAM_OVER_3_5_CORNERS_FOR", "corners", 440, lambda c: c.corners_for, _over(lambda c: c.corners_for, 3.5)),
    MarketRule("TEAM_OVER_4_5_CORNERS_FOR", "corners", 450, lambda c: c.corners_for, _over(lambda c: c.corners_for, 4.5)),
    MarketRule("TEAM_OVER_5_5_CORNERS_FOR", "corners", 460, lambda c: c.corners_for, _over(lambda c: c.corners_for, 5.5)),
    MarketRule("TEAM_OVER_6_5_CORNERS_FOR", "corners", 465, lambda c: c.corners_for, _over(lambda c: c.corners_for, 6.5)),
    MarketRule("TEAM_OVER_2_5_CORNERS_AGAINST", "corners", 470, lambda c: c.corners_against, _over(lambda c: c.corners_against, 2.5)),
    MarketRule("TEAM_OVER_3_5_CORNERS_AGAINST", "corners", 480, lambda c: c.corners_against, _over(lambda c: c.corners_against, 3.5)),
    MarketRule("TEAM_OVER_4_5_CORNERS_AGAINST", "corners", 483, lambda c: c.corners_against, _over(lambda c: c.corners_against, 4.5)),
    MarketRule("TEAM_OVER_5_5_CORNERS_AGAINST", "corners", 484, lambda c: c.corners_against, _over(lambda c: c.corners_against, 5.5)),
    MarketRule("TEAM_OVER_6_5_CORNERS_AGAINST", "corners", 485, lambda c: c.corners_against, _over(lambda c: c.corners_against, 6.5)),
    MarketRule("EACH_TEAM_OVER_1_5_CORNERS", "corners", 486, lambda c: min(c.corners_for, c.corners_against), lambda c: c.corners_for > 1.5 and c.corners_against > 1.5),
    MarketRule("EACH_TEAM_OVER_2_5_CORNERS", "corners", 487, lambda c: min(c.corners_for, c.corners_against), lambda c: c.corners_for > 2.5 and c.corners_against > 2.5),
    MarketRule("EACH_TEAM_OVER_3_5_CORNERS", "corners", 488, lambda c: min(c.corners_for, c.corners_against), lambda c: c.corners_for > 3.5 and c.corners_against > 3.5),
    MarketRule("EACH_TEAM_OVER_4_5_CORNERS", "corners", 489, lambda c: min(c.corners_for, c.corners_against), lambda c: c.corners_for > 4.5 and c.corners_against > 4.5),
    MarketRule("MOST_CORNERS", "corners", 490, lambda c: c.corners_for - c.corners_against, lambda c: c.corners_for > c.corners_against),
    MarketRule("MATCH_1H_OVER_3_5_CORNERS", "corners", 491, lambda c: _value(c.total_corners_1h), _over(lambda c: _value(c.total_corners_1h), 3.5), lambda c: c.total_corners_1h is not None),
    MarketRule("MATCH_1H_OVER_4_5_CORNERS", "corners", 492, lambda c: _value(c.total_corners_1h), _over(lambda c: _value(c.total_corners_1h), 4.5), lambda c: c.total_corners_1h is not None),
    MarketRule("MATCH_1H_OVER_5_5_CORNERS", "corners", 493, lambda c: _value(c.total_corners_1h), _over(lambda c: _value(c.total_corners_1h), 5.5), lambda c: c.total_corners_1h is not None),
    MarketRule("MATCH_1H_OVER_6_5_CORNERS", "corners", 494, lambda c: _value(c.total_corners_1h), _over(lambda c: _value(c.total_corners_1h), 6.5), lambda c: c.total_corners_1h is not None),
    MarketRule("TEAM_1H_OVER_1_5_CORNERS_FOR", "corners", 495, lambda c: _value(c.corners_for_1h), _over(lambda c: _value(c.corners_for_1h), 1.5), lambda c: c.corners_for_1h is not None),
    MarketRule("TEAM_1H_OVER_2_5_CORNERS_FOR", "corners", 496, lambda c: _value(c.corners_for_1h), _over(lambda c: _value(c.corners_for_1h), 2.5), lambda c: c.corners_for_1h is not None),
    MarketRule("TEAM_1H_OVER_3_5_CORNERS_FOR", "corners", 497, lambda c: _value(c.corners_for_1h), _over(lambda c: _value(c.corners_for_1h), 3.5), lambda c: c.corners_for_1h is not None),
    MarketRule("TEAM_1H_OVER_1_5_CORNERS_AGAINST", "corners", 498, lambda c: _value(c.corners_against_1h), _over(lambda c: _value(c.corners_against_1h), 1.5), lambda c: c.corners_against_1h is not None),
    MarketRule("TEAM_1H_OVER_2_5_CORNERS_AGAINST", "corners", 499, lambda c: _value(c.corners_against_1h), _over(lambda c: _value(c.corners_against_1h), 2.5), lambda c: c.corners_against_1h is not None),
    MarketRule("TEAM_1H_OVER_3_5_CORNERS_AGAINST", "corners", 500, lambda c: _value(c.corners_against_1h), _over(lambda c: _value(c.corners_against_1h), 3.5), lambda c: c.corners_against_1h is not None),
    MarketRule("MATCH_2H_OVER_3_5_CORNERS", "corners", 501, lambda c: _value(c.total_corners_2h), _over(lambda c: _value(c.total_corners_2h), 3.5), lambda c: c.total_corners_2h is not None),
    MarketRule("MATCH_2H_OVER_4_5_CORNERS", "corners", 502, lambda c: _value(c.total_corners_2h), _over(lambda c: _value(c.total_corners_2h), 4.5), lambda c: c.total_corners_2h is not None),
    MarketRule("MATCH_2H_OVER_5_5_CORNERS", "corners", 503, lambda c: _value(c.total_corners_2h), _over(lambda c: _value(c.total_corners_2h), 5.5), lambda c: c.total_corners_2h is not None),
    MarketRule("MATCH_2H_OVER_6_5_CORNERS", "corners", 504, lambda c: _value(c.total_corners_2h), _over(lambda c: _value(c.total_corners_2h), 6.5), lambda c: c.total_corners_2h is not None),
    MarketRule("MATCH_EACH_HALF_OVER_3_5_CORNERS", "corners", 505, lambda c: min(_value(c.total_corners_1h), _value(c.total_corners_2h)), lambda c: _value(c.total_corners_1h) > 3.5 and _value(c.total_corners_2h) > 3.5, lambda c: c.total_corners_1h is not None and c.total_corners_2h is not None),
    MarketRule("MATCH_EACH_HALF_OVER_4_5_CORNERS", "corners", 506, lambda c: min(_value(c.total_corners_1h), _value(c.total_corners_2h)), lambda c: _value(c.total_corners_1h) > 4.5 and _value(c.total_corners_2h) > 4.5, lambda c: c.total_corners_1h is not None and c.total_corners_2h is not None),
    MarketRule("MATCH_EACH_HALF_OVER_5_5_CORNERS", "corners", 507, lambda c: min(_value(c.total_corners_1h), _value(c.total_corners_2h)), lambda c: _value(c.total_corners_1h) > 5.5 and _value(c.total_corners_2h) > 5.5, lambda c: c.total_corners_1h is not None and c.total_corners_2h is not None),
    MarketRule("MATCH_EACH_HALF_OVER_6_5_CORNERS", "corners", 508, lambda c: min(_value(c.total_corners_1h), _value(c.total_corners_2h)), lambda c: _value(c.total_corners_1h) > 6.5 and _value(c.total_corners_2h) > 6.5, lambda c: c.total_corners_1h is not None and c.total_corners_2h is not None),
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
    *_shot_market_rules(),
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
        goals_for_1h=_optional_num(row.get("goals_for_1h")),
        goals_against_1h=_optional_num(row.get("goals_against_1h")),
        total_1h_goals=_optional_num(row.get("total_1h_goals")),
        goals_for_2h=_optional_num(row.get("goals_for_2h")),
        goals_against_2h=_optional_num(row.get("goals_against_2h")),
        total_2h_goals=_optional_num(row.get("total_2h_goals")),
        corners_for=_num(row.get("corners_for")),
        corners_against=_num(row.get("corners_against")),
        total_corners=_num(row.get("total_corners")),
        corners_for_1h=_optional_num(row.get("corners_for_1h")),
        corners_against_1h=_optional_num(row.get("corners_against_1h")),
        total_corners_1h=_optional_num(row.get("total_corners_1h")),
        corners_for_2h=_optional_num(row.get("corners_for_2h")),
        corners_against_2h=_optional_num(row.get("corners_against_2h")),
        total_corners_2h=_optional_num(row.get("total_corners_2h")),
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
        total_shots_for=_optional_num(row.get("total_shots_for")),
        total_shots_against=_optional_num(row.get("total_shots_against")),
        shots_on_target_for=_optional_num(row.get("shots_on_target_for")),
        shots_on_target_against=_optional_num(row.get("shots_on_target_against")),
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
                if not rule.sample_predicate(context):
                    continue

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
