from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Sequence

from pipeline_core import StufRepository, configure_logging, create_supabase_client, load_settings, parse_int, utcnow
from player_season_engine import _to_row as player_season_row
from player_season_engine import PlayerSeasonContext

LOGGER = configure_logging("stuf.national-player-evidence")


PLAYER_FIXTURE_SELECT = """
fixture_id,
player_id,
team_id,
league_id,
season,
played_at,
is_home,
minutes,
substitute,
goals,
assists,
total_shots,
shots_on_target,
yellow_cards,
red_cards,
fouls_committed,
fouls_drawn,
tackles,
offsides
"""


@dataclass(frozen=True)
class EvidenceSource:
    target_league_id: int
    target_season: int
    source_league_id: int
    source_season: int
    source_label: str
    priority: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project national-team player stats into World Cup scope.")
    parser.add_argument("--target-league", type=int, default=1)
    parser.add_argument("--target-season", type=int, default=2026)
    parser.add_argument("--window-months", type=int, default=24)
    parser.add_argument("--full-refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def select_all(
    repository: StufRepository,
    table: str,
    select: str,
    *,
    eq: dict[str, Any] | None = None,
    in_filters: dict[str, Sequence[Any]] | None = None,
    gte: dict[str, Any] | None = None,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        def request(offset: int = offset):
            query = repository.supabase.table(table).select(select)
            for key, value in (eq or {}).items():
                query = query.eq(key, value)
            for key, values in (in_filters or {}).items():
                query = query.in_(key, list(values))
            for key, value in (gte or {}).items():
                query = query.gte(key, value)
            return query.range(offset, offset + page_size - 1)

        response = repository._execute(request, f"select {table} offset={offset}")
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def load_sources(repository: StufRepository, target_league_id: int, target_season: int) -> list[EvidenceSource]:
    rows = select_all(
        repository,
        "national_team_evidence_sources",
        "target_league_id,target_season,source_league_id,source_season,source_label,priority,is_active",
        eq={"target_league_id": target_league_id, "target_season": target_season, "is_active": True},
    )
    return sorted(
        [
            EvidenceSource(
                target_league_id=int(row["target_league_id"]),
                target_season=int(row["target_season"]),
                source_league_id=int(row["source_league_id"]),
                source_season=int(row["source_season"]),
                source_label=str(row["source_label"]),
                priority=int(row.get("priority") or 50),
            )
            for row in rows
        ],
        key=lambda item: (item.priority, item.source_league_id, item.source_season),
    )


def load_target_team_ids(repository: StufRepository, target_league_id: int, target_season: int) -> tuple[int, ...]:
    rows = select_all(
        repository,
        "team_league_seasons",
        "team_id",
        eq={"league_id": target_league_id, "season": target_season, "is_active": True},
    )
    return tuple(sorted({int(row["team_id"]) for row in rows if row.get("team_id") is not None}))


def load_source_player_rows(
    repository: StufRepository,
    *,
    team_ids: tuple[int, ...],
    sources: list[EvidenceSource],
    cutoff_iso: str,
) -> list[dict[str, Any]]:
    source_pairs = {(source.source_league_id, source.source_season) for source in sources}
    league_ids = sorted({league_id for league_id, _ in source_pairs})
    seasons = sorted({season for _, season in source_pairs})
    rows: list[dict[str, Any]] = []

    for team_chunk in chunked(team_ids, 50):
        page = select_all(
            repository,
            "player_fixture_stats",
            PLAYER_FIXTURE_SELECT,
            in_filters={"team_id": team_chunk, "league_id": league_ids, "season": seasons},
            gte={"played_at": cutoff_iso},
        )
        rows.extend(
            row
            for row in page
            if (int(row["league_id"]), int(row["season"])) in source_pairs
            and parse_int(row.get("minutes"), 0) > 0
        )
    return rows


def context_from_row(row: dict[str, Any]) -> PlayerSeasonContext:
    minutes = max(0, parse_int(row.get("minutes"), 0))
    appearance = 1 if minutes > 0 else 0
    substitute = bool(row.get("substitute"))
    return PlayerSeasonContext(
        player_id=int(row["player_id"]),
        scope="home" if bool(row.get("is_home")) else "away",
        appearance=appearance,
        lineup=1 if appearance and not substitute else 0,
        minutes=minutes,
        goals=max(0, parse_int(row.get("goals"), 0)),
        assists=max(0, parse_int(row.get("assists"), 0)),
        total_shots=max(0, parse_int(row.get("total_shots"), 0)),
        shots_on_target=max(0, parse_int(row.get("shots_on_target"), 0)),
        yellow_cards=max(0, parse_int(row.get("yellow_cards"), 0)),
        red_cards=max(0, parse_int(row.get("red_cards"), 0)),
        fouls_committed=max(0, parse_int(row.get("fouls_committed"), 0)),
        fouls_drawn=max(0, parse_int(row.get("fouls_drawn"), 0)),
        tackles=max(0, parse_int(row.get("tackles"), 0)),
        offsides=max(0, parse_int(row.get("offsides"), 0)),
    )


def build_projected_rows(
    rows: list[dict[str, Any]],
    *,
    target_league_id: int,
    target_season: int,
    projected_source: dict[str, Any],
) -> list[dict[str, Any]]:
    contexts_by_player_team: dict[tuple[int, int], list[PlayerSeasonContext]] = defaultdict(list)
    for row in rows:
        contexts_by_player_team[(int(row["player_id"]), int(row["team_id"]))].append(context_from_row(row))

    output: list[dict[str, Any]] = []
    for (player_id, team_id), contexts in sorted(contexts_by_player_team.items()):
        output.extend(
            {
                **item,
                "projected_source": projected_source,
            }
            for item in (
                player_season_row(player_id, team_id, target_league_id, target_season, "overall", contexts),
                player_season_row(player_id, team_id, target_league_id, target_season, "home", [ctx for ctx in contexts if ctx.scope == "home"]),
                player_season_row(player_id, team_id, target_league_id, target_season, "away", [ctx for ctx in contexts if ctx.scope == "away"]),
            )
            if item is not None and int(item.get("minutes") or 0) > 0
        )
    return output


def delete_projected_rows(repository: StufRepository, target_league_id: int, target_season: int) -> None:
    # The whole player_tournament_projected_stats table is projection-only, so a
    # full-refresh deletes every row for the target (league, season).
    def request():
        return (
            repository.supabase.table("player_tournament_projected_stats")
            .delete()
            .eq("league_id", target_league_id)
            .eq("season", target_season)
        )

    repository._execute(request, f"delete projected player_tournament_projected_stats league={target_league_id} season={target_season}")


def main() -> None:
    args = parse_args()
    settings = load_settings()
    repository = StufRepository(create_supabase_client(settings), LOGGER)

    sources = load_sources(repository, args.target_league, args.target_season)
    if not sources:
        raise RuntimeError("No active national_team_evidence_sources rows found.")
    target_team_ids = load_target_team_ids(repository, args.target_league, args.target_season)
    if not target_team_ids:
        raise RuntimeError("No target World Cup teams found.")

    cutoff = utcnow() - timedelta(days=max(1, args.window_months) * 31)
    source_rows = load_source_player_rows(
        repository,
        team_ids=target_team_ids,
        sources=sources,
        cutoff_iso=cutoff.isoformat(),
    )
    source_pairs = sorted({(source.source_league_id, source.source_season) for source in sources})
    projected_source = {
        "projection": "national_team_player_evidence_v1",
        "target_league_id": args.target_league,
        "target_season": args.target_season,
        "window_months": args.window_months,
        "built_at": utcnow().isoformat(),
        "source_pairs": [
            {"league_id": league_id, "season": season}
            for league_id, season in source_pairs
        ],
        "source_fixture_count": len({int(row["fixture_id"]) for row in source_rows}),
    }
    projected_rows = build_projected_rows(
        source_rows,
        target_league_id=args.target_league,
        target_season=args.target_season,
        projected_source=projected_source,
    )
    covered_teams = {int(row["team_id"]) for row in projected_rows}
    LOGGER.info(
        "Player projection plan: target_teams=%s source_player_rows=%s source_fixtures=%s projected_rows=%s teams_covered=%s dry_run=%s",
        len(target_team_ids),
        len(source_rows),
        projected_source["source_fixture_count"],
        len(projected_rows),
        len(covered_teams),
        args.dry_run,
    )
    missing = sorted(set(target_team_ids) - covered_teams)
    if missing:
        LOGGER.warning("No projected player rows for target team_ids=%s", missing)
    if args.dry_run:
        return

    if args.full_refresh:
        delete_projected_rows(repository, args.target_league, args.target_season)

    if projected_rows:
        repository._upsert_rows(
            "player_tournament_projected_stats",
            projected_rows,
            "player_id,team_id,league_id,season,scope",
            "upsert projected national player_tournament_projected_stats",
        )
    LOGGER.info("Projected national player evidence ready rows=%s teams=%s.", len(projected_rows), len(covered_teams))


if __name__ == "__main__":
    main()
