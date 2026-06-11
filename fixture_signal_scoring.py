"""Scoring helpers for the Fixture Signals serving layer.

Two responsibilities:

1. A faithful Python port of stuf-web/lib/server/streaks-informativeness.ts.
   The TypeScript module remains the source of truth for the Streaks page;
   this port is used by rebuild_fixture_signals.py so the materialized
   read model ranks team-market signals by the SAME contextual rarity logic.
   If you change one, change the other and keep the constants in sync.

2. Cross-source combination for a fixture card: team-market (primary, scored by
   informativeness), referee context (amplifier, esp. cards/fouls), and player
   props (secondary, weight-capped so they never dominate a fixture).

NOTE: hyperparameters here are provisional and uncalibrated, mirroring the
streaks informativeness rollout. They are named constants so the owner can tune
them without touching control flow. No odds / edge / probability language is
produced anywhere in this module.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

# ── Informativeness config (parity with streaks-informativeness.ts) ───────────
STREAK_INFORMATIVENESS_CONFIG: dict[str, Any] = {
    "min_context_teams": 6,
    "high_baseline": 0.95,
    "low_baseline": 0.05,
    "std_min": 0.03,
    "std_percentile": 0.1,
    "z_cap": 4,
    "strong_z_min": 1.25,
    "watch_z_min": 0.6,
    "reliable_sample_min": 10,
    "max_streak_for_normalization": 20,
    "max_sample_for_reliability": 30,
    "weights": {"z": 3.0, "streak": 1.0, "sample": 0.5},
    "low_information_penalty": 8.0,
}

# ── Fixture-signal cross-source config ────────────────────────────────────────
FIXTURE_SIGNAL_CONFIG: dict[str, Any] = {
    "top_signals_per_fixture": 6,
    # Never surface a team-market signal built on fewer appearances than the P0
    # emerging floor (see stuf-web/lib/server/sample-bands.ts: EMERGING_MIN_SAMPLE).
    "min_team_market_sample": 5,
    # Referee context: amplify matching card/foul team-market signals and emit a
    # single context chip per fixture for the referee's strongest tendency.
    "referee_categories": ("cards", "booking_points", "fouls"),
    "referee_amplify_bonus": 1.5,
    "referee_context_base_strength": 2.0,
    "referee_high_pct": 65.0,
    "referee_min_sample": 8,
    # Player props: scale percentage (0-100) into a strength, then hard-cap so a
    # player prop cannot outrank a strong team-market signal.
    "player_prop_weight": 0.06,
    "player_prop_strength_cap": 5.0,
    "player_prop_min_sample": 8,
    "player_prop_reliable_sample": 10,
    "player_prop_high_pct": 70.0,
    "player_prop_max_per_fixture": 3,
}


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


# ── Team-market tendency scoring (replaces z-score contextual deviation) ───────
# The fixtures Match Intelligence view ranks team-market tendencies by how far
# the hit rate sits from a coin-flip (extremity), weighted by sample reliability.
# It deliberately does NOT score by deviation from a peer "league average":
# that was not decision-relevant, and for national teams the cross-confederation
# baseline (CONMEBOL vs AFC qualifiers, friendlies, etc.) was statistically
# meaningless. No confidence band, no odds, no prediction — just the tendency.
TEAM_MARKET_TENDENCY_BAND = "tendency"


def team_market_tendency_strength(percentage: Any, sample: Any) -> float:
    """Strength for ranking which team-market tendencies to surface per fixture.

    extremity = |hit_rate - 50%| (0..0.5), scaled up by sample reliability so a
    notable rate on many matches outranks the same rate on a thin sample.
    Returns 0 for a coin-flip or unparseable input (it then ranks last).
    """
    try:
        pct = float(percentage)
    except (TypeError, ValueError):
        return 0.0
    frac = pct / 100.0 if pct > 1.0 else pct
    extremity = abs(frac - 0.5)
    try:
        s = float(sample)
    except (TypeError, ValueError):
        s = 0.0
    sample_reliability = clamp(
        s / STREAK_INFORMATIVENESS_CONFIG["max_sample_for_reliability"], 0.0, 1.0
    )
    return extremity * (0.5 + 0.5 * sample_reliability)


def context_key(league_id: Any, season: Any, scope: Any, market_key: Any) -> str:
    return f"{league_id}:{season}:{scope}:{market_key}"


def hit_rate(hits: Any, sample: Any) -> float | None:
    try:
        sample_value = float(sample)
        hits_value = float(hits)
    except (TypeError, ValueError):
        return None
    if sample_value <= 0:
        return None
    return hits_value / sample_value


def _percentile_cont(values: list[float], percentile: float) -> float | None:
    sorted_values = sorted(value for value in values if math.isfinite(value))
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = (len(sorted_values) - 1) * clamp(percentile, 0.0, 1.0)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    weight = position - lower_index
    lower = sorted_values[lower_index]
    upper = sorted_values[upper_index]
    return lower + (upper - lower) * weight


def _population_std(values: list[float], average: float) -> float:
    if not values:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def build_context_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per (league, season, scope, market) average / std / dispersion percentile.

    rows: dicts with league_id, season, scope, market_key, sample, hits.
    """
    rates_by_context: dict[str, list[float]] = {}
    for row in rows:
        rate = hit_rate(row.get("hits"), row.get("sample"))
        if rate is None:
            continue
        key = context_key(row.get("league_id"), row.get("season"), row.get("scope"), row.get("market_key"))
        rates_by_context.setdefault(key, []).append(rate)

    metrics_by_context: dict[str, dict[str, Any]] = {}
    eligible_std_values: list[float] = []
    min_context_teams = STREAK_INFORMATIVENESS_CONFIG["min_context_teams"]

    for key, rates in rates_by_context.items():
        average = sum(rates) / len(rates)
        std = _population_std(rates, average)
        metrics_by_context[key] = {
            "league_market_avg": average,
            "league_market_std": std,
            "context_teams": len(rates),
            "std_low_percentile": None,
        }
        if len(rates) >= min_context_teams:
            eligible_std_values.append(std)

    std_low_percentile = _percentile_cont(
        eligible_std_values, STREAK_INFORMATIVENESS_CONFIG["std_percentile"]
    )
    for metrics in metrics_by_context.values():
        metrics["std_low_percentile"] = std_low_percentile

    return metrics_by_context


