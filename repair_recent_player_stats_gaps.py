import argparse
import asyncio
from datetime import datetime, time, timedelta

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

LOGGER = configure_logging("stuf.player-stats-repair")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repara fixtures recientes sin player stats rehidratando solo /fixtures/players."
    )
    parser.add_argument("--date", dest="target_date", help="Fecha base YYYY-MM-DD. Default: hoy UTC.")
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY.")
    parser.add_argument("--leagues", help="Lista CSV de league_id. Ej: 39,61,78,135,140.")
    parser.add_argument("--days-back", type=int, default=20, help="Dias hacia atras para revisar fixtures finalizados.")
    parser.add_argument("--limit", type=int, help="Limita la cantidad de fixtures a reparar.")
    parser.add_argument("--request-delay", type=float, default=1.0, help="Pausa minima entre requests a API-Football.")
    return parser.parse_args()


def resolve_target_leagues(args: argparse.Namespace) -> tuple[int, ...]:
    settings = load_settings()
    if args.leagues:
        return parse_target_leagues(args.leagues)
    return settings.target_leagues


def apply_league_filter(query, target_leagues: tuple[int, ...], column: str = "league_id"):
    if len(target_leagues) == 1:
        return query.eq(column, target_leagues[0])
    return query.in_(column, list(target_leagues))


def load_candidate_fixtures(
    repository: StufRepository,
    target_leagues: tuple[int, ...],
    season: int,
    start_at: datetime,
    end_at: datetime,
) -> list[dict]:
    response = repository._execute(
        lambda: apply_league_filter(
            repository.supabase.table("fixtures")
            .select("id,league_id,season,date,status_short,hydrated_players")
            .eq("season", season)
            .gte("date", start_at.isoformat())
            .lte("date", end_at.isoformat())
            .in_("status_short", sorted(FINAL_STATUSES)),
            target_leagues,
        ),
        f"load recent fixtures for player repair season={season}",
    )
    return response.data or []


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args)
    target_date = datetime.fromisoformat(args.target_date).date() if args.target_date else utcnow().date()
    start_at = datetime.combine(target_date - timedelta(days=max(1, args.days_back) - 1), time.min)
    end_at = datetime.combine(target_date, time.max)

    fixtures = load_candidate_fixtures(repository, target_leagues, args.season, start_at, end_at)
    gaps: list[dict] = []
    for row in fixtures:
        fixture_id = row.get("id")
        if fixture_id is None:
            continue
        stored_rows = repository.count_player_stats_rows(fixture_id)
        if stored_rows == 0:
            gaps.append(row)

    gaps.sort(key=lambda row: row.get("date") or "", reverse=True)
    if args.limit is not None:
        gaps = gaps[: max(0, args.limit)]

    LOGGER.info("Player stats repair: candidates=%s", len(gaps))
    if not gaps:
        LOGGER.info("No hay fixtures recientes pendientes de repair en player stats.")
        return

    repaired = 0
    still_missing = 0
    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        for row in gaps:
            fixture_id = row["id"]
            payload = await api_client.fetch("fixtures/players", {"fixture": fixture_id})
            player_groups = (payload or {}).get("response", [])
            if not player_groups:
                repository.mark_fixture_hydration(fixture_id, hydrated_players=False)
                still_missing += 1
                LOGGER.warning("fixture=%s league=%s sin player_groups en API.", fixture_id, row.get("league_id"))
                continue

            persisted_rows = repository.replace_player_stats(fixture_id, player_groups)
            repository.mark_fixture_hydration(fixture_id, hydrated_players=persisted_rows > 0)
            if persisted_rows > 0:
                repaired += 1
                LOGGER.info(
                    "fixture=%s league=%s repaired persisted_rows=%s",
                    fixture_id,
                    row.get("league_id"),
                    persisted_rows,
                )
            else:
                still_missing += 1
                LOGGER.error(
                    "fixture=%s league=%s sigue sin player stats tras repair.",
                    fixture_id,
                    row.get("league_id"),
                )

    LOGGER.info(
        "Player stats repair finalizado. repaired=%s still_missing=%s",
        repaired,
        still_missing,
    )


if __name__ == "__main__":
    asyncio.run(main())
