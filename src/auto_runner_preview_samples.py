from __future__ import annotations

from .models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown



def _indicator(close: float) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        close=close,
        ema20=close * 0.98,
        ema50=close * 0.96,
        ema200=close * 0.94,
        high20=close * 1.04,
        low20=close * 0.9,
        atr14=close * 0.02,
        atr14_pct=2.0,
        rsi14=62.0,
        volume=1200.0,
        avg_volume20=900.0,
        quote_volume_24h=25_000_000.0,
        body_pct=55.0,
        upper_wick_pct=9.0,
        lower_wick_pct=7.0,
        distance_to_ema20_pct=2.0,
        change_24h_pct=4.5,
        change_7d_pct=10.0,
    )



def _score(total: int) -> ScoreBreakdown:
    return ScoreBreakdown(
        trend_score=20,
        liquidity_score=18,
        strength_score=15,
        breakout_score=12,
        overextension_penalty=0,
        regime_score=7,
        total_score=total,
        passed_candidate_gate=True,
        strong_candidate=total >= 80,
        reasons=['preview sample'],
    )



def _candidate(symbol: str, *, priority: int, regime: str, day_context_label: str, position_size_pct: float = 5.0) -> PairAnalysis:
    indicator = _indicator(108.0 if symbol == 'TRX/USDT' else 87.0 if symbol == 'SOL/USDT' else 52.0)
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        decision_action='BUY_APPROVED' if symbol != 'XRP/USDT' else 'WATCHLIST_ONLY',
        execution_stage='IMMEDIATE_ATTENTION' if symbol != 'XRP/USDT' else 'MONITOR_ONLY',
        attention_level='HIGH' if symbol == 'TRX/USDT' else 'MEDIUM',
        decision_priority=priority,
        position_size_pct=position_size_pct,
        day_context_label=day_context_label,
        regime=regime,
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=_score(priority),
        reasons=['preview sample'],
        risk=RiskPlan(invalidation_level=indicator.close * 0.95),
    )



def build_preview_sample_candidates(regime: str = 'neutral', debug_contract: dict | None = None) -> list[PairAnalysis]:
    if regime == 'risk_off':
        return []

    trx = _candidate('TRX/USDT', priority=168 if regime == 'risk_on' else 159, regime=regime, day_context_label='TRENDING_HEALTHY')
    sol = _candidate('SOL/USDT', priority=82, regime=regime, day_context_label='ROTATION_SETUP')
    xrp = _candidate('XRP/USDT', priority=0, regime=regime, day_context_label='RISKY_BREAKOUT')
    xrp.decision_action = 'DENY'
    xrp.execution_stage = 'SKIP'
    xrp.attention_level = 'LOW'
    return [trx, sol, xrp]
