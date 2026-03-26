from __future__ import annotations

import math

import pandas as pd

from .auto_entry_gate import AutoEntryConfig
from .day_context import enrich_day_context
from .decision_engine import decide_action
from .exchange import fetch_ohlcv_dataframe
from .indicators import add_indicators, latest_snapshot, require_indicator_history
from .models import IndicatorSnapshot, PairAnalysis
from .position_exit_policy import plan_entry_exit_levels
from .risk import build_risk_plan
from .scorer import score_candidate
from .signals import determine_signal


def _extract_swing_highs(series: pd.Series, *, left_span: int, right_span: int) -> list[float]:
    values = [float(value) for value in series.dropna().tolist() if math.isfinite(float(value))]
    min_window = left_span + right_span + 1
    if len(values) < min_window:
        return []
    swings: list[float] = []
    for index in range(left_span, len(values) - right_span):
        pivot = values[index]
        left_max = max(values[index - left_span:index], default=pivot)
        right_max = max(values[index + 1:index + 1 + right_span], default=pivot)
        if pivot >= left_max and pivot >= right_max:
            swings.append(pivot)
    return swings


def _pick_resistance_level(overhead_candidates: list[float], *, current_price: float, zone_width: float) -> tuple[float | None, str]:
    if not overhead_candidates:
        return None, "none"

    ordered_levels = sorted(float(value) for value in overhead_candidates if math.isfinite(float(value)))
    if not ordered_levels:
        return None, "none"

    best_cluster_level: float | None = None
    best_cluster_touches = 0
    for level in ordered_levels:
        touches = sum(1 for candidate in ordered_levels if abs(candidate - level) <= zone_width)
        if touches < 2:
            continue
        if touches > best_cluster_touches:
            best_cluster_touches = touches
            best_cluster_level = level
            continue
        if touches == best_cluster_touches and best_cluster_level is not None:
            if level < best_cluster_level and level > current_price:
                best_cluster_level = level

    if best_cluster_level is not None:
        return float(best_cluster_level), "swing_cluster"

    nearest_level = min(ordered_levels, key=lambda level: abs(level - current_price))
    return float(nearest_level), "nearest_swing"