def derive_signal_metrics(
    *,
    sample: Any,
    hits: Any,
    streak_length: Any,
    context: dict[str, Any] | None,
    apply_low_information_penalty: bool,
) -> dict[str, Any]:
    """Port of deriveStreakSignalMetrics. Returns the informativeness scoring
    for one team-market row."""
    cfg = STREAK_INFORMATIVENESS_CONFIG
    team_hit_rate = hit_rate(hits, sample)

    context = context or {
        "league_market_avg": None,
        "league_market_std": None,
        "context_teams": 0,
        "std_low_percentile": None,
    }
    league_market_avg = context.get("league_market_avg")
    league_market_std = context.get("league_market_std")
    context_teams = context.get("context_teams") or 0
    std_low_percentile = context.get("std_low_percentile")

    has_stable_context = (
        context_teams >= cfg["min_context_teams"]
        and league_market_avg is not None
        and league_market_std is not None
        and team_hit_rate is not None
    )
    can_compute_z = has_stable_context and league_market_std is not None and league_market_std > 0
    z_score = (
        (team_hit_rate - league_market_avg) / league_market_std
        if can_compute_z
        else None
    )

    is_extreme_baseline = has_stable_context and (
        league_market_avg >= cfg["high_baseline"] or league_market_avg <= cfg["low_baseline"]
    )
    is_low_dispersion = (
        has_stable_context
        and std_low_percentile is not None
        and league_market_std < cfg["std_min"]
        and league_market_std <= std_low_percentile
    )
    is_low_information = has_stable_context and (
        is_extreme_baseline or is_low_dispersion or league_market_std == 0
    )

    positive_z = clamp(z_score if z_score is not None else 0.0, 0.0, cfg["z_cap"])
    try:
        streak_value = float(streak_length)
    except (TypeError, ValueError):
        streak_value = 0.0
    normalized_streak = clamp(streak_value / cfg["max_streak_for_normalization"], 0.0, 1.0)
    sample_numeric = float(sample) if sample is not None else 0.0
    sample_reliability = clamp(sample_numeric / cfg["max_sample_for_reliability"], 0.0, 1.0)

    low_information_penalty = (
        cfg["low_information_penalty"] if (is_low_information and apply_low_information_penalty) else 0.0
    )
    signal_value = (
        cfg["weights"]["z"] * positive_z
        + cfg["weights"]["streak"] * normalized_streak
        + cfg["weights"]["sample"] * sample_reliability
        - low_information_penalty
    )

    signal_band = "neutral"
    z_for_band = z_score if z_score is not None else 0.0
    if is_low_information:
        signal_band = "low_info"
    elif z_for_band >= cfg["strong_z_min"] and sample_numeric >= cfg["reliable_sample_min"]:
        signal_band = "strong"
    elif z_for_band >= cfg["watch_z_min"] or sample_numeric < cfg["reliable_sample_min"]:
        signal_band = "watch"

    return {
        "team_hit_rate": team_hit_rate,
        "league_market_avg": league_market_avg,
        "league_market_std": league_market_std,
        "context_teams": context_teams,
        "z_score": z_score,
        "signal_value": signal_value,
        "is_low_information": is_low_information,
        "signal_band": signal_band,
    }


# ── Fixture-card band normalization ───────────────────────────────────────────
# fixture_signals.signal_band is constrained to strong | watch | context | low_info.
# Informativeness 'neutral' maps to 'watch' on the card (visible, low confidence).
def normalize_team_market_band(informativeness_band: str) -> str:
    if informativeness_band == "strong":
        return "strong"
    if informativeness_band == "low_info":
        return "low_info"
    return "watch"


def player_prop_strength(pct: Any, sample: Any) -> tuple[float, str]:
    cfg = FIXTURE_SIGNAL_CONFIG
    try:
        pct_value = float(pct)
    except (TypeError, ValueError):
        return 0.0, "watch"
    try:
        sample_value = float(sample) if sample is not None else 0.0
    except (TypeError, ValueError):
        sample_value = 0.0

    strength = min(cfg["player_prop_weight"] * pct_value, cfg["player_prop_strength_cap"])
    if sample_value < cfg["player_prop_min_sample"]:
        band = "low_info"
    elif pct_value >= cfg["player_prop_high_pct"] and sample_value >= cfg["player_prop_reliable_sample"]:
        band = "strong"
    else:
        band = "watch"
    return strength, band


def referee_amplifies(category: Any, pct: Any, sample: Any) -> bool:
    cfg = FIXTURE_SIGNAL_CONFIG
    if str(category) not in cfg["referee_categories"]:
        return False
    try:
        return float(pct) >= cfg["referee_high_pct"] and float(sample) >= cfg["referee_min_sample"]
    except (TypeError, ValueError):
        return False
