from __future__ import annotations

import pytest

from src.live_position_residue import classify_live_position_residue
from src.models import Position
from src.utils import utc_now_iso


def _live_position(
    *,
    symbol: str = 'ADA/USDT',
    position_id: str = 'pos-live-ada',
    status: str = 'open',
    initial_position_size_pct: float = 6.0,
    remaining_position_size_pct: float = 6.0,
    entry_quote_amount: float = 5.94,
    entry_base_amount: float = 10.0,
    entry_price: float = 0.594,
) -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status=status,
        entry_time=now,
        entry_price=entry_price,
        entry_signal='LIVE_SUBMIT_FILLED',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='live_fill_reconciled',
        entry_attention_level='high',
        initial_position_size_pct=initial_position_size_pct,
        remaining_position_size_pct=remaining_position_size_pct,
        entry_quote_amount=entry_quote_amount,
        entry_base_amount=entry_base_amount,
        initial_stop_price=entry_price * 0.96,
        active_stop_price=entry_price * 0.96,
        suggested_stop_price=None,
        risk_budget='normal',
        market_state_at_entry='LIVE_RECONCILED',
        tp1_price=entry_price * 1.06,
        tp2_price=entry_price * 1.10,
        highest_price_since_entry=entry_price,
        last_price=entry_price,
        notes=[],
        tags=['live_fill_reconciled', 'truth_domain_live'],
    )


def test_classify_live_position_residue_keeps_full_small_live_position_blocking():
    position = _live_position()

    classification = classify_live_position_residue(position)

    assert classification.is_residue is False
    assert classification.blocking is True
    assert classification.estimated_remaining_quote_amount == pytest.approx(5.94)


def test_classify_live_position_residue_marks_reduced_small_leftover_as_residue():
    position = _live_position(
        status='partially_reduced',
        entry_quote_amount=30.0,
        entry_base_amount=30.0,
        entry_price=1.0,
        initial_position_size_pct=6.0,
        remaining_position_size_pct=1.0,
    )

    classification = classify_live_position_residue(position)

    assert classification.is_residue is True
    assert classification.blocking is False
    assert classification.residue_kind == 'dust_notional_below_tiny_live_threshold'
    assert classification.estimated_remaining_quote_amount == pytest.approx(5.0)
