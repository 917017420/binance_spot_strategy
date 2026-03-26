from __future__ import annotations

from src.models import Position
from src.position_manager import evaluate_position
from src.utils import utc_now_iso


def _position(**overrides) -> Position:
    payload = {
        'position_id': 'pos:test',
        'symbol': 'ADA/USDT',
        'status': 'open',
        'entry_time': utc_now_iso(),
        'entry_price': 100.0,
        'entry_signal': 'BUY_READY_BREAKOUT',
        'entry_secondary_signal': None,
        'entry_decision_action': 'BUY_APPROVED',
        'entry_execution_stage': 'live_fill_reconciled',
        'entry_attention_level': 'high',
        'initial_position_size_pct': 5.0,
        'remaining_position_size_pct': 5.0,
        'entry_quote_amount': 500.0,
        'entry_base_amount': 5.0,
        'initial_stop_price': 96.0,
        'active_stop_price': 96.0,
        'suggested_stop_price': 96.0,
        'risk_budget': 'normal',
        'market_state_at_entry': 'NEUTRAL_MIXED',
        'tp1_price': 106.0,
        'tp2_price': 110.0,
        'highest_price_since_entry': 100.0,
        'last_price': 100.0,
        'notes': [],
        'tags': ['truth_domain_live'],
    }
    payload.update(overrides)
    return Position(**payload)


def test_evaluate_position_respects_custom_exit_semantics():
    position = _position(
        tp1_reduce_pct=20.0,
        tp2_reduce_pct=40.0,
        move_stop_to_breakeven_on_tp1=False,
        enable_trailing_on_tp2=False,
    )

    after_tp1 = evaluate_position(position, current_price=106.0, market_state='NEUTRAL_MIXED')

    assert after_tp1.position.remaining_position_size_pct == 4.0
    assert after_tp1.position.active_stop_price == 96.0
    assert after_tp1.state.suggested_action == 'SELL_REDUCE'
    assert 'reduce 20%' in after_tp1.state.reasons[0]

    after_tp2 = evaluate_position(after_tp1.position, current_price=110.0, market_state='NEUTRAL_MIXED')

    assert after_tp2.position.remaining_position_size_pct == 2.0
    assert after_tp2.position.trailing_enabled is False
    assert after_tp2.state.suggested_action == 'ENABLE_TRAILING_STOP'
    assert 'reduce another 40%' in after_tp2.state.reasons[0]


def test_evaluate_position_can_keep_risk_off_as_hold_when_disabled():
    position = _position(risk_off_exit_enabled=False)

    result = evaluate_position(position, current_price=101.0, market_state='RISK_OFF')

    assert result.position.status == 'open'
    assert result.state.suggested_action == 'HOLD'
