"""
fetch_pre_match_odds.py — STUF Pre-Match Odds Capture

Captures odds snapshots for upcoming fixtures from ALL bookmakers referenced
in market_price_policy (reference + fallback arrays). Each bookmaker is fetched
separately and stored as an independent fixture_odds_snapshots row, allowing the
normalizer to pick the best source per market per policy.

Architecture:
  - Reference bookmakers (Pinnacle/Betfair): priced for fair probability + edge
  - Fallback bookmakers (1xBet/Betano/Bet365): priced for conditional markets
    (btts, cards, fouls, offsides) — display only, not edge calculation

PRIOR DESIGN: Pinnacle-only (single bookmaker).
CURRENT DESIGN: All bookmakers from active market_price_policy.
  Fallback: uses PINNACLE_BOOKMAKER_NAME env var if policy table is empty.

Usage:
  python fetch_pre_match_odds.py --window-hours 96 --season 2026 --leagues 1
"""
import asyncio
from datetime import timedelta

from pipeline_core import (
    UPCOMING_STATUSES,
    ApiFootballClient,
    StufRepository,
    configure_logging,
    create_supabase_client,
    league_supports,
    load_settings,
    parse_cli_args,
    resolve_target_leagues,
    sync_reference_catalogs,
    utcnow,
)

LOGGER = configure_logging("stuf.odds")


def resolve_bookmaker(repository: StufRepository, bookmaker_name: str) -> dict | None:
    """Resolves a bookmaker name to its catalog row (case-insensitive)."""
    normalized_target = bookmaker_name.strip().lower()
    for bookmaker in repository.get_bookmakers():
        name = (bookmaker.get("name") or "").strip().lower()
        if name == normalized_target:
            return bookmaker
    return None


def resolve_policy_bookmakers(
    repository: StufRepository,
    fallback_name: str,
) -> list[dict]:
    """
    Returns the ordered list of bookmaker catalog rows to fetch odds for.

    Order: reference-tier bookmakers first (Pinnacle/Betfair), then fallback
    (1xBet/Betano/Bet365/...). Deduplicates by bookmaker_id.

    Falls back to [fallback_name] (typically Pinnacle from PINNACLE_BOOKMAKER_NAME)
    if market_price_policy is empty or the query fails.

    Logs a warning for any policy bookmaker name not found in api_reference_bookmakers.
    That indicates sync_reference_catalogs has not been run yet for that bookmaker.
    """
    try:
        response = repository._execute(
            lambda: repository.supabase.table("market_price_policy")
            .select("reference_bookmaker_names,fallback_bookmaker_names")
            .eq("active", True),
            "load policy bookmaker names",
        )
        rows = response.data or []
    except Exception as exc:
        LOGGER.warning(
            "No se pudo cargar bookmakers desde market_price_policy: %s. "
            "Usando solo el bookmaker de fallback: %s.",
            exc,
            fallback_name,
        )
        rows = []

    # Collect unique names in priority order: reference arrays first, then fallback arrays
    seen_names: set[str] = set()
    ordered_names: list[str] = []
    for field in ("reference_bookmaker_names", "fallback_bookmaker_names"):
        for row in rows:
            for name in row.get(field) or []:
                if name and name not in seen_names:
                    seen_names.add(name)
                    ordered_names.append(name)

    if not ordered_names:
        LOGGER.info(
            "market_price_policy sin bookmakers activos. "
            "Usando solo: %s.",
            fallback_name,
        )
        ordered_names = [fallback_name]

    result: list[dict] = []
    seen_ids: set[int] = set()
    for name in ordered_names:
        bm = resolve_bookmaker(repository, name)
        if bm is None:
            LOGGER.warning(
                "Bookmaker '%s' referenciado en policy pero no encontrado en "
                "api_reference_bookmakers. Ejecutar sync_reference_catalogs primero.",
                name,
            )
            continue
        bm_id = bm.get("id")
        if bm_id in seen_ids:
            continue
        seen_ids.add(bm_id)
        result.append(bm)
        LOGGER.debug("Bookmaker incluido: %s (id=%s)", name, bm_id)

    if not result:
        LOGGER.error(
            "Ningún bookmaker de policy resolvió en el catálogo. "
            "Verificar que sync_reference_catalogs se haya ejecutado."
        )

    return result


