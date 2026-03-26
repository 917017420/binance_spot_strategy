from __future__ import annotations

from src.models import Position
from src.live_inflight_state import save_live_inflight_state
from src.positions_store import save_positions
from src.single_active_trade_state import build_single_active_trade_state
from src.utils import utc_now_iso


def _active_position(symbol: str, position_id: str, *, tags: list[str], entry_execution_stage: str = 'armed') -> Position:
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
        entry_execution_stage=entry_execution_stage,
        entry_attention_level='high',
        initial_position_size_pct=5.0,
        remaining_position_size_pct=5.0,
        entry_quote_amount=50.0,
        entry_base_amount=0.5,
        initial_stop_price=96.0,
        active_stop_price=96.0,
        suggested_stop_price=96.0,
        risk_budget='normal',
        market_state_at_entry='NEUTRAL_MIXED',
        tp1_price=106.0,
        tp2_price=110.0,
        highest_price_since_entry=100.0,
        last_price=100.0,
        notes=[],
        tags=tags,
    )


def test_single_active_trade_state_locks_on_open_live_order(tmp_path):
    save_live_inflight_state(
        {
            'orders': {
                'SOL/USDT|live|armed': {
                    'status': 'open',
                    'client_order_id': 'cid-open',
                    'updated_at': utc_now_iso(),
                }
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.lock.blocking is True
    assert state.lock.lock_reason == 'live_submit_inflight_pending'
    assert state.lock.active_symbol == 'SOL/USDT'


def test_single_active_trade_state_does_not_lock_on_release_cooldown_only(tmp_path):
    save_live_inflight_state(
        {
            'orders': {},
            'released': {
                'SOL/USDT|live|armed': {
                    'released_at': utc_now_iso(),
                    'last_status': 'submit_failed',
                    'reason': 'stale_escalation',
                }
            },
            'quarantined': {},
        },
        base_dir=tmp_path,
    )

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.status == 'idle'
    assert state.lock.blocking is False
    assert state.lock.lock_reason is None


def test_single_active_trade_state_ignores_simulated_positions_for_live_locking(tmp_path):
    save_positions(
        [
            _active_position('SOL/USDT', 'pos-dry', tags=['manual_confirmed', 'dry_run', 'position_initialized']),
            _active_position('ETH/USDT', 'pos-paper', tags=['manual_confirmed', 'paper', 'position_initialized']),
        ],
        base_dir=tmp_path,
    )

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.status == 'idle'
    assert state.lock.blocking is False
    assert state.anomalies == []
    assert {item['truth_domain'] for item in state.observed_positions} == {'simulation'}
    assert all(item['participates_in_live_control_plane'] is False for item in state.observed_positions)
    assert all(item['blocking'] is False for item in state.observed_positions)


def test_single_active_trade_state_only_counts_live_positions_as_blockers(tmp_path):
    save_positions(
        [
            _active_position('SOL/USDT', 'pos-paper', tags=['manual_confirmed', 'paper', 'position_initialized']),
            _active_position('BTC/USDT', 'pos-live', tags=['manual_confirmed', 'live', 'truth_domain_live', 'position_initialized']),
        ],
        base_dir=tmp_path,
    )

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
    assert state.lock.active_symbol == 'BTC/USDT'
    assert state.anomalies == []


def test_single_active_trade_state_prefers_active_position_over_same_symbol_pending_sell_management(tmp_path):
    save_positions(
        [
            _active_position('ADA/USDT', 'pos-live-ada', tags=['manual_confirmed', 'live', 'truth_domain_live', 'position_initialized']),
        ],
        base_dir=tmp_path,
    )
    save_live_inflight_state(
        {
            'orders': {
                'ADA/USDT|live|armed': {
                    'status': 'open',
                    'side': 'sell',
                    'action_intent': 'SELL_EXIT',
                    'client_order_id': 'cid-ada-exit',
                    'updated_at': utc_now_iso(),
                }
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
    assert state.lock.active_symbol == 'ADA/USDT'
    assert state.lock.active_stage == 'position_open'
    assert state.observed_inflight[0]['pending_management'] is True
    assert state.lock.source_details['pending_live_management_orders'][0]['client_order_id'] == 'cid-ada-exit'
