import argparse
from typing import Any

from pipeline_core import (
    FINAL_STATUSES,
    StufRepository,
    chunked,
    configure_logging,
    create_supabase_client,
    load_settings,
    parse_target_leagues,
    resolve_target_leagues,
)


LOGGER = configure_logging("stuf.repair-cards-facts-from-events")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild team_fixture_facts from already stored fixture_events/fixture_statistics. "
            "Does not call API-Football. Writes only when --apply is passed."
        )
    )
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--leagues", help="CSV league IDs, e.g. 39,61,78,135,140.")
    parser.add_argument("--fixture", type=int, action="append", dest="fixture_ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-statistics-fallback", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Actually write rebuilt team_fixture_facts rows.")
    return parser.parse_args()


def select_all(build_query, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = build_query(offset).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def load_final_fixture_ids(repository: StufRepository, league_id: int, season: int) -> list[int]:
    rows = select_all(
        lambda offset: repository.supabase.table("fixtures")
        .select("id")
        .eq("league_id", league_id)
        .eq("season", season)
        .in_("status_short", sorted(FINAL_STATUSES))
        .order("date", desc=False)
        .range(offset, offset + 999)
    )
    return [int(row["id"]) for row in rows if row.get("id") is not None]


def load_fixtures_with_card_events(repository: StufRepository, fixture_ids: list[int]) -> set[int]:
    fixture_ids_with_cards: set[int] = set()
    for batch in chunked(fixture_ids, 300):
        response = repository._execute(
            lambda batch=batch: repository.supabase.table("fixture_events")
            .select("fixture_id,type")
            .in_("fixture_id", list(batch)),
            f"load card event candidates batch={len(batch)}",
        )
        for row in response.data or []:
            if str(row.get("type") or "").strip().lower() == "card" and row.get("fixture_id") is not None:
                fixture_ids_with_cards.add(int(row["fixture_id"]))
    return fixture_ids_with_cards


def load_fixtures_with_stat_cards(repository: StufRepository, fixture_ids: list[int]) -> set[int]:
    fixture_ids_with_stats: set[int] = set()
    for batch in chunked(fixture_ids, 300):
        response = repository._execute(
            lambda batch=batch: repository.supabase.table("fixture_statistics")
            .select("fixture_id,yellow_cards,red_cards,period")
            .in_("fixture_id", list(batch))
            .eq("period", "FT"),
            f"load statistics card candidates batch={len(batch)}",
        )
        for row in response.data or []:
            if row.get("fixture_id") is None:
                continue
            if row.get("yellow_cards") is not None or row.get("red_cards") is not None:
                fixture_ids_with_stats.add(int(row["fixture_id"]))
    return fixture_ids_with_stats


def resolve_fixture_candidates(
    repository: StufRepository,
    *,
    league_id: int,
    season: int,
    include_statistics_fallback: bool,
) -> list[int]:
    fixture_ids = load_final_fixture_ids(repository, league_id, season)
    event_fixture_ids = load_fixtures_with_card_events(repository, fixture_ids)
    candidate_ids = set(event_fixture_ids)
    if include_statistics_fallback:
        candidate_ids.update(load_fixtures_with_stat_cards(repository, fixture_ids))

    return [fixture_id for fixture_id in fixture_ids if fixture_id in candidate_ids]


def main() -> None:
    args = parse_args()
    settings = load_settings()
    supabase = create_supabase_client(settings)
    repository = StufRepository(supabase, LOGGER)

    if args.fixture_ids:
        candidates_by_scope: dict[str, list[int]] = {"manual": sorted(set(args.fixture_ids))}
    else:
        target_leagues = parse_target_leagues(args.leagues) or resolve_target_leagues(
            args,
            settings,
            repository,
            season=args.season,
        )
        candidates_by_scope = {}
        for league_id in target_leagues:
            candidates = resolve_fixture_candidates(
                repository,
                league_id=league_id,
                season=args.season,
                include_statistics_fallback=args.include_statistics_fallback,
            )
            if args.limit and args.limit > 0:
                candidates = candidates[:args.limit]
            candidates_by_scope[str(league_id)] = candidates
            LOGGER.info(
                "Cards facts candidates league=%s season=%s candidates=%s include_statistics_fallback=%s",
                league_id,
                args.season,
                len(candidates),
                args.include_statistics_fallback,
            )

    all_candidates = [fixture_id for candidates in candidates_by_scope.values() for fixture_id in candidates]
    preview = ", ".join(str(fixture_id) for fixture_id in all_candidates[:20])
    if not args.apply:
        LOGGER.info(
            "Dry-run only: would rebuild team_fixture_facts for %s fixture(s). Preview=%s",
            len(all_candidates),
            preview or "-",
        )
        LOGGER.info("Pass --apply to write rebuilt facts.")
        return

    rebuilt = 0
    failed = 0
    for index, fixture_id in enumerate(all_candidates, start=1):
        try:
            LOGGER.info("Rebuild cards facts fixture=%s (%s/%s)", fixture_id, index, len(all_candidates))
            if repository.replace_team_fixture_facts(fixture_id):
                rebuilt += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            LOGGER.exception("Failed rebuilding cards facts fixture=%s: %s", fixture_id, exc)

    LOGGER.info(
        "Cards facts repair done: candidates=%s rebuilt=%s failed=%s",
        len(all_candidates),
        rebuilt,
        failed,
    )


if __name__ == "__main__":
    main()
