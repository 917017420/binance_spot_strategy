from __future__ import annotations

from types import SimpleNamespace

from src.config import AutoEntrySettings, Settings
from src.live_submit_executor import build_live_submit_plan
from src.models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown


def _candidate(symbol: str = 'SOL/USDT') -> PairAnalysis:
    indicator = IndicatorSnapshot(
        close=150.0,
        ema20=149.0,
        ema50=148.0,
        ema200=140.0,
        atr14=2.0,
        atr14_pct=1.3,
        high20=155.0,
        low20=130.0,
        avg_volume20=1000.0,
        volume=1200.0,
        quote_volume_24h=2_000_000.0,
        distance_to_ema20_pct=0.6,
        change_24h_pct=2.1,
        change_7d_pct=5.4,
        upper_wick_pct=8.0,
        body_pct=60.0,
    )
    score = ScoreBreakdown(
        trend_score=20,
        liquidity_score=18,
        strength_score=14,
        breakout_score=11,
        overextension_penalty=0,
        regime_score=6,
        total_score=69,
        passed_candidate_gate=True,
        strong_candidate=False,
        reasons=['test candidate'],
    )
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        secondary_signal=None,
        decision_action='BUY_APPROVED',
        execution_stage='IMMEDIATE_ATTENTION',
        attention_level='HIGH',
        decision_priority=169,
        position_size_pct=25.0,
        day_context_label='NEUTRAL_DAY_STRUCTURE',
        regime='neutral',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=score,
        reasons=['test candidate'],
        risk=RiskPlan(invalidation_level=140.0),
    )


def test_build_live_submit_plan_uses_configured_live_quote_amount(monkeypatch):
    captured = {}

    def _fake_submit_live_order(settings, payload):
        captured['payload'] = payload
        return SimpleNamespace(
            status='adapter_stubbed',
            message='stubbed',
            details={
                'request': {'symbol': payload.symbol},
                'exchange_params': {},
                'response': {'status': 'pending_real_submit'},
                'submit_contract': {'adapter_call_stage': 'adapter_stubbed'},
            },
        )

    monkeypatch.setattr('src.live_submit_executor.submit_live_order', _fake_submit_live_order)

    settings = Settings(auto_entry=AutoEntrySettings(live_order_quote_amount=6.4))
    result = build_live_submit_plan(_candidate(), total_equity_quote=1000.0, settings=settings)

    assert captured['payload'].quote_amount == 6.4
    assert captured['payload'].metadata['quote_amount_source'] == 'explicit_quote_amount'
    assert result.details['configured_live_order_quote_amount'] == 6.4
    assert result.details['quote_amount'] == 6.4
