import argparse
import asyncio
from datetime import datetime, time, timedelta
from typing import Any

from pipeline_core import (
    ApiFootballClient,
    FINAL_STATUSES,
    StufRepository,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_target_leagues,
    utcnow,
)

LOGGER = configure_logging("stuf.player-stats-audit")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita fixtures recientes sin player stats para distinguir huecos de API vs huecos de pipeline."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha base YYYY-MM-DD. Default: hoy UTC.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 39,61,78,135,140.")
    parser.add_argument(
        "--days-back",
        type=int,
        default=20,
        help="Dias hacia atras para revisar fixtures finalizados recientes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Cantidad maxima de fixtures a sondear contra API-Football.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    return parser.parse_args()


def resolve_target_leagues(args: argparse.Namespace) -> tuple[int, ...]:
    settings = load_settings()
    if args.leagues:
        return parse_target_leagues(args.leagues)
    return settings.target_leagues


def chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def apply_league_filter(query, target_leagues: tuple[int, ...], column: str = "league_id"):
    if len(target_leagues) == 1:
        return query.eq(column, target_leagues[0])
    return query.in_(column, list(target_leagues))


def fetch_recent_finished_fixtures(
    repository: StufRepository,
    target_leagues: tuple[int, ...],
    season: int,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    response = repository._execute(
        lambda: apply_league_filter(
            repository.supabase.table("fixtures")
            .select(
                "id,league_id,season,date,status_short,home_team_id,away_team_id,hydrated_players,referee_id,referee_name_raw"
            )
            .eq("season", season)
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", sorted(FINAL_STATUSES)),
            target_leagues,
        ),
        f"load recent finished fixtures season={season}",
    )
    return response.data or []


def build_player_count_map(
    repository: StufRepository,
    fixture_ids: list[int],
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for batch in chunked(fixture_ids, 150):
        offset = 0
        while True:
            response = repository._execute(
                lambda batch=batch, offset=offset: repository.supabase.table("player_fixture_stats")
                .select("fixture_id")
                .in_("fixture_id", batch)
                .range(offset, offset + 999),
                f"load player fixture stats counts batch={len(batch)} offset={offset}",
            )
            batch_rows = response.data or []
            for row in batch_rows:
                fixture_id = row.get("fixture_id")
                if fixture_id is None:
                    continue
                counts[fixture_id] = counts.get(fixture_id, 0) + 1
            if len(batch_rows) < 1000:
                break
            offset += 1000
    return counts


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args)
    target_date = datetime.fromisoformat(args.target_date).date() if args.target_date else utcnow().date()
    recent_start = datetime.combine(target_date - timedelta(days=max(1, args.days_back) - 1), time.min)
    recent_end = datetime.combine(target_date, time.max)

    fixtures = fetch_recent_finished_fixtures(
        repository,
        target_leagues,
        args.season,
        recent_start,
        recent_end,
    )
    fixture_ids = [row["id"] for row in fixtures if row.get("id") is not None]
    player_counts = build_player_count_map(repository, fixture_ids)

    gaps = [
        row
        for row in fixtures
        if not row.get("hydrated_players") or player_counts.get(row["id"], 0) == 0
    ]
    gaps.sort(key=lambda row: (row.get("date") or "", row.get("league_id") or 0), reverse=True)

    LOGGER.info(
        "Auditoria player stats recientes: fixtures_recent=%s gaps=%s sampled=%s",
        len(fixtures),
        len(gaps),
        min(len(gaps), max(1, args.limit)),
    )

    if not gaps:
        LOGGER.info("No se detectaron huecos recientes de player stats.")
        return

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        for row in gaps[: max(1, args.limit)]:
            fixture_id = row["id"]
            payload = await api_client.fetch("fixtures/players", {"fixture": fixture_id})
            response_rows = (payload or {}).get("response", [])
            team_groups = len(response_rows)
            total_players = sum(len(group.get("players") or []) for group in response_rows)
            groups_with_team_id = sum(1 for group in response_rows if (group.get("team") or {}).get("id"))
            players_with_id = 0
            players_without_id = 0
            for group in response_rows:
                for player_item in group.get("players") or []:
                    player = player_item.get("player") or {}
                    if player.get("id"):
                        players_with_id += 1
                    else:
                        players_without_id += 1
            LOGGER.info(
                (
                    "fixture=%s league=%s date=%s hydrated_players=%s stored_rows=%s "
                    "api_team_groups=%s groups_with_team_id=%s api_players=%s "
                    "players_with_id=%s players_without_id=%s"
                ),
                fixture_id,
                row.get("league_id"),
                row.get("date"),
                row.get("hydrated_players"),
                player_counts.get(fixture_id, 0),
                team_groups,
                groups_with_team_id,
                total_players,
                players_with_id,
                players_without_id,
            )


if __name__ == "__main__":
    asyncio.run(main())
