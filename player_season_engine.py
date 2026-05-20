from __future__ import annotations

from dataclasses import dataclass

from pipeline_core import StufRepository, is_final_status, utcnow


@dataclass(frozen=True)
class PlayerSeasonContext:
    player_id: int
    scope: str
    appearance: int
    lineup: int
    minutes: int
    goals: int
    assists: int
    total_shots: int
    shots_on_target: int
    yellow_cards: int
    red_cards: int
    fouls_committed: int
    fouls_drawn: int
    tackles: int
    offsides: int


def _int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _per90(total: int, minutes: int) -> float:
    if minutes <= 0:
        return 0.0
    return round((total / minutes) * 90, 2)


def _context_from_player_row(row: dict) -> PlayerSeasonContext:
    minutes = max(0, _int(row.get("minutes")))
    substitute = bool(row.get("substitute"))
    scope = "home" if bool(row.get("is_home")) else "away"
    appearance = 1 if minutes > 0 else 0
    lineup = 1 if appearance and not substitute else 0

    return PlayerSeasonContext(
        player_id=int(row["player_id"]),
        scope=scope,
        appearance=appearance,
        lineup=lineup,
        minutes=minutes,
        goals=max(0, _int(row.get("goals"))),
        assists=max(0, _int(row.get("assists"))),
        total_shots=max(0, _int(row.get("total_shots"))),
        shots_on_target=max(0, _int(row.get("shots_on_target"))),
        yellow_cards=max(0, _int(row.get("yellow_cards"))),
        red_cards=max(0, _int(row.get("red_cards"))),
        fouls_committed=max(0, _int(row.get("fouls_committed"))),
        fouls_drawn=max(0, _int(row.get("fouls_drawn"))),
        tackles=max(0, _int(row.get("tackles"))),
        offsides=max(0, _int(row.get("offsides"))),
    )


def _to_row(
    player_id: int,
    team_id: int,
    league_id: int,
    season: int,
    scope: str,
    contexts: list[PlayerSeasonContext],
) -> dict | None:
    if not contexts:
        return None

    appearances = sum(item.appearance for item in contexts)
    lineups = sum(item.lineup for item in contexts)
    minutes = sum(item.minutes for item in contexts)
    goals = sum(item.goals for item in contexts)
    assists = sum(item.assists for item in contexts)
    total_shots = sum(item.total_shots for item in contexts)
    shots_on_target = sum(item.shots_on_target for item in contexts)
    yellow_cards = sum(item.yellow_cards for item in contexts)
    red_cards = sum(item.red_cards for item in contexts)
    fouls_committed = sum(item.fouls_committed for item in contexts)
    fouls_drawn = sum(item.fouls_drawn for item in contexts)
    tackles = sum(item.tackles for item in contexts)
    offsides = sum(item.offsides for item in contexts)

    return {
        "player_id": player_id,
        "team_id": team_id,
        "league_id": league_id,
        "season": season,
        "scope": scope,
        "appearances": appearances,
        "lineups": lineups,
        "minutes": minutes,
        "goals": goals,
        "assists": assists,
        "total_shots": total_shots,
        "shots_on_target": shots_on_target,
        "yellow_cards": yellow_cards,
        "red_cards": red_cards,
        "fouls_committed": fouls_committed,
        "fouls_drawn": fouls_drawn,
        "tackles": tackles,
        "offsides": offsides,
        "goals_per90": _per90(goals, minutes),
        "assists_per90": _per90(assists, minutes),
        "shots_per90": _per90(total_shots, minutes),
        "shots_on_target_per90": _per90(shots_on_target, minutes),
        "fouls_committed_per90": _per90(fouls_committed, minutes),
        "fouls_drawn_per90": _per90(fouls_drawn, minutes),
        "tackles_per90": _per90(tackles, minutes),
        "offsides_per90": _per90(offsides, minutes),
        "updated_at": utcnow().isoformat(),
    }


def rebuild_player_season_stats(repository: StufRepository, team_id: int, league_id: int, season: int) -> None:
    player_rows = repository.get_player_fixture_stat_rows(team_id, league_id, season)
    contexts = [_context_from_player_row(row) for row in player_rows if row.get("player_id")]

    by_player: dict[int, list[PlayerSeasonContext]] = {}
    for context in contexts:
        by_player.setdefault(context.player_id, []).append(context)

    rows: list[dict] = []
    for player_id, player_contexts in by_player.items():
        rows.extend(
            row
            for row in (
                _to_row(player_id, team_id, league_id, season, "overall", player_contexts),
                _to_row(player_id, team_id, league_id, season, "home", [item for item in player_contexts if item.scope == "home"]),
                _to_row(player_id, team_id, league_id, season, "away", [item for item in player_contexts if item.scope == "away"]),
            )
            if row is not None
        )

    repository.replace_player_season_stats(team_id, league_id, season, rows)


def refresh_player_season_stats_for_fixture(repository: StufRepository, fixture_id: int) -> None:
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
        rebuild_player_season_stats(repository, team_id, league_id, season)
