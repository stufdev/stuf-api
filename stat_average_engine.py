from __future__ import annotations

from dataclasses import dataclass

from pipeline_core import StufRepository, is_final_status, utcnow


@dataclass(frozen=True)
class TeamAverageContext:
    scope: str
    goals_for: float | None
    goals_against: float | None
    total_match_goals: float | None
    goals_for_1h: float | None
    goals_against_1h: float | None
    total_1h_goals: float | None
    goals_for_2h: float | None
    goals_against_2h: float | None
    total_2h_goals: float | None
    corners_for: float | None
    corners_against: float | None
    total_corners: float | None
    corners_for_1h: float | None
    corners_against_1h: float | None
    total_corners_1h: float | None
    corners_for_2h: float | None
    corners_against_2h: float | None
    total_corners_2h: float | None
    cards_for: float | None
    cards_against: float | None
    total_cards: float | None
    booking_points_for: float | None
    booking_points_against: float | None
    total_booking_points: float | None
    fouls_committed: float | None
    fouls_won: float | None
    total_fouls: float | None
    offsides_for: float | None
    offsides_against: float | None
    total_offsides: float | None
    total_shots_for: float | None
    total_shots_against: float | None
    shots_on_target_for: float | None
    shots_on_target_against: float | None
    goal_kicks_for: float | None
    goal_kicks_against: float | None
    total_goal_kicks: float | None
    throw_ins_for: float | None
    throw_ins_against: float | None
    total_throw_ins: float | None
    tackles_for: float | None
    tackles_against: float | None
    total_tackles: float | None


def _num(value) -> float | None:
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


def _avg(total: float, count: int) -> float | None:
    if count == 0:
        return None
    return round(total / count, 1)


def _context_from_fact(row: dict) -> TeamAverageContext:
    return TeamAverageContext(
        scope=str(row.get("venue_scope") or "overall"),
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
        corners_for_1h=_num(row.get("corners_for_1h")),
        corners_against_1h=_num(row.get("corners_against_1h")),
        total_corners_1h=_num(row.get("total_corners_1h")),
        corners_for_2h=_num(row.get("corners_for_2h")),
        corners_against_2h=_num(row.get("corners_against_2h")),
        total_corners_2h=_num(row.get("total_corners_2h")),
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
        goal_kicks_for=_num(row.get("goal_kicks_for")),
        goal_kicks_against=_num(row.get("goal_kicks_against")),
        total_goal_kicks=_num(row.get("total_goal_kicks")),
        throw_ins_for=_num(row.get("throw_ins_for")),
        throw_ins_against=_num(row.get("throw_ins_against")),
        total_throw_ins=_num(row.get("total_throw_ins")),
        tackles_for=_num(row.get("tackles_for")),
        tackles_against=_num(row.get("tackles_against")),
        total_tackles=_num(row.get("total_tackles")),
    )


def _to_row(team_id: int, league_id: int, season: int, scope: str, contexts: list[TeamAverageContext]) -> dict | None:
    matches_played = len(contexts)
    if matches_played == 0:
        return None

    def average_for(field: str) -> float | None:
        values = [value for value in (getattr(item, field) for item in contexts) if value is not None]
        if not values:
            return None
        return _avg(sum(values), len(values))

    return {
        "team_id": team_id,
        "league_id": league_id,
        "season": season,
        "scope": scope,
        "matches_played": matches_played,
        "avg_goals_total": average_for("total_match_goals"),
        "avg_goals_for": average_for("goals_for"),
        "avg_goals_against": average_for("goals_against"),
        "avg_1h_goals_total": average_for("total_1h_goals"),
        "avg_1h_goals_for": average_for("goals_for_1h"),
        "avg_1h_goals_against": average_for("goals_against_1h"),
        "avg_2h_goals_total": average_for("total_2h_goals"),
        "avg_2h_goals_for": average_for("goals_for_2h"),
        "avg_2h_goals_against": average_for("goals_against_2h"),
        "avg_corners_total": average_for("total_corners"),
        "avg_corners_for": average_for("corners_for"),
        "avg_corners_against": average_for("corners_against"),
        "avg_1h_corners_total": average_for("total_corners_1h"),
        "avg_1h_corners_for": average_for("corners_for_1h"),
        "avg_1h_corners_against": average_for("corners_against_1h"),
        "avg_2h_corners_total": average_for("total_corners_2h"),
        "avg_2h_corners_for": average_for("corners_for_2h"),
        "avg_2h_corners_against": average_for("corners_against_2h"),
        "avg_cards_total": average_for("total_cards"),
        "avg_cards_for": average_for("cards_for"),
        "avg_cards_against": average_for("cards_against"),
        "avg_booking_points_total": average_for("total_booking_points"),
        "avg_booking_points_for": average_for("booking_points_for"),
        "avg_booking_points_against": average_for("booking_points_against"),
        "avg_fouls_total": average_for("total_fouls"),
        "avg_fouls_committed": average_for("fouls_committed"),
        "avg_fouls_won": average_for("fouls_won"),
        "avg_offsides_total": average_for("total_offsides"),
        "avg_offsides_for": average_for("offsides_for"),
        "avg_offsides_against": average_for("offsides_against"),
        "avg_total_shots_for": average_for("total_shots_for"),
        "avg_total_shots_against": average_for("total_shots_against"),
        "avg_shots_on_target_for": average_for("shots_on_target_for"),
        "avg_shots_on_target_against": average_for("shots_on_target_against"),
        "avg_goal_kicks_total": average_for("total_goal_kicks"),
        "avg_goal_kicks_for": average_for("goal_kicks_for"),
        "avg_goal_kicks_against": average_for("goal_kicks_against"),
        "avg_throw_ins_total": average_for("total_throw_ins"),
        "avg_throw_ins_for": average_for("throw_ins_for"),
        "avg_throw_ins_against": average_for("throw_ins_against"),
        "avg_tackles_total": average_for("total_tackles"),
        "avg_tackles_for": average_for("tackles_for"),
        "avg_tackles_against": average_for("tackles_against"),
        "updated_at": utcnow().isoformat(),
    }


def rebuild_team_stat_averages(repository: StufRepository, team_id: int, league_id: int, season: int) -> None:
    fact_rows = repository.get_team_fixture_fact_rows(team_id, league_id, season)
    contexts = [_context_from_fact(row) for row in fact_rows]

    rows = [
        row
        for row in (
            _to_row(team_id, league_id, season, "overall", contexts),
            _to_row(team_id, league_id, season, "home", [item for item in contexts if item.scope == "home"]),
            _to_row(team_id, league_id, season, "away", [item for item in contexts if item.scope == "away"]),
        )
        if row is not None
    ]
    repository.replace_team_stat_averages(team_id, league_id, season, rows)


def refresh_stat_averages_for_fixture(repository: StufRepository, fixture_id: int) -> None:
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
        rebuild_team_stat_averages(repository, team_id, league_id, season)
