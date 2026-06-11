from __future__ import annotations

from typing import Any


def _line_key(line: float) -> str:
    return str(line).replace(".", "_")


def _market(
    key: str,
    category: str,
    label: str,
    subject: str,
    metric: str,
    operator: str,
    line: float | None,
    display_order: int,
    period: str = "FT",
    family: str | None = None,
) -> dict[str, Any]:
    row = {
        "key": key,
        "category": category,
        "label": label,
        "subject": subject,
        "metric": metric,
        "operator": operator,
        "line": line,
        "period": period,
        "display_order": display_order,
    }
    if family is not None:
        row["family"] = family
    return row


def _goals_market_definitions() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    order = 50

    for line in (0.5, 1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_OVER_{key_line}_GOALS", "goals", f"Over {line} Total Goals", "match", "goals", "over", line, order, family="match_totals"))
        order += 1
    for line in (1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_UNDER_{key_line}_GOALS", "goals", f"Under {line} Total Goals", "match", "goals", "under", line, order, family="match_totals"))
        order += 1

    for key, label, line in (
        ("MATCH_GOAL_RANGE_0_1", "0-1 Goals", 1.0),
        ("MATCH_GOAL_RANGE_2_3", "2-3 Goals", 3.0),
        ("MATCH_GOAL_RANGE_4_PLUS", "4+ Goals", 4.0),
    ):
        rows.append(_market(key, "goals", label, "match", "goals", "custom", line, order, family="match_totals"))
        order += 1

    rows.append(_market("MATCH_GOAL_IN_BOTH_HALVES", "goals", "Goal In Both Halves", "match", "goals", "custom", None, order, family="match_totals"))
    order = 80

    for line in (0.5, 1.5, 2.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_OVER_{key_line}_GOALS_FOR", "goals", f"Over {line} Team Goals For", "team", "goals_for", "over", line, order, family="team_goals"))
        order += 1
        rows.append(_market(f"TEAM_OVER_{key_line}_GOALS_AGAINST", "goals", f"Over {line} Team Goals Against", "opponent", "goals_against", "over", line, order, family="team_goals"))
        order += 1
    for line in (0.5, 1.5, 2.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_UNDER_{key_line}_GOALS_FOR", "goals", f"Under {line} Team Goals For", "team", "goals_for", "under", line, order, family="team_goals"))
        order += 1
        rows.append(_market(f"TEAM_UNDER_{key_line}_GOALS_AGAINST", "goals", f"Under {line} Team Goals Against", "opponent", "goals_against", "under", line, order, family="team_goals"))
        order += 1

    rows.append(_market("TEAM_SCORED_BOTH_HALVES", "goals", "Team Scored In Both Halves", "team", "goals_for", "custom", None, order, family="team_goals"))
    order += 1
    rows.append(_market("TEAM_CONCEDED_BOTH_HALVES", "goals", "Team Conceded In Both Halves", "opponent", "goals_against", "custom", None, order, family="team_goals"))
    order = 120

    for period in ("1H", "2H"):
        period_label = "First Half" if period == "1H" else "Second Half"
        period_key = "1H" if period == "1H" else "2H"
        for line in (0.5, 1.5):
            key_line = _line_key(line)
            rows.append(_market(f"MATCH_{period_key}_OVER_{key_line}_GOALS", "goals", f"Over {line} {period_label} Match Goals", "match", "goals", "over", line, order, period=period, family="goals_by_half"))
            order += 1
            rows.append(_market(f"MATCH_{period_key}_UNDER_{key_line}_GOALS", "goals", f"Under {line} {period_label} Match Goals", "match", "goals", "under", line, order, period=period, family="goals_by_half"))
            order += 1
            rows.append(_market(f"TEAM_{period_key}_OVER_{key_line}_GOALS_FOR", "goals", f"Over {line} {period_label} Team Goals For", "team", "goals_for", "over", line, order, period=period, family="goals_by_half"))
            order += 1
            rows.append(_market(f"TEAM_{period_key}_UNDER_{key_line}_GOALS_FOR", "goals", f"Under {line} {period_label} Team Goals For", "team", "goals_for", "under", line, order, period=period, family="goals_by_half"))
            order += 1
            rows.append(_market(f"TEAM_{period_key}_OVER_{key_line}_GOALS_AGAINST", "goals", f"Over {line} {period_label} Team Goals Against", "opponent", "goals_against", "over", line, order, period=period, family="goals_by_half"))
            order += 1
            rows.append(_market(f"TEAM_{period_key}_UNDER_{key_line}_GOALS_AGAINST", "goals", f"Under {line} {period_label} Team Goals Against", "opponent", "goals_against", "under", line, order, period=period, family="goals_by_half"))
            order += 1

    return tuple(rows)


def _shots_market_definitions() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    order = 700

    for line in (19.5, 21.5, 23.5, 25.5, 27.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_OVER_{key_line}_SHOTS", "shots", f"Over {line} Total Match Shots", "match", "shots", "over", line, order))
        order += 1
        rows.append(_market(f"MATCH_UNDER_{key_line}_SHOTS", "shots", f"Under {line} Total Match Shots", "match", "shots", "under", line, order))
        order += 1

    for line in (7.5, 9.5, 11.5, 13.5, 15.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_OVER_{key_line}_SHOTS_FOR", "shots", f"Over {line} Team Shots For", "team", "shots_for", "over", line, order))
        order += 1
        rows.append(_market(f"TEAM_OVER_{key_line}_SHOTS_AGAINST", "shots", f"Over {line} Team Shots Against", "opponent", "shots_against", "over", line, order))
        order += 1

    for line in (5.5, 6.5, 7.5, 8.5, 9.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_OVER_{key_line}_SHOTS_ON_TARGET", "shots", f"Over {line} Match Shots On Target", "match", "shots_on_target", "over", line, order))
        order += 1

    for line in (2.5, 3.5, 4.5, 5.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_OVER_{key_line}_SHOTS_ON_TARGET_FOR", "shots", f"Over {line} Team Shots On Target For", "team", "shots_on_target", "over", line, order))
        order += 1
        rows.append(_market(f"TEAM_OVER_{key_line}_SHOTS_ON_TARGET_AGAINST", "shots", f"Over {line} Team Shots On Target Against", "opponent", "shots_on_target", "over", line, order))
        order += 1

    for line in (1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rows.append(_market(f"EACH_TEAM_OVER_{key_line}_SHOTS_ON_TARGET", "shots", f"Each Team Over {line} Shots On Target", "match", "shots_on_target", "over", line, order))
        order += 1

    return tuple(rows)


def _offsides_market_definitions() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    order = 900

    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_OVER_{key_line}_OFFSIDES", "offsides", f"Over {line} Total Match Offsides", "match", "offsides", "over", line, order, family="match_offsides"))
        order += 1
        rows.append(_market(f"MATCH_UNDER_{key_line}_OFFSIDES", "offsides", f"Under {line} Total Match Offsides", "match", "offsides", "under", line, order, family="match_offsides"))
        order += 1

    for line in (0.5, 1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_OVER_{key_line}_OFFSIDES_FOR", "offsides", f"Over {line} Team Offsides For", "team", "offsides_for", "over", line, order, family="team_offsides"))
        order += 1
        rows.append(_market(f"TEAM_OVER_{key_line}_OFFSIDES_AGAINST", "offsides", f"Over {line} Team Offsides Against", "opponent", "offsides_against", "over", line, order, family="team_offsides"))
        order += 1

    return tuple(rows)


def _cards_market_definitions() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    order = 590

    rows.append(_market("MATCH_OVER_0_5_CARDS", "cards", "Over 0.5 Total Match Cards", "match", "cards", "over", 0.5, order, family="match_cards"))
    order += 1
    for line in (1.5, 2.5, 3.5, 4.5, 5.5, 6.5):
        key_line = _line_key(line)
        rows.append(_market(f"MATCH_OVER_{key_line}_CARDS", "cards", f"Over {line} Total Match Cards", "match", "cards", "over", line, order, family="match_cards"))
        order += 1
        rows.append(_market(f"MATCH_UNDER_{key_line}_CARDS", "cards", f"Under {line} Total Match Cards", "match", "cards", "under", line, order, family="match_cards"))
        order += 1

    for line in (0.5, 1.5, 2.5, 3.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_OVER_{key_line}_CARDS_FOR", "cards", f"Over {line} Team Cards For", "team", "cards_for", "over", line, order, family="team_cards"))
        order += 1
        rows.append(_market(f"TEAM_OVER_{key_line}_CARDS_AGAINST", "cards", f"Over {line} Team Cards Against", "opponent", "cards_against", "over", line, order, family="team_cards"))
        order += 1
    for line in (0.5, 1.5, 2.5):
        key_line = _line_key(line)
        rows.append(_market(f"TEAM_UNDER_{key_line}_CARDS_FOR", "cards", f"Under {line} Team Cards For", "team", "cards_for", "under", line, order, family="team_cards"))
        order += 1
        rows.append(_market(f"TEAM_UNDER_{key_line}_CARDS_AGAINST", "cards", f"Under {line} Team Cards Against", "opponent", "cards_against", "under", line, order, family="team_cards"))
        order += 1

    for line in (0.5, 1.5, 2.5):
        key_line = _line_key(line)
        rows.append(_market(f"EACH_TEAM_OVER_{key_line}_CARDS", "cards", f"Each Team Over {line} Cards", "each_team", "cards", "over", line, order, family="each_team_cards"))
        order += 1

    return tuple(rows)


MARKET_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "WIN",
        "category": "result",
        "label": "Win",
        "subject": "team",
        "metric": "result",
        "operator": "win",
        "line": None,
        "period": "FT",
        "display_order": 10,
    },
    {
        "key": "DRAW",
        "category": "result",
        "label": "Draw",
        "subject": "match",
        "metric": "result",
        "operator": "draw",
        "line": None,
        "period": "FT",
        "display_order": 20,
    },
    {
        "key": "LOSS",
        "category": "result",
        "label": "Loss",
        "subject": "team",
        "metric": "result",
        "operator": "loss",
        "line": None,
        "period": "FT",
        "display_order": 30,
    },
    {
        "key": "UNBEATEN",
        "category": "result",
        "label": "Unbeaten",
        "subject": "team",
        "metric": "result",
        "operator": "custom",
        "line": None,
        "period": "FT",
        "display_order": 35,
    },
    {
        "key": "WINLESS",
        "category": "result",
        "label": "Winless",
        "subject": "team",
        "metric": "result",
        "operator": "custom",
        "line": None,
        "period": "FT",
        "display_order": 36,
    },
    {
        "key": "WIN_1H",
        "category": "half_result",
        "label": "Win 1st Half",
        "subject": "team",
        "metric": "result",
        "operator": "win",
        "line": None,
        "period": "1H",
        "display_order": 10,
    },
    {
        "key": "DRAW_1H",
        "category": "half_result",
        "label": "Draw 1st Half",
        "subject": "match",
        "metric": "result",
        "operator": "draw",
        "line": None,
        "period": "1H",
        "display_order": 20,
    },
    {
        "key": "LOSS_1H",
        "category": "half_result",
        "label": "Lose 1st Half",
        "subject": "team",
        "metric": "result",
        "operator": "loss",
        "line": None,
        "period": "1H",
        "display_order": 30,
    },
    {
        "key": "WIN_2H",
        "category": "half_result",
        "label": "Win 2nd Half",
        "subject": "team",
        "metric": "result",
        "operator": "win",
        "line": None,
        "period": "2H",
        "display_order": 40,
    },
    {
        "key": "DRAW_2H",
        "category": "half_result",
        "label": "Draw 2nd Half",
        "subject": "match",
        "metric": "result",
        "operator": "draw",
        "line": None,
        "period": "2H",
        "display_order": 50,
    },
    {
        "key": "LOSS_2H",
        "category": "half_result",
        "label": "Lose 2nd Half",
        "subject": "team",
        "metric": "result",
        "operator": "loss",
        "line": None,
        "period": "2H",
        "display_order": 60,
    },
    {
        "key": "BTTS_YES",
        "category": "btts",
        "label": "BTTS",
        "subject": "match",
        "metric": "goals",
        "operator": "btts",
        "line": None,
        "period": "FT",
        "display_order": 40,
    },
    {
        "key": "BTTS_NO",
        "category": "btts",
        "label": "BTTS No",
        "subject": "match",
        "metric": "goals",
        "operator": "custom",
        "line": None,
        "period": "FT",
        "display_order": 41,
    },
    {
        "key": "BTTS_1H",
        "category": "btts",
        "label": "BTTS 1st Half",
        "subject": "match",
        "metric": "goals",
        "operator": "btts",
        "line": None,
        "period": "1H",
        "display_order": 42,
    },
    {
        "key": "BTTS_2H",
        "category": "btts",
        "label": "BTTS 2nd Half",
        "subject": "match",
        "metric": "goals",
        "operator": "btts",
        "line": None,
        "period": "2H",
        "display_order": 43,
    },
    {
        "key": "BTTS_BOTH_HALVES",
        "category": "btts",
        "label": "BTTS Both Halves",
        "subject": "match",
        "metric": "goals",
        "operator": "custom",
        "line": None,
        "period": "FT",
        "display_order": 44,
    },
    *_goals_market_definitions(),
    {
        "key": "MATCH_OVER_7_5_CORNERS",
        "category": "corners",
        "label": "Over 7.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 7.5,
        "period": "FT",
        "display_order": 195,
    },
    {
        "key": "MATCH_OVER_8_5_CORNERS",
        "category": "corners",
        "label": "Over 8.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 8.5,
        "period": "FT",
        "display_order": 200,
    },
    {
        "key": "MATCH_OVER_9_5_CORNERS",
        "category": "corners",
        "label": "Over 9.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 9.5,
        "period": "FT",
        "display_order": 210,
    },
    {
        "key": "MATCH_OVER_10_5_CORNERS",
        "category": "corners",
        "label": "Over 10.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 10.5,
        "period": "FT",
        "display_order": 220,
    },
    {
        "key": "MATCH_OVER_11_5_CORNERS",
        "category": "corners",
        "label": "Over 11.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 11.5,
        "period": "FT",
        "display_order": 225,
    },
    {
        "key": "MATCH_OVER_12_5_CORNERS",
        "category": "corners",
        "label": "Over 12.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 12.5,
        "period": "FT",
        "display_order": 226,
    },
    {
        "key": "MATCH_UNDER_7_5_CORNERS",
        "category": "corners",
        "label": "Under 7.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 7.5,
        "period": "FT",
        "display_order": 227,
    },
    {
        "key": "MATCH_UNDER_8_5_CORNERS",
        "category": "corners",
        "label": "Under 8.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 8.5,
        "period": "FT",
        "display_order": 228,
    },
    {
        "key": "MATCH_UNDER_9_5_CORNERS",
        "category": "corners",
        "label": "Under 9.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 9.5,
        "period": "FT",
        "display_order": 229,
    },
    {
        "key": "MATCH_UNDER_10_5_CORNERS",
        "category": "corners",
        "label": "Under 10.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 10.5,
        "period": "FT",
        "display_order": 230,
    },
    {
        "key": "MATCH_UNDER_11_5_CORNERS",
        "category": "corners",
        "label": "Under 11.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 11.5,
        "period": "FT",
        "display_order": 231,
    },
    {
        "key": "MATCH_UNDER_12_5_CORNERS",
        "category": "corners",
        "label": "Under 12.5 Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "under",
        "line": 12.5,
        "period": "FT",
        "display_order": 232,
    },
    {
        "key": "TEAM_OVER_2_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 2.5 Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 2.5,
        "period": "FT",
        "display_order": 230,
    },
    {
        "key": "TEAM_OVER_3_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 3.5 Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 3.5,
        "period": "FT",
        "display_order": 240,
    },
    {
        "key": "TEAM_OVER_4_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 4.5 Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 4.5,
        "period": "FT",
        "display_order": 250,
    },
    {
        "key": "TEAM_OVER_5_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 5.5 Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 5.5,
        "period": "FT",
        "display_order": 255,
    },
    {
        "key": "TEAM_OVER_6_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 6.5 Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 6.5,
        "period": "FT",
        "display_order": 256,
    },
    {
        "key": "TEAM_OVER_2_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 2.5 Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 2.5,
        "period": "FT",
        "display_order": 260,
    },
    {
        "key": "TEAM_OVER_3_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 3.5 Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 3.5,
        "period": "FT",
        "display_order": 265,
    },
    {
        "key": "TEAM_OVER_4_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 4.5 Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 4.5,
        "period": "FT",
        "display_order": 266,
    },
    {
        "key": "TEAM_OVER_5_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 5.5 Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 5.5,
        "period": "FT",
        "display_order": 267,
    },
    {
        "key": "TEAM_OVER_6_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 6.5 Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 6.5,
        "period": "FT",
        "display_order": 268,
    },
    {
        "key": "EACH_TEAM_OVER_1_5_CORNERS",
        "category": "corners",
        "label": "Each Team Over 1.5 Corners",
        "subject": "each_team",
        "metric": "corners",
        "operator": "over",
        "line": 1.5,
        "period": "FT",
        "display_order": 269,
    },
    {
        "key": "EACH_TEAM_OVER_2_5_CORNERS",
        "category": "corners",
        "label": "Each Team Over 2.5 Corners",
        "subject": "each_team",
        "metric": "corners",
        "operator": "over",
        "line": 2.5,
        "period": "FT",
        "display_order": 270,
    },
    {
        "key": "EACH_TEAM_OVER_3_5_CORNERS",
        "category": "corners",
        "label": "Each Team Over 3.5 Corners",
        "subject": "each_team",
        "metric": "corners",
        "operator": "over",
        "line": 3.5,
        "period": "FT",
        "display_order": 271,
    },
    {
        "key": "EACH_TEAM_OVER_4_5_CORNERS",
        "category": "corners",
        "label": "Each Team Over 4.5 Corners",
        "subject": "each_team",
        "metric": "corners",
        "operator": "over",
        "line": 4.5,
        "period": "FT",
        "display_order": 272,
    },
    {
        "key": "MOST_CORNERS",
        "category": "corners",
        "label": "Most Corners",
        "subject": "team",
        "metric": "corners",
        "operator": "most",
        "line": None,
        "period": "FT",
        "display_order": 280,
    },
    {
        "key": "MATCH_1H_OVER_3_5_CORNERS",
        "category": "corners",
        "label": "Over 3.5 First Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 3.5,
        "period": "1H",
        "display_order": 281,
    },
    {
        "key": "MATCH_1H_OVER_4_5_CORNERS",
        "category": "corners",
        "label": "Over 4.5 First Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 4.5,
        "period": "1H",
        "display_order": 282,
    },
    {
        "key": "MATCH_1H_OVER_5_5_CORNERS",
        "category": "corners",
        "label": "Over 5.5 First Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 5.5,
        "period": "1H",
        "display_order": 283,
    },
    {
        "key": "MATCH_1H_OVER_6_5_CORNERS",
        "category": "corners",
        "label": "Over 6.5 First Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 6.5,
        "period": "1H",
        "display_order": 284,
    },
    {
        "key": "TEAM_1H_OVER_1_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 1.5 First Half Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 1.5,
        "period": "1H",
        "display_order": 285,
    },
    {
        "key": "TEAM_1H_OVER_2_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 2.5 First Half Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 2.5,
        "period": "1H",
        "display_order": 286,
    },
    {
        "key": "TEAM_1H_OVER_3_5_CORNERS_FOR",
        "category": "corners",
        "label": "Over 3.5 First Half Team Corners For",
        "subject": "team",
        "metric": "corners_for",
        "operator": "over",
        "line": 3.5,
        "period": "1H",
        "display_order": 287,
    },
    {
        "key": "TEAM_1H_OVER_1_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 1.5 First Half Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 1.5,
        "period": "1H",
        "display_order": 288,
    },
    {
        "key": "TEAM_1H_OVER_2_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 2.5 First Half Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 2.5,
        "period": "1H",
        "display_order": 289,
    },
    {
        "key": "TEAM_1H_OVER_3_5_CORNERS_AGAINST",
        "category": "corners",
        "label": "Over 3.5 First Half Team Corners Against",
        "subject": "opponent",
        "metric": "corners_against",
        "operator": "over",
        "line": 3.5,
        "period": "1H",
        "display_order": 290,
    },
    {
        "key": "MATCH_2H_OVER_3_5_CORNERS",
        "category": "corners",
        "label": "Over 3.5 Second Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 3.5,
        "period": "2H",
        "display_order": 291,
    },
    {
        "key": "MATCH_2H_OVER_4_5_CORNERS",
        "category": "corners",
        "label": "Over 4.5 Second Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 4.5,
        "period": "2H",
        "display_order": 292,
    },
    {
        "key": "MATCH_2H_OVER_5_5_CORNERS",
        "category": "corners",
        "label": "Over 5.5 Second Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 5.5,
        "period": "2H",
        "display_order": 293,
    },
    {
        "key": "MATCH_2H_OVER_6_5_CORNERS",
        "category": "corners",
        "label": "Over 6.5 Second Half Match Corners",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 6.5,
        "period": "2H",
        "display_order": 294,
    },
    {
        "key": "MATCH_EACH_HALF_OVER_3_5_CORNERS",
        "category": "corners",
        "label": "Over 3.5 Corners In Each Half",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 3.5,
        "period": "FT",
        "display_order": 295,
    },
    {
        "key": "MATCH_EACH_HALF_OVER_4_5_CORNERS",
        "category": "corners",
        "label": "Over 4.5 Corners In Each Half",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 4.5,
        "period": "FT",
        "display_order": 296,
    },
    {
        "key": "MATCH_EACH_HALF_OVER_5_5_CORNERS",
        "category": "corners",
        "label": "Over 5.5 Corners In Each Half",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 5.5,
        "period": "FT",
        "display_order": 297,
    },
    {
        "key": "MATCH_EACH_HALF_OVER_6_5_CORNERS",
        "category": "corners",
        "label": "Over 6.5 Corners In Each Half",
        "subject": "match",
        "metric": "corners",
        "operator": "over",
        "line": 6.5,
        "period": "FT",
        "display_order": 298,
    },
    *_cards_market_definitions(),
    {
        "key": "MATCH_OVER_15_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 15 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 15,
        "period": "FT",
        "display_order": 390,
    },
    {
        "key": "MATCH_OVER_25_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 25 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 25,
        "period": "FT",
        "display_order": 400,
    },
    {
        "key": "MATCH_OVER_35_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 35 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 35,
        "period": "FT",
        "display_order": 410,
    },
    {
        "key": "MATCH_OVER_45_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 45 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 45,
        "period": "FT",
        "display_order": 420,
    },
    {
        "key": "MATCH_OVER_55_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 55 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 55,
        "period": "FT",
        "display_order": 425,
    },
    {
        "key": "MATCH_OVER_65_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Over 65 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "over",
        "line": 65,
        "period": "FT",
        "display_order": 426,
    },
    {
        "key": "TEAM_OVER_15_BOOKING_POINTS_FOR",
        "category": "booking_points",
        "label": "Over 15 Team Booking Points For",
        "subject": "team",
        "metric": "booking_points_for",
        "operator": "over",
        "line": 15,
        "period": "FT",
        "display_order": 430,
    },
    {
        "key": "TEAM_OVER_25_BOOKING_POINTS_FOR",
        "category": "booking_points",
        "label": "Over 25 Team Booking Points For",
        "subject": "team",
        "metric": "booking_points_for",
        "operator": "over",
        "line": 25,
        "period": "FT",
        "display_order": 440,
    },
    {
        "key": "TEAM_OVER_15_BOOKING_POINTS_AGAINST",
        "category": "booking_points",
        "label": "Over 15 Team Booking Points Against",
        "subject": "opponent",
        "metric": "booking_points_against",
        "operator": "over",
        "line": 15,
        "period": "FT",
        "display_order": 450,
    },
    {
        "key": "TEAM_OVER_25_BOOKING_POINTS_AGAINST",
        "category": "booking_points",
        "label": "Over 25 Team Booking Points Against",
        "subject": "opponent",
        "metric": "booking_points_against",
        "operator": "over",
        "line": 25,
        "period": "FT",
        "display_order": 455,
    },
    {
        "key": "EACH_TEAM_OVER_5_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Each Team Over 5 Booking Points",
        "subject": "each_team",
        "metric": "booking_points",
        "operator": "over",
        "line": 5,
        "period": "FT",
        "display_order": 458,
    },
    {
        "key": "EACH_TEAM_OVER_15_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Each Team Over 15 Booking Points",
        "subject": "each_team",
        "metric": "booking_points",
        "operator": "over",
        "line": 15,
        "period": "FT",
        "display_order": 460,
    },
    {
        "key": "EACH_TEAM_OVER_25_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Each Team Over 25 Booking Points",
        "subject": "each_team",
        "metric": "booking_points",
        "operator": "over",
        "line": 25,
        "period": "FT",
        "display_order": 462,
    },
    {
        "key": "MATCH_UNDER_5_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 5 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 5,
        "period": "FT",
        "display_order": 463,
    },
    {
        "key": "MATCH_UNDER_15_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 15 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 15,
        "period": "FT",
        "display_order": 464,
    },
    {
        "key": "MATCH_UNDER_25_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 25 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 25,
        "period": "FT",
        "display_order": 465,
    },
    {
        "key": "MATCH_UNDER_35_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 35 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 35,
        "period": "FT",
        "display_order": 466,
    },
    {
        "key": "MATCH_UNDER_45_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 45 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 45,
        "period": "FT",
        "display_order": 467,
    },
    {
        "key": "MATCH_UNDER_55_BOOKING_POINTS",
        "category": "booking_points",
        "label": "Under 55 Booking Points",
        "subject": "match",
        "metric": "booking_points",
        "operator": "under",
        "line": 55,
        "period": "FT",
        "display_order": 468,
    },
    {
        "key": "TEAM_UNDER_5_BOOKING_POINTS_FOR",
        "category": "booking_points",
        "label": "Under 5 Team Booking Points For",
        "subject": "team",
        "metric": "booking_points_for",
        "operator": "under",
        "line": 5,
        "period": "FT",
        "display_order": 469,
    },
    {
        "key": "TEAM_UNDER_15_BOOKING_POINTS_FOR",
        "category": "booking_points",
        "label": "Under 15 Team Booking Points For",
        "subject": "team",
        "metric": "booking_points_for",
        "operator": "under",
        "line": 15,
        "period": "FT",
        "display_order": 470,
    },
    {
        "key": "TEAM_UNDER_25_BOOKING_POINTS_FOR",
        "category": "booking_points",
        "label": "Under 25 Team Booking Points For",
        "subject": "team",
        "metric": "booking_points_for",
        "operator": "under",
        "line": 25,
        "period": "FT",
        "display_order": 471,
    },
    {
        "key": "TEAM_UNDER_5_BOOKING_POINTS_AGAINST",
        "category": "booking_points",
        "label": "Under 5 Team Booking Points Against",
        "subject": "opponent",
        "metric": "booking_points_against",
        "operator": "under",
        "line": 5,
        "period": "FT",
        "display_order": 472,
    },
    {
        "key": "TEAM_UNDER_15_BOOKING_POINTS_AGAINST",
        "category": "booking_points",
        "label": "Under 15 Team Booking Points Against",
        "subject": "opponent",
        "metric": "booking_points_against",
        "operator": "under",
        "line": 15,
        "period": "FT",
        "display_order": 473,
    },
    {
        "key": "TEAM_UNDER_25_BOOKING_POINTS_AGAINST",
        "category": "booking_points",
        "label": "Under 25 Team Booking Points Against",
        "subject": "opponent",
        "metric": "booking_points_against",
        "operator": "under",
        "line": 25,
        "period": "FT",
        "display_order": 474,
    },
    *_shots_market_definitions(),
    {
        "key": "MATCH_OVER_20_5_FOULS",
        "category": "fouls",
        "label": "Over 20.5 Match Fouls",
        "subject": "match",
        "metric": "fouls",
        "operator": "over",
        "line": 20.5,
        "period": "FT",
        "display_order": 800,
    },
    {
        "key": "TEAM_OVER_10_5_FOULS_COMMITTED",
        "category": "fouls",
        "label": "Over 10.5 Team Fouls Committed",
        "subject": "team",
        "metric": "fouls_committed",
        "operator": "over",
        "line": 10.5,
        "period": "FT",
        "display_order": 810,
    },
    *_offsides_market_definitions(),
)


def ensure_market_definitions(repository) -> None:
    repository.upsert_market_definitions(list(MARKET_DEFINITIONS))
