from __future__ import annotations

from src.models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown
from src.portfolio_risk import build_portfolio_risk_snapshot
from src.positions_store import save_positions
from src.models import Position
from src.utils import utc_now_iso


def _candidate(symbol: str = 'SOL/USDT') -> PairAnalysis:
    indicator = IndicatorSnapshot(
        close=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=97.0,
        atr14=2.0,
        atr14_pct=2.0,
        high20=103.0,
        low20=90.0,
        avg_volume20=1000.0,
        volume=1200.0,
        quote_volume_24h=2_000_000.0,
        distance_to_ema20_pct=1.0,
        change_24h_pct=3.0,
        change_7d_pct=5.0,
        upper_wick_pct=10.0,
        body_pct=55.0,
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
        reasons=['test'],
    )
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        secondary_signal=None,
        decision_action='BUY_APPROVED',
        execution_stage='IMMEDIATE_ATTENTION',
        attention_level='HIGH',
        decision_priority=160,
        position_size_pct=5.0,
        regime='neutral',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=score,
        reasons=['test'],
        risk=RiskPlan(invalidation_level=95.0),
    )


def _position(symbol: str, position_id: str, *, tags: list[str]) -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status='open',
        entry_time=now,
        entry_price=100.0,
        entry_signal='BUY_READY_BREAKOUT',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='live_fill_reconciled' if 'live_fill_reconciled' in tags else 'manual_confirmation',
        entry_attention_level='high',
        initial_position_size_pct=5.0,
        remaining_position_size_pct=5.0,
        entry_quote_amount=50.0,
        entry_base_amount=0.5,
        initial_stop_price=95.0,
        active_stop_price=95.0,
        suggested_stop_price=95.0,
        risk_budget='normal',
        market_state_at_entry='NEUTRAL',
        tp1_price=106.0,
        tp2_price=110.0,
        highest_price_since_entry=100.0,
        last_price=100.0,
        notes=[],
        tags=tags,
    )


def test_portfolio_risk_snapshot_ignores_simulated_positions(tmp_path, monkeypatch):
    save_positions(
        [
            _position('ETH/USDT', 'pos-sim', tags=['manual_confirmed', 'dry_run', 'position_initialized']),
            _position('BTC/USDT', 'pos-live', tags=['live_fill_reconciled']),
        ],
        base_dir=tmp_path,
    )
    monkeypatch.setattr('src.portfolio_risk.load_live_active_positions', lambda: [
        _position('BTC/USDT', 'pos-live', tags=['live_fill_reconciled'])
    ])

    snapshot = build_portfolio_risk_snapshot(_candidate(), bucket_weight_overrides={})

    assert snapshot['open_positions'] == 1
    assert snapshot['same_symbol_open'] is False
