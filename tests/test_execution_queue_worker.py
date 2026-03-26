from __future__ import annotations

from types import SimpleNamespace

from src.config import AutoEntrySettings, Settings
from src.execution_candidate_queue import append_execution_candidate
from src.execution_queue_worker import process_execution_queue
from src.models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown


def _candidate(symbol: str = 'SOL/USDT') -> PairAnalysis:
    indicator = IndicatorSnapshot(
        close=24.5,
        ema20=24.0,
        ema50=23.5,
        ema200=22.0,
        atr14=0.5,
        atr14_pct=2.0,
        high20=26.0,
        low20=20.0,
        avg_volume20=1000.0,
        volume=1400.0,
        quote_volume_24h=4_000_000.0,
        distance_to_ema20_pct=2.1,
        change_24h_pct=3.2,
        change_7d_pct=7.7,
        upper_wick_pct=9.0,
        body_pct=55.0,
    )
    score = ScoreBreakdown(
        trend_score=22,
        liquidity_score=20,
        strength_score=13,
        breakout_score=10,
        overextension_penalty=0,
        regime_score=6,
        total_score=71,
        passed_candidate_gate=True,
        strong_candidate=False,
        reasons=['queue test'],
    )
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        secondary_signal=None,
        decision_action='BUY_APPROVED',
        execution_stage='IMMEDIATE_ATTENTION',
        attention_level='HIGH',
        decision_priority=171,
        position_size_pct=9.0,
        day_context_label='NEUTRAL_DAY_STRUCTURE',
        regime='neutral',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=score,
        reasons=['queue test'],
        risk=RiskPlan(invalidation_level=22.0),
    )


def test_process_execution_queue_reuses_candidate_snapshot_and_config(monkeypatch, tmp_path):
    candidate = _candidate()
    append_execution_candidate(
        {
            'symbol': candidate.symbol,
            'route': 'live',
            'route_status': 'armed',
            'candidate_snapshot': candidate.model_dump(mode='json'),
        },
        base_dir=tmp_path,
    )

    captured = {}

    def _fake_build_live_submit_plan(candidate_arg, total_equity_quote, settings, debug_contract=None):
        captured['candidate'] = candidate_arg
        captured['total_equity_quote'] = total_equity_quote
        captured['live_order_quote_amount'] = settings.auto_entry.live_order_quote_amount
        return SimpleNamespace(
            details={
                'adapter_details': {},
                'exchange_submit_response': {'status': 'pending_real_submit'},
                'exchange_submit_contract': {'adapter_call_stage': 'adapter_stubbed'},
                'exchange_submit_error': None,
                'exchange_submit_debug_contract': None,
                'plan_path': str(tmp_path / 'plan.json'),
                'client_order_id': 'cid-queue',
            }
        )

    monkeypatch.setattr('src.execution_queue_worker.build_live_submit_plan', _fake_build_live_submit_plan)

    settings = Settings(auto_entry=AutoEntrySettings(scan_reference_equity_quote=1234.0, live_order_quote_amount=6.2))
    result = process_execution_queue(base_dir=tmp_path, settings=settings)

    assert result.processed == 1
    assert captured['candidate'].symbol == 'SOL/USDT'
    assert captured['candidate'].indicators_1h.close == 24.5
    assert captured['candidate'].position_size_pct == 9.0
    assert captured['total_equity_quote'] == 1234.0
    assert captured['live_order_quote_amount'] == 6.2
