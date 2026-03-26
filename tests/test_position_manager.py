from __future__ import annotations

import pytest

from src.config import ExitSettings
from src.models import Position
from src.models import ExecutionResult, PendingConfirmation
from src.position_manager import evaluate_position
from src.position_exit_policy import plan_entry_exit_levels
from src.position_initializer import build_position_from_execution
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


def test_plan_entry_exit_levels_uses_structure_atr_and_resistance():
    exit_settings = ExitSettings(
        initial_stop_loss_pct=4.0,
        tp1_profit_pct=6.0,
        tp2_profit_pct=10.0,
        initial_stop_atr_multiple=1.0,
        stop_structure_buffer_atr=0.0,
        tp1_atr_multiple=2.0,
        tp2_atr_multiple=8.0,
        tp1_runway_fraction=0.5,
        resistance_buffer_pct=0.0,
    )
    plan = plan_entry_exit_levels(
        100.0,
        exit_settings=exit_settings,
        suggested_stop_price=95.0,
        atr14=1.5,
        structure_support_price=99.0,
        local_resistance_price=104.0,
    )

    assert plan.initial_stop_price == pytest.approx(99.0)
    assert plan.tp2_price == pytest.approx(104.0)
    assert 100.0 < plan.tp1_price < plan.tp2_price
    assert plan.reward_risk_ratio > 0
    assert any('resistance' in note for note in plan.notes)


def test_build_position_from_execution_prefers_planned_exit_levels_in_confirmation_meta():
    confirmation = PendingConfirmation(
        confirmation_id='c-1',
        created_at=utc_now_iso(),
        expires_at=utc_now_iso(),
        symbol='ADA/USDT',
        requested_position_size_pct=5.0,
        trigger_price=100.0,
        suggested_stop_price=95.0,
        trigger_reason='test',
        decision_action='BUY_APPROVED',
        execution_stage='MANUAL_CONFIRMATION',
        attention_level='HIGH',
        market_state='NEUTRAL_MIXED',
        risk_budget='normal',
        signal='BUY_READY_BREAKOUT',
        meta={
            'planned_initial_stop_price': 97.0,
            'planned_tp1_price': 103.0,
            'planned_tp2_price': 107.0,
            'atr14_at_signal': 1.8,
            'runway_resistance_price': 108.0,
        },
    )
    execution = ExecutionResult(
        confirmation_id='c-1',
        mode='paper',
        status='paper_submitted',
        symbol='ADA/USDT',
        side='buy',
        requested_position_size_pct=5.0,
        reference_price=100.0,
        estimated_quote_amount=50.0,
        estimated_base_amount=0.5,
        message='ok',
        created_at=utc_now_iso(),
    )

    position = build_position_from_execution(confirmation, execution)

    assert position.initial_stop_price == pytest.approx(97.0)
    assert position.tp1_price == pytest.approx(103.0)
    assert position.tp2_price == pytest.approx(107.0)
