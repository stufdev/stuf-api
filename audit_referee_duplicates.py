from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from pipeline_core import (
    build_referee_alias_key,
    choose_preferred_referee_name,
    configure_logging,
    create_supabase_client,
    is_abbreviated_referee_name,
    load_settings,
    normalize_name,
    parse_cli_args,
    resolve_target_leagues,
    strip_country_suffix,
)


LOGGER = configure_logging("stuf.referee_audit")
PAGE_SIZE = 1000


def fetch_all_rows(
    supabase: Any,
    table_name: str,
    columns: str,
    apply_filters: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        query = supabase.table(table_name).select(columns).range(offset, offset + PAGE_SIZE - 1)
        if apply_filters is not None:
            query = apply_filters(query)
        response = query.execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return rows


def choose_canonical_candidate(
    rows: list[dict[str, Any]],
    fixture_counts: Counter[int],
    final_fixture_counts: Counter[int],
    fact_counts: Counter[int],
    market_counts: Counter[int],
) -> dict[str, Any]:
    preferred_name = ""
    for row in rows:
        preferred_name = choose_preferred_referee_name(preferred_name, row.get("name"))

    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        referee_id = int(row["id"])
        name = row.get("name") or ""
        return (
            0 if normalize_name(name) == normalize_name(preferred_name) else 1,
            -fixture_counts[referee_id],
            -final_fixture_counts[referee_id],
            -fact_counts[referee_id],
            -market_counts[referee_id],
            1 if is_abbreviated_referee_name(name) else 0,
            -len(strip_country_suffix(name)),
            referee_id,
        )

    return sorted(rows, key=sort_key)[0]


def main() -> None:
    args = parse_cli_args("Auditoria de arbitros potencialmente duplicados.")
    settings = load_settings()
    supabase = create_supabase_client(settings)
    target_leagues = resolve_target_leagues(args, settings)

    def apply_context_filters(query: Any) -> Any:
        if target_leagues:
            query = query.in_("league_id", list(target_leagues))
        if args.season is not None:
            query = query.eq("season", args.season)
        return query

    LOGGER.info(
        "Auditando arbitros duplicados para leagues=%s season=%s",
        ",".join(str(league_id) for league_id in target_leagues),
        args.season or "ALL",
    )

    referee_rows = fetch_all_rows(supabase, "referees", "id,name,name_normalized,country_name")
    fixture_rows = fetch_all_rows(
        supabase,
        "fixtures",
        "id,league_id,season,status_short,referee_id,referee_name_raw",
        apply_context_filters,
    )
    fact_rows = fetch_all_rows(
        supabase,
        "referee_fixture_facts",
        "fixture_id,referee_id,league_id,season",
        apply_context_filters,
    )
    market_rows = fetch_all_rows(
        supabase,
        "referee_market_stats",
        "referee_id,league_id,season,market_key",
        apply_context_filters,
    )

    fixture_counts: Counter[int] = Counter()
    final_fixture_counts: Counter[int] = Counter()
    raw_name_examples: dict[int, set[str]] = defaultdict(set)
    for row in fixture_rows:
        referee_id = row.get("referee_id")
        if referee_id is None:
            continue
        referee_id = int(referee_id)
        fixture_counts[referee_id] += 1
        if row.get("status_short") in {"FT", "AET", "PEN"}:
            final_fixture_counts[referee_id] += 1
        raw_name = " ".join(str(row.get("referee_name_raw") or "").split())
        if raw_name:
            raw_name_examples[referee_id].add(raw_name)

    fact_counts: Counter[int] = Counter(int(row["referee_id"]) for row in fact_rows if row.get("referee_id") is not None)
    market_counts: Counter[int] = Counter(int(row["referee_id"]) for row in market_rows if row.get("referee_id") is not None)

    alias_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in referee_rows:
        alias_key = build_referee_alias_key(row.get("name") or row.get("name_normalized"))
        if not alias_key:
            continue
        alias_groups[alias_key].append(row)

    suspicious_groups = [
        (alias_key, rows)
        for alias_key, rows in alias_groups.items()
        if len(rows) > 1
    ]
    suspicious_groups.sort(key=lambda item: (-len(item[1]), item[0]))

    if not suspicious_groups:
        LOGGER.info("No se encontraron grupos sospechosos de arbitros duplicados.")
        return

    LOGGER.warning("Se encontraron %s grupos sospechosos de arbitros duplicados.", len(suspicious_groups))

    for index, (alias_key, rows) in enumerate(suspicious_groups, start=1):
        canonical = choose_canonical_candidate(rows, fixture_counts, final_fixture_counts, fact_counts, market_counts)
        canonical_id = int(canonical["id"])
        LOGGER.warning(
            "[%s] alias=%s canonical_id=%s canonical_name=%s",
            index,
            alias_key,
            canonical_id,
            canonical.get("name"),
        )

        ranked_rows = sorted(
            rows,
            key=lambda row: (
                int(row["id"]) != canonical_id,
                -fixture_counts[int(row["id"])],
                -final_fixture_counts[int(row["id"])],
                -fact_counts[int(row["id"])],
                -market_counts[int(row["id"])],
                int(row["id"]),
            ),
        )

        for row in ranked_rows:
            referee_id = int(row["id"])
            warnings: list[str] = []
            if referee_id != canonical_id:
                warnings.append("MERGE_CANDIDATE")
            if fixture_counts[referee_id] == 0 and (fact_counts[referee_id] > 0 or market_counts[referee_id] > 0):
                warnings.append("ORPHAN_AGGREGATES")
            if len(raw_name_examples.get(referee_id, set())) > 1:
                warnings.append("MULTIPLE_RAW_NAMES")

            raw_examples = sorted(raw_name_examples.get(referee_id, set()))
            LOGGER.warning(
                "    - id=%s name=%s country=%s fixtures=%s final_fixtures=%s facts=%s markets=%s raw_names=%s flags=%s",
                referee_id,
                row.get("name"),
                row.get("country_name") or "-",
                fixture_counts[referee_id],
                final_fixture_counts[referee_id],
                fact_counts[referee_id],
                market_counts[referee_id],
                raw_examples[:5] if raw_examples else [],
                warnings or ["OK"],
            )


if __name__ == "__main__":
    main()
