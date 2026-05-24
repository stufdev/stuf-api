import argparse
import asyncio
from typing import Any

from pipeline_core import (
    ApiFootballClient,
    FINAL_STATUSES,
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    resolve_target_leagues,
    utcnow,
)


LOGGER = configure_logging("stuf.repair-half-statistics")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repara fixtures ya cargados trayendo solo /fixtures/statistics?half=true "
            "para persistir statistics_1h/statistics_2h sin repetir el backfill historico completo."
        )
    )
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY a reparar.")
    parser.add_argument("--leagues", help="Lista CSV de league_id, ej: 140 o 39,61,78,135,140.")
    parser.add_argument("--fixture", type=int, help="Reparar un fixture puntual.")
    parser.add_argument("--from-date", help="Fecha inicial YYYY-MM-DD opcional para acotar fixtures.")
    parser.add_argument("--to-date", help="Fecha final YYYY-MM-DD opcional para acotar fixtures.")
    parser.add_argument("--limit", type=int, help="Maximo de fixtures a reparar por liga.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Procesa tambien fixtures que ya tienen 1H. Util para reprocesar tras cambios de parser.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra cuantos fixtures repararia. No llama API-Football ni escribe en Supabase.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Pausa minima entre requests a API-Football.",
    )
    return parser.parse_args()


def fetch_final_fixture_ids(
    repository: StufRepository,
    *,
    league_id: int,
    season: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[int]:
    rows: list[dict[str, Any]] = []
    page_size = 1000
    offset = 0

    while True:
        def request(offset: int = offset):
            query = (
                repository.supabase.table("fixtures")
                .select("id,date,status_short")
                .eq("league_id", league_id)
                .eq("season", season)
                .in_("status_short", sorted(FINAL_STATUSES))
                .order("date", desc=False)
                .range(offset, offset + page_size - 1)
            )
            if from_date:
                query = query.gte("date", from_date)
            if to_date:
                query = query.lte("date", f"{to_date}T23:59:59+00:00")
            return query

        response = repository._execute(
            request,
            f"load final fixtures league={league_id} season={season} offset={offset}",
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    return [int(row["id"]) for row in rows if row.get("id") is not None]


def load_fixture_ids_with_period(repository: StufRepository, fixture_ids: list[int], period: str) -> set[int]:
    found: set[int] = set()
    for batch in chunked(fixture_ids, 300):
        response = repository._execute(
            lambda batch=batch: repository.supabase.table("fixture_statistics")
            .select("fixture_id")
            .in_("fixture_id", list(batch))
            .eq("period", period),
            f"load fixture_statistics period={period} batch={len(batch)}",
        )
        found.update(int(row["fixture_id"]) for row in response.data or [] if row.get("fixture_id") is not None)
    return found


def select_repair_candidates(
    repository: StufRepository,
    fixture_ids: list[int],
    *,
    force: bool,
    limit: int | None = None,
) -> list[int]:
    if not fixture_ids:
        return []

    with_ft = load_fixture_ids_with_period(repository, fixture_ids, "FT")
    with_1h = load_fixture_ids_with_period(repository, fixture_ids, "1H")

    candidates = [
        fixture_id
        for fixture_id in fixture_ids
        if fixture_id in with_ft and (force or fixture_id not in with_1h)
    ]
    return candidates[:limit] if limit and limit > 0 else candidates


def payload_has_half_statistics(rows: list[dict[str, Any]]) -> bool:
    return any(row.get("statistics_1h") or row.get("statistics_2h") for row in rows)


def count_period_rows(repository: StufRepository, fixture_id: int, period: str) -> int:
    response = repository._execute(
        lambda: repository.supabase.table("fixture_statistics")
        .select("team_id")
        .eq("fixture_id", fixture_id)
        .eq("period", period),
        f"count fixture_statistics fixture={fixture_id} period={period}",
    )
    return len(response.data or [])


async def repair_fixture(
    api_client: ApiFootballClient,
    repository: StufRepository,
    fixture_id: int,
) -> bool:
    payload = await api_client.fetch("fixtures/statistics", {"fixture": fixture_id, "half": "true"})
    rows = (payload or {}).get("response", [])
    if len(rows) < 2:
        LOGGER.warning("Fixture %s sin response completo de statistics?half=true. Se omite.", fixture_id)
        return False
    if not payload_has_half_statistics(rows):
        LOGGER.warning("Fixture %s no trae statistics_1h/statistics_2h. Se omite.", fixture_id)
        return False

    repository.replace_fixture_statistics(fixture_id, rows, rows)
    facts_replaced = repository.replace_team_fixture_facts(fixture_id)
    repository.mark_fixture_hydration(fixture_id, hydrated_statistics=True)

    first_half_count = count_period_rows(repository, fixture_id, "1H")
    second_half_count = count_period_rows(repository, fixture_id, "2H")
    if first_half_count < 2 or second_half_count < 2:
        LOGGER.warning(
            "Fixture %s reparado parcialmente: 1H rows=%s 2H rows=%s facts=%s",
            fixture_id,
            first_half_count,
            second_half_count,
            facts_replaced,
        )
        return False

    if not facts_replaced:
        LOGGER.warning("Fixture %s persistio 1H/2H pero no regenero team_fixture_facts.", fixture_id)
        return False

    return True


async def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    if args.fixture:
        candidates_by_league = {"manual": [args.fixture]}
    else:
        target_leagues = resolve_target_leagues(args, settings, repository, season=args.season)
        candidates_by_league: dict[int | str, list[int]] = {}
        for league_id in target_leagues:
            fixture_ids = fetch_final_fixture_ids(
                repository,
                league_id=league_id,
                season=args.season,
                from_date=args.from_date,
                to_date=args.to_date,
            )
            candidates = select_repair_candidates(
                repository,
                fixture_ids,
                force=args.force,
                limit=args.limit,
            )
            candidates_by_league[league_id] = candidates
            LOGGER.info(
                "Liga %s temporada %s: final fixtures=%s candidates=%s force=%s",
                league_id,
                args.season,
                len(fixture_ids),
                len(candidates),
                args.force,
            )

    all_candidates = [fixture_id for fixture_ids in candidates_by_league.values() for fixture_id in fixture_ids]
    if args.dry_run:
        preview = ", ".join(str(fixture_id) for fixture_id in all_candidates[:20])
        LOGGER.info(
            "Dry-run: repararia %s fixtures. Preview=%s",
            len(all_candidates),
            preview or "-",
        )
        return

    started_at = utcnow()
    repaired = 0
    failed = 0

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        for index, fixture_id in enumerate(all_candidates, start=1):
            try:
                LOGGER.info("Repair half statistics fixture=%s (%s/%s)", fixture_id, index, len(all_candidates))
                if await repair_fixture(api_client, repository, fixture_id):
                    repaired += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                LOGGER.exception("Fixture %s fallo durante repair_half_statistics: %s", fixture_id, exc)

    elapsed = utcnow() - started_at
    LOGGER.info(
        "Repair half statistics finalizado: candidates=%s repaired=%s failed=%s elapsed=%s",
        len(all_candidates),
        repaired,
        failed,
        elapsed,
    )


if __name__ == "__main__":
    asyncio.run(main())