def _estimate_runway_profile(
    enriched_primary: pd.DataFrame,
    enriched_context: pd.DataFrame,
    indicators_1h: IndicatorSnapshot,
    indicators_4h: IndicatorSnapshot,
    settings,
) -> dict[str, float | bool | str | None]:
    current_price = float(indicators_1h.close)
    lookback = max(int(settings.strategy.runway_lookback_bars or 0), 24)
    primary_highs = enriched_primary.iloc[:-1]["high"].tail(lookback) if len(enriched_primary) > 1 else pd.Series(dtype=float)
    context_highs = enriched_context.iloc[:-1]["high"].tail(max(lookback // 4, 24)) if len(enriched_context) > 1 else pd.Series(dtype=float)
    primary_swing_highs = _extract_swing_highs(primary_highs, left_span=2, right_span=2)
    context_swing_highs = _extract_swing_highs(context_highs, left_span=1, right_span=1)

    local_high_candidates: list[float] = [
        float(indicators_1h.high20),
        float(indicators_4h.high20),
    ]
    local_high_candidates.extend(primary_swing_highs[-40:])
    local_high_candidates.extend(context_swing_highs[-20:])

    structural_fallback_candidates: list[float] = list(local_high_candidates)
    for series in (primary_highs.tail(8), context_highs.tail(8)):
        if series.empty:
            continue
        structural_fallback_candidates.extend(float(value) for value in series.dropna().tolist())
    structural_fallback_candidates = [value for value in structural_fallback_candidates if math.isfinite(value) and value > 0]

    resistance_candidates: list[float] = []
    min_clearance_price = current_price * 1.001
    for value in local_high_candidates:
        if math.isfinite(value) and value > min_clearance_price:
            resistance_candidates.append(float(value))
    resistance_zone_width = max(float(indicators_1h.atr14) * 0.35, current_price * 0.0025, 1e-6)
    runway_resistance_price, runway_source = _pick_resistance_level(
        resistance_candidates,
        current_price=current_price,
        zone_width=resistance_zone_width,
    )
    if runway_resistance_price is None:
        runway_resistance_price = current_price + (float(indicators_1h.atr14) * max(float(settings.exit.tp2_atr_multiple), 0.0))
        runway_source = "atr_projection"
    local_high_reference = runway_resistance_price if runway_resistance_price else (
        max(structural_fallback_candidates) if structural_fallback_candidates else current_price
    )

    runway_upside_pct = max(((runway_resistance_price / max(current_price, 1e-9)) - 1.0) * 100.0, 0.0)
    distance_to_local_high_pct = (
        max(((local_high_reference / max(current_price, 1e-9)) - 1.0) * 100.0, 0.0) if local_high_reference > 0 else 0.0
    )
    near_local_high = (
        local_high_reference >= current_price
        and distance_to_local_high_pct <= max(float(settings.strategy.runway_near_high_threshold_pct), 0.0)
    )

    return {
        "runway_upside_pct": float(runway_upside_pct),
        "runway_resistance_price": float(runway_resistance_price),
        "local_high_reference_price": float(local_high_reference),
        "distance_to_local_high_pct": float(distance_to_local_high_pct),
        "near_local_high": bool(near_local_high),
        "runway_source": runway_source,
    }


def scan_symbol_analysis(exchange, symbol: str, quote_volume_24h: float, settings, regime: str) -> PairAnalysis:
    primary_frame = fetch_ohlcv_dataframe(exchange, symbol, timeframe=settings.data.primary_timeframe, limit=settings.data.ohlcv_limit)
    context_frame = fetch_ohlcv_dataframe(exchange, symbol, timeframe=settings.data.context_timeframe, limit=settings.data.ohlcv_limit)
    enriched_primary = add_indicators(primary_frame)
    enriched_context = add_indicators(context_frame)
    require_indicator_history(enriched_primary)
    require_indicator_history(enriched_context)
    indicators_1h = IndicatorSnapshot(**latest_snapshot(enriched_primary, quote_volume_24h))
    indicators_4h = IndicatorSnapshot(**latest_snapshot(enriched_context, quote_volume_24h))
    runway_profile = _estimate_runway_profile(enriched_primary, enriched_context, indicators_1h, indicators_4h, settings)
    scores = score_candidate(
        symbol,
        indicators_1h,
        indicators_4h,
        settings.runtime_btc_indicators_1h,
        regime,
        settings,
        runway_upside_pct=float(runway_profile["runway_upside_pct"] or 0.0),
        distance_to_local_high_pct=float(runway_profile["distance_to_local_high_pct"] or 0.0),
        near_local_high=bool(runway_profile["near_local_high"]),
    )
    signal, secondary_signal, signal_reasons = determine_signal(
        enriched_primary,
        indicators_1h,
        scores,
        regime,
        settings,
        runway_upside_pct=float(runway_profile["runway_upside_pct"] or 0.0),
        near_local_high=bool(runway_profile["near_local_high"]),
    )
    risk = build_risk_plan(
        indicators_1h,
        indicators_4h,
        local_resistance_price=float(runway_profile["runway_resistance_price"] or 0.0),
        runway_upside_pct=float(runway_profile["runway_upside_pct"] or 0.0),
    )
    exit_plan = plan_entry_exit_levels(
        indicators_1h.close,
        exit_settings=settings.exit,
        suggested_stop_price=risk.invalidation_level,
        atr14=indicators_1h.atr14,
        structure_support_price=min(indicators_1h.low20, indicators_1h.ema50),
        local_resistance_price=float(runway_profile["runway_resistance_price"] or 0.0),
    )
    market = exchange.market(symbol) if hasattr(exchange, 'market') else {}
    limits = market.get('limits') or {}
    precision = market.get('precision') or {}
    min_amount = (((limits.get('amount') or {}).get('min')) if isinstance(limits.get('amount'), dict) else None) or 0.0
    amount_step = precision.get('amount') or 0.0
    tiny_live_quote = float(settings.auto_entry.scan_tiny_live_notional_quote)
    tiny_live_base = (tiny_live_quote / indicators_1h.close) if indicators_1h.close > 0 else 0.0
    analysis = PairAnalysis(
        symbol=symbol,
        signal=signal,
        secondary_signal=secondary_signal,
        regime=regime,
        indicators_1h=indicators_1h,
        indicators_4h=indicators_4h,
        scores=scores,
        reasons=signal_reasons,
        risk=risk,
        runway_upside_pct=float(runway_profile["runway_upside_pct"] or 0.0),
        runway_resistance_price=float(runway_profile["runway_resistance_price"] or 0.0),
        local_high_reference_price=float(runway_profile["local_high_reference_price"] or 0.0),
        distance_to_local_high_pct=float(runway_profile["distance_to_local_high_pct"] or 0.0),
        near_local_high=bool(runway_profile["near_local_high"]),
        expected_upside_pct=exit_plan.expected_upside_pct,
        expected_downside_pct=exit_plan.expected_downside_pct,
        reward_risk_ratio=exit_plan.reward_risk_ratio,
        planned_initial_stop_price=exit_plan.initial_stop_price,
        planned_tp1_price=exit_plan.tp1_price,
        planned_tp2_price=exit_plan.tp2_price,
        exit_plan_notes=[
            *exit_plan.notes,
            f"Runway source: {runway_profile['runway_source']}.",
        ],
        execution_tiny_live_quote_amount=tiny_live_quote,
        execution_tiny_live_base_amount=tiny_live_base,
        execution_tiny_live_min_amount_ok=(tiny_live_base >= float(min_amount or 0.0)) if min_amount else True,
        execution_market_min_amount=float(min_amount or 0.0),
        execution_market_amount_step=float(amount_step or 0.0),
    )
    analysis = enrich_day_context(
        analysis,
        btc_change_24h_pct=settings.runtime_btc_indicators_1h.change_24h_pct if settings.runtime_btc_indicators_1h else 0.0,
    )
    return decide_action(analysis, settings)


def build_auto_entry_config(settings) -> AutoEntryConfig:
    return AutoEntryConfig(
        live_min_score=settings.auto_entry.live_min_score,
        paper_min_score=settings.auto_entry.paper_min_score,
        shadow_min_score=settings.auto_entry.shadow_min_score,
        shadow_soft_risk_points=settings.auto_entry.shadow_soft_risk_points,
        max_open_positions=settings.auto_entry.max_open_positions,
        max_single_position_pct=settings.auto_entry.max_single_position_pct,
        max_total_exposure_pct=settings.auto_entry.max_total_exposure_pct,
        allow_watchlist_alert_to_paper=settings.auto_entry.allow_watchlist_alert_to_paper,
        allow_manual_confirmation_to_paper=settings.auto_entry.allow_manual_confirmation_to_paper,
        deny_when_market_risk_off=settings.auto_entry.deny_when_market_risk_off,
        bucket_weight_overrides=settings.auto_entry.bucket_weight_overrides,
        trending_bonus=settings.day_context.trending_bonus,
        overheated_penalty=settings.day_context.overheated_penalty,
        weak_rebound_penalty=settings.day_context.weak_rebound_penalty,
    )


def apply_ranked_candidate_handoff(
    priority_candidates: list[PairAnalysis],
    secondary_candidates: list[PairAnalysis] | None = None,
) -> tuple[list[PairAnalysis], list[PairAnalysis]]:
    for index, candidate in enumerate(priority_candidates):
        candidate.execution_stage = 'IMMEDIATE_ATTENTION' if index == 0 else 'MANUAL_CONFIRMATION'
        candidate.attention_level = 'HIGH'

    secondary = secondary_candidates or []
    for candidate in secondary:
        candidate.execution_stage = 'MONITOR_ONLY'
        candidate.attention_level = 'MEDIUM'

    return priority_candidates, secondary
