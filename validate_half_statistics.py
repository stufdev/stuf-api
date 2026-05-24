import argparse
import sys
from typing import Any

from market_catalog import MARKET_DEFINITIONS
from pipeline_core import (
    FINAL_STATUSES,
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    resolve_target_leagues,
)


LOGGER = configure_logging("stuf.validate-half-statistics")
DEFAULT_MARKET_KEY = "MATCH_1H_OVER_3_5_CORNERS"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida que Corners By Half este cargado de punta a punta: stats, facts y market rollups."
    )
    parser.add_argument("--season", type=int, default=2025, help="Temporada YYYY a validar.")
    parser.add_argument("--leagues", help="Lista CSV de league_id, ej: 140 o 39,61,78,135,140.")
    parser.add_argument(
        "--market-key",
        default=DEFAULT_MARKET_KEY,
        help=f"Market key By Half usado para validar rollups. Default: {DEFAULT_MARKET_KEY}.",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=0.98,
        help="Cobertura minima aceptable de fixtures con 1H/2H sobre fixtures FT.",
    )
    parser.add_argument(
        "--min-overall-sample",
        type=int,
        default=10,
        help="Sample minimo recomendado para warning de equipos atipicos. No falla la validacion por si solo.",
    )
    return parser.parse_args()


def fetch_all(request_factory, *, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = request_factory(offset, page_size).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        offset += page_size


def by_half_market_keys() -> list[str]:
    return [
        str(definition["key"])
        for definition in MARKET_DEFINITIONS
        if definition.get("category") == "corners"
        and (
            definition.get("period") in {"1H", "2H"}
            or "EACH_HALF" in str(definition.get("key"))
        )
    ]


def load_final_fixture_ids(repository: StufRepository, league_id: int, season: int) -> list[int]:
    rows = fetch_all(
        lambda offset, page_size: repository.supabase.table("fixtures")
        .select("id,date,status_short")
        .eq("league_id", league_id)
        .eq("season", season)
        .in_("status_short", sorted(FINAL_STATUSES))
        .order("date", desc=False)
        .range(offset, offset + page_size - 1)
    )
    return [int(row["id"]) for row in rows if row.get("id") is not None]


def load_fixture_ids_with_period(repository: StufRepository, fixture_ids: list[int], period: str) -> set[int]:
    found: set[int] = set()
    for batch in chunked(fixture_ids, 300):
        response = repository._execute(
            lambda batch=batch: repository.supabase.table("fixture_statistics")
            .select("fixture_id")
            .in_("fixture_id", list(batch))
            .eq("period", period),
            f"validate fixture_statistics period={period} batch={len(batch)}",
        )
        found.update(int(row["fixture_id"]) for row in response.data or [] if row.get("fixture_id") is not None)
    return found


def load_fact_rows(repository: StufRepository, league_id: int, season: int) -> list[dict[str, Any]]:
    return fetch_all(
        lambda offset, page_size: repository.supabase.table("team_fixture_facts")
        .select("fixture_id,team_id,total_corners_1h,total_corners_2h,corners_for_1h,corners_for_2h")
        .eq("league_id", league_id)
        .eq("season", season)
        .range(offset, offset + page_size - 1)
    )


def load_market_stat_rows(
    repository: StufRepository,
    league_id: int,
    season: int,
    market_key: str,
) -> list[dict[str, Any]]:
    return fetch_all(
        lambda offset, page_size: repository.supabase.table("team_season_market_stats")
        .select("team_id,scope,sample,hits,percentage,current_streak")
        .eq("league_id", league_id)
        .eq("season", season)
        .eq("category", "corners")
        .eq("market_key", market_key)
        .range(offset, offset + page_size - 1)
    )


def count_market_results(repository: StufRepository, league_id: int, season: int, market_key: str) -> int:
    response = repository._execute(
        lambda: repository.supabase.table("team_match_market_results")
        .select("fixture_id", count="exact")
        .eq("league_id", league_id)
        .eq("season", season)
        .eq("market_key", market_key)
        .limit(1),
        f"count market results league={league_id} season={season} market={market_key}",
    )
    return int(response.count or 0)


def validate_market_definitions(repository: StufRepository) -> tuple[bool, str]:
    expected_keys = by_half_market_keys()
    response = repository._execute(
        lambda: repository.supabase.table("market_definitions")
        .select("key,is_active")
        .in_("key", expected_keys),
        "validate by-half market definitions",
    )
    rows = response.data or []
    active_keys = {row["key"] for row in rows if row.get("is_active") is True}
    missing = sorted(set(expected_keys) - active_keys)
    if missing:
        return False, f"FAIL market_definitions active={len(active_keys)}/{len(expected_keys)} missing={missing[:8]}"
    return True, f"OK market_definitions active={len(active_keys)}/{len(expected_keys)}"


def pct(part: int, total: int) -> float:
    return 0.0 if total <= 0 else part / total


def validate_league(
    repository: StufRepository,
    *,
    league_id: int,
    season: int,
    market_key: str,
    min_coverage: float,
    min_overall_sample: int,
) -> bool:
    ok = True
    fixture_ids = load_final_fixture_ids(repository, league_id, season)
    final_count = len(fixture_ids)
    print(f"\nLeague {league_id} season {season}")
    print(f"  final fixtures: {final_count}")

    if final_count == 0:
        print("  FAIL no final fixtures found")
        return False

    period_counts: dict[str, int] = {}
    for period in ("FT", "1H", "2H"):
        count = len(load_fixture_ids_with_period(repository, fixture_ids, period))
        period_counts[period] = count
        coverage = pct(count, final_count)
        status = "OK" if coverage >= min_coverage else "FAIL"
        ok = ok and status == "OK"
        print(f"  {status} fixture_statistics {period}: {count}/{final_count} ({coverage:.1%})")

    fact_rows = load_fact_rows(repository, league_id, season)
    expected_fact_rows = final_count * 2
    facts_1h = sum(1 for row in fact_rows if row.get("total_corners_1h") is not None)
    facts_2h = sum(1 for row in fact_rows if row.get("total_corners_2h") is not None)
    facts_status = "OK" if len(fact_rows) >= expected_fact_rows and facts_1h >= expected_fact_rows and facts_2h >= expected_fact_rows else "FAIL"
    ok = ok and facts_status == "OK"
    print(
        f"  {facts_status} team_fixture_facts rows={len(fact_rows)}/{expected_fact_rows} "
        f"with_1h={facts_1h}/{expected_fact_rows} with_2h={facts_2h}/{expected_fact_rows}"
    )

    market_stats = load_market_stat_rows(repository, league_id, season, market_key)
    overall_samples = [int(row.get("sample") or 0) for row in market_stats if row.get("scope") == "overall"]
    scope_counts = {
        scope: sum(1 for row in market_stats if row.get("scope") == scope)
        for scope in ("overall", "home", "away")
    }
    min_sample = min(overall_samples) if overall_samples else 0
    max_sample = max(overall_samples) if overall_samples else 0
    low_sample_rows = [
        row
        for row in market_stats
        if row.get("scope") == "overall" and int(row.get("sample") or 0) < min_overall_sample
    ]
    market_status = "OK" if overall_samples else "FAIL"
    ok = ok and market_status == "OK"
    print(
        f"  {market_status} team_season_market_stats {market_key}: "
        f"rows={len(market_stats)} scopes={scope_counts} overall_sample_min={min_sample} overall_sample_max={max_sample}"
    )
    if low_sample_rows:
        preview = [
            f"team={row.get('team_id')} sample={row.get('sample')}"
            for row in low_sample_rows[:8]
        ]
        print(
            f"  WARN low overall sample teams below {min_overall_sample}: "
            f"count={len(low_sample_rows)} preview={preview}"
        )

    result_count = count_market_results(repository, league_id, season, market_key)
    expected_min_results = final_count * 2
    result_status = "OK" if result_count >= expected_min_results else "FAIL"
    ok = ok and result_status == "OK"
    print(f"  {result_status} team_match_market_results {market_key}: rows={result_count} expected_min={expected_min_results}")

    return ok


def main() -> None:
    args = parse_args()
    settings = load_settings()
    repository = StufRepository(create_supabase_client(settings), LOGGER)
    target_leagues = resolve_target_leagues(args, settings, repository, season=args.season)

    definitions_ok, definitions_message = validate_market_definitions(repository)
    print(definitions_message)

    all_ok = definitions_ok
    for league_id in target_leagues:
        all_ok = validate_league(
            repository,
            league_id=league_id,
            season=args.season,
            market_key=args.market_key,
            min_coverage=args.min_coverage,
            min_overall_sample=args.min_overall_sample,
        ) and all_ok

    if not all_ok:
        print("\nFAIL Corners By Half validation did not pass.")
        sys.exit(1)

    print("\nOK Corners By Half validation passed.")


if __name__ == "__main__":
    main()
