from __future__ import annotations

from typing import Any


def _prop(
    key: str,
    category: str,
    label: str,
    metric: str,
    operator: str,
    line: float | None,
    display_order: int,
    family: str | None = None,
    period: str = "FT",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "category": category,
        "label": label,
        "metric": metric,
        "operator": operator,
        "line": line,
        "period": period,
        "display_order": display_order,
        "is_active": True,
    }
    if family is not None:
        row["family"] = family
    return row


def _line_key(line: float) -> str:
    return str(line).replace(".", "_")


def _over_props(
    category: str,
    metric: str,
    lines: tuple[float, ...],
    label_noun: str,
    start_order: int,
    family: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        lk = _line_key(line)
        rows.append(_prop(
            f"PLAYER_OVER_{lk}_{metric.upper()}",
            category,
            f"Over {line} {label_noun}",
            metric,
            "over",
            line,
            start_order + i,
            family=family,
        ))
    return rows


PLAYER_PROP_DEFINITIONS: tuple[dict[str, Any], ...] = (
    # ── Attacking ───────────────────────────────────────────────────────────
    _prop("PLAYER_SCORED", "attacking", "Scored", "goals", "equals", 1.0, 10, family="attacking"),
    _prop("PLAYER_ASSISTED", "attacking", "Assisted", "assists", "equals", 1.0, 20, family="attacking"),
    _prop("PLAYER_GOAL_INVOLVEMENT", "attacking", "Goal Involvement", "goal_involvement", "custom", None, 30, family="attacking"),
    _prop("PLAYER_OVER_0_5_OFFSIDES", "attacking", "Over 0.5 Offsides", "offsides", "over", 0.5, 40, family="attacking"),

    # ── Shots on target ─────────────────────────────────────────────────────
    *_over_props("shots", "shots_on_target", (0.5, 1.5, 2.5), "Shots on Target", 100, family="shots_on_target"),

    # ── Total shots ─────────────────────────────────────────────────────────
    *_over_props("shots", "total_shots", (0.5, 1.5, 2.5, 3.5), "Total Shots", 200, family="total_shots"),

    # ── Cards ────────────────────────────────────────────────────────────────
    _prop("PLAYER_CARDED", "cards", "Carded", "carded", "equals", 1.0, 300, family="cards"),

    # ── Fouls committed ──────────────────────────────────────────────────────
    *_over_props("fouls", "fouls_committed", (0.5, 1.5, 2.5, 3.5), "Fouls Committed", 400, family="fouls_committed"),

    # ── Tackles ──────────────────────────────────────────────────────────────
    *_over_props("tackles", "tackles", (0.5, 1.5, 2.5, 3.5), "Tackles", 500, family="tackles"),

    # ── Fouls drawn (won/received) ───────────────────────────────────────────
    *_over_props("fouled", "fouls_drawn", (0.5, 1.5, 2.5), "Fouls Won/Drawn", 600, family="fouls_drawn"),
)


# Metric → player_fixture_stats column mapping (for builder source-of-truth).
# All metrics use player_fixture_stats as primary source.
# NULL rule: only include rows where minutes IS NOT NULL AND minutes > 0.
# Stats are NOT NULL DEFAULT 0 — zero is a real value, not missing, once minutes > 0.
METRIC_COLUMN_MAP: dict[str, str] = {
    "goals": "goals",
    "assists": "assists",
    "goal_involvement": "_computed",   # goals + assists (computed in builder, not a column)
    "shots_on_target": "shots_on_target",
    "total_shots": "total_shots",
    "carded": "_computed",             # yellow_cards + red_cards >= 1 (computed in builder)
    "fouls_committed": "fouls_committed",
    "fouls_drawn": "fouls_drawn",
    "tackles": "tackles",
    "offsides": "offsides",
}

# Prop operators and how to evaluate them against a numeric_value.
# "over":   numeric_value > line
# "equals": numeric_value >= line  (scored = goals >= 1, carded = card_count >= 1)
# "custom": computed per-metric (goal_involvement: goals+assists >= 1)
PROP_OPERATOR_NOTE = """
Prop result evaluation:
  over line:   numeric_value > line
  equals line: numeric_value >= line   (e.g. Scored = goals >= 1)
  custom:      goal_involvement = goals + assists >= 1
               carded           = yellow_cards + red_cards >= 1
"""


def ensure_player_prop_definitions(repository) -> None:  # type: ignore[no-untyped-def]
    """Seed player_prop_definitions table. Upserts; does not deactivate existing keys."""
    repository.upsert_player_prop_definitions(list(PLAYER_PROP_DEFINITIONS))