async def main() -> None:
    args = parse_cli_args("Carril D - captura de cuotas pre-match (multi-bookmaker).")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)
    target_leagues = resolve_target_leagues(args, settings, repository, season=args.season)

    async with ApiFootballClient(settings, LOGGER, request_delay_seconds=args.request_delay) as api_client:
        # 1. Sync reference catalogs (populates api_reference_bookmakers + api_reference_bets)
        coverage_map = await sync_reference_catalogs(
            api_client, repository, settings, target_leagues=target_leagues
        )

        # 2. Resolve all bookmakers from active market_price_policy
        bookmakers_to_fetch = resolve_policy_bookmakers(repository, settings.pinnacle_bookmaker_name)
        if not bookmakers_to_fetch:
            LOGGER.error(
                "Sin bookmakers a capturar. Verificar market_price_policy y "
                "que sync_reference_catalogs haya corrido."
            )
            return

        LOGGER.info(
            "Bookmakers a capturar: %s",
            ", ".join(bm["name"] for bm in bookmakers_to_fetch),
        )

        # 3. Find upcoming fixtures in window
        window_start = utcnow()
        window_end = window_start + timedelta(hours=args.window_hours)
        candidate_fixtures = repository.get_candidate_fixtures(
            window_start, window_end, sorted(UPCOMING_STATUSES)
        )
        filtered_candidates = [
            row
            for row in candidate_fixtures
            if row.get("league_id") in target_leagues
            and league_supports(coverage_map, row.get("league_id"), row.get("season"), "odds")
        ]

        if not filtered_candidates:
            LOGGER.info("No hay fixtures con odds habilitados en la ventana %sh.", args.window_hours)
            return

        fixture_ids = {row["id"] for row in filtered_candidates}
        dates = sorted(
            {row["date"].split("T")[0] for row in filtered_candidates if row.get("date")}
        )
        captured_at = utcnow()

        LOGGER.info(
            "Capturando odds para %d fixtures en %d fechas × %d bookmakers.",
            len(fixture_ids),
            len(dates),
            len(bookmakers_to_fetch),
        )

        # 4. Fetch odds per bookmaker × date
        total_snapshots = 0
        for bookmaker in bookmakers_to_fetch:
            bm_snapshots = 0
            LOGGER.info("  → Bookmaker: %s (id=%s)", bookmaker["name"], bookmaker["id"])

            for date_value in dates:
                odds_rows = await api_client.fetch_paginated(
                    "odds",
                    {"date": date_value, "bookmaker": bookmaker["id"]},
                )
                scoped_rows = [
                    row
                    for row in odds_rows
                    if (row.get("fixture") or {}).get("id") in fixture_ids
                ]

                if scoped_rows:
                    repository.store_odds_snapshots(
                        market_scope="prematch",
                        captured_at=captured_at,
                        bookmaker_id=bookmaker["id"],
                        bookmaker_name=bookmaker["name"],
                        odds_items=scoped_rows,
                    )
                    bm_snapshots += len(scoped_rows)

                    # Mark fixtures as having odds hydrated
                    for row in scoped_rows:
                        fixture_id = (row.get("fixture") or {}).get("id")
                        if fixture_id:
                            repository.mark_fixture_hydration(fixture_id, hydrated_odds=True)

                await asyncio.sleep(0.5)

            LOGGER.info(
                "    %s: %d snapshots capturados.",
                bookmaker["name"],
                bm_snapshots,
            )
            total_snapshots += bm_snapshots

    LOGGER.info(
        "Captura pre-match completada. Total snapshots: %d. captured_at=%s.",
        total_snapshots,
        captured_at.isoformat(),
    )


if __name__ == "__main__":
    asyncio.run(main())
