from __future__ import annotations

from .auto_entry_gate import AutoEntryConfig
from .day_context import enrich_day_context
from .decision_engine import decide_action
from .exchange import fetch_ohlcv_dataframe
from .indicators import add_indicators, latest_snapshot, require_indicator_history
from .models import IndicatorSnapshot, PairAnalysis
from .risk import build_risk_plan
from .scorer import score_candidate
from .signals import determine_signal


def scan_symbol_analysis(exchange, symbol: str, quote_volume_24h: float, settings, regime: str) -> PairAnalysis:
    primary_frame = fetch_ohlcv_dataframe(exchange, symbol, timeframe=settings.data.primary_timeframe, limit=settings.data.ohlcv_limit)
    context_frame = fetch_ohlcv_dataframe(exchange, symbol, timeframe=settings.data.context_timeframe, limit=settings.data.ohlcv_limit)
    enriched_primary = add_indicators(primary_frame)
    enriched_context = add_indicators(context_frame)
    require_indicator_history(enriched_primary)
    require_indicator_history(enriched_context)
    indicators_1h = IndicatorSnapshot(**latest_snapshot(enriched_primary, quote_volume_24h))
    indicators_4h = IndicatorSnapshot(**latest_snapshot(enriched_context, quote_volume_24h))
    scores = score_candidate(symbol, indicators_1h, indicators_4h, settings.runtime_btc_indicators_1h, regime, settings)
    signal, secondary_signal, signal_reasons = determine_signal(enriched_primary, indicators_1h, scores, regime, settings)
    risk = build_risk_plan(indicators_1h)
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
