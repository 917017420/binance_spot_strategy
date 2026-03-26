from __future__ import annotations

import re

from src.live_order_payload import build_live_order_payload, build_position_live_order_payload
from src.models import IndicatorSnapshot, PairAnalysis, Position, PositionState, RiskPlan, ScoreBreakdown
from src.utils import utc_now_iso


_BINANCE_CLIENT_ORDER_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,36}$')


def _indicator() -> IndicatorSnapshot:
    return IndicatorSnapshot(
        close=0.2698,
        ema20=0.268,
        ema50=0.265,
        ema200=0.24,
        atr14=0.01,
        atr14_pct=3.0,
        high20=0.28,
        low20=0.22,
        avg_volume20=1000.0,
        volume=1200.0,
        quote_volume_24h=2_000_000.0,
        distance_to_ema20_pct=0.5,
        change_24h_pct=2.0,
        change_7d_pct=4.0,
        upper_wick_pct=5.0,
        body_pct=60.0,
    )


def _score() -> ScoreBreakdown:
    return ScoreBreakdown(
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


def _candidate(symbol: str = 'ADA/USDT', action: str = 'BUY_APPROVED', priority: int = 175) -> PairAnalysis:
    indicator = _indicator()
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        secondary_signal=None,
        decision_action=action,
        execution_stage='IMMEDIATE_ATTENTION',
        attention_level='HIGH',
        decision_priority=priority,
        position_size_pct=25.0,
        day_context_label='NEUTRAL_DAY_STRUCTURE',
        regime='neutral',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=_score(),
        reasons=['test candidate'],
        risk=RiskPlan(invalidation_level=0.24),
    )


def _position(symbol: str = 'ADA/USDT', position_id: str = 'pos:live:live-ADA-USDT-BUY_APPROVED-175') -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status='open',
        entry_time=now,
        entry_price=0.2698,
        entry_signal='BUY_READY_BREAKOUT',
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='IMMEDIATE_ATTENTION',
        entry_attention_level='HIGH',
        initial_position_size_pct=5.0,
        remaining_position_size_pct=5.0,
        entry_quote_amount=6.0,
        entry_base_amount=22.2,
        initial_stop_price=0.25,
        active_stop_price=0.25,
        risk_budget='normal',
        market_state_at_entry='neutral',
        tp1_price=0.28,
        tp2_price=0.29,
        highest_price_since_entry=0.271,
        last_price=0.2698,
    )


def _state(position: Position, suggested_action: str, reasons: list[str]) -> PositionState:
    return PositionState(
        position_id=position.position_id,
        symbol=position.symbol,
        updated_at=utc_now_iso(),
        status=position.status,
        last_price=position.last_price,
        remaining_position_size_pct=position.remaining_position_size_pct,
        active_stop_price=position.active_stop_price,
        tp1_hit=position.tp1_hit,
        tp2_hit=position.tp2_hit,
        trailing_enabled=position.trailing_enabled,
        highest_price_since_entry=position.highest_price_since_entry,
        suggested_action=suggested_action,
        reasons=reasons,
    )


def test_build_live_order_payload_preserves_existing_buy_client_order_id_when_legal():
    payload = build_live_order_payload(_candidate(), total_equity_quote=100.0)

    assert payload.client_order_id == 'live-ADA-USDT-BUY_APPROVED-175'
    assert _BINANCE_CLIENT_ORDER_ID_RE.fullmatch(payload.client_order_id)


def test_build_position_live_order_payload_generates_short_legal_stable_sell_exit_client_order_id():
    position = _position()
    state = _state(position, 'SELL_EXIT', ['Current price fell below active stop'])

    first = build_position_live_order_payload(position, state, requested_reduce_pct=0.0)
    second = build_position_live_order_payload(position, state, requested_reduce_pct=0.0)

    assert first.client_order_id == second.client_order_id
    assert first.client_order_id.startswith('live-ADA-USDT-SX-')
    assert len(first.client_order_id) <= 36
    assert _BINANCE_CLIENT_ORDER_ID_RE.fullmatch(first.client_order_id)


def test_build_position_live_order_payload_generates_short_legal_sell_reduce_client_order_id():
    position = _position(
        symbol='1000SHIB/USDT',
        position_id='pos:live:live-1000SHIB-USDT-BUY_APPROVED-123456789',
    ).model_copy(update={'status': 'partially_reduced', 'tp1_hit': True, 'remaining_position_size_pct': 3.5})
    state = _state(position, 'ENABLE_TRAILING_STOP', ['Trailing activation'])

    payload = build_position_live_order_payload(position, state, requested_reduce_pct=30.0)

    assert payload.metadata['action_intent'] == 'SELL_REDUCE'
    assert payload.client_order_id.startswith('live-1000SHIB-USDT-SR-')
    assert len(payload.client_order_id) <= 36
    assert _BINANCE_CLIENT_ORDER_ID_RE.fullmatch(payload.client_order_id)
