from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .control_plane_reconcile import reconcile_control_plane_state
from .live_inflight_state import save_live_inflight_state
from .live_submit_state import save_live_submit_state
from .models import Position
from .positions_store import save_positions
from .runner_state import save_runner_state
from .single_active_trade_repair import repair_single_active_trade_state
from .single_active_trade_state import build_single_active_trade_state
from .utils import utc_now_iso



def _base_position(symbol: str, position_id: str) -> Position:
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
        entry_execution_stage='armed',
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
        tags=['scenario'],
    )



def run_single_active_trade_scenarios() -> dict:
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix='single-active-scenarios-') as temp_dir:
        base_dir = Path(temp_dir)

        # Scenario 1: one active position => locked by active_open_position_exists
        save_positions([_base_position('SOL/USDT', 'pos-sol')], base_dir=base_dir)
        save_live_inflight_state({'orders': {}, 'released': {}}, base_dir=base_dir)
        save_live_submit_state({}, base_dir=base_dir)
        save_runner_state({}, base_dir=base_dir)
        state1 = build_single_active_trade_state(base_dir=base_dir)
        results['single_position_locked'] = {
            'status': state1.status,
            'active_symbol': state1.lock.active_symbol,
            'lock_reason': state1.lock.lock_reason,
        }

        # Scenario 2: multiple active positions => anomalous
        save_positions([
            _base_position('SOL/USDT', 'pos-sol'),
            _base_position('TEST/USDT', 'pos-test'),
        ], base_dir=base_dir)
        state2 = build_single_active_trade_state(base_dir=base_dir)
        results['multiple_positions_anomalous'] = {
            'status': state2.status,
            'anomalies': state2.anomalies,
            'lock_reason': state2.lock.lock_reason,
        }

        # Scenario 3: repair collapses to one canonical position
        repair = repair_single_active_trade_state(base_dir=base_dir, dry_run=False)
        state3 = build_single_active_trade_state(base_dir=base_dir)
        results['repair_restores_lock'] = {
            'status': state3.status,
            'active_symbol': state3.lock.active_symbol,
            'lock_reason': state3.lock.lock_reason,
            'repair_actions': repair.actions,
        }

        # Scenario 4: no active position and no inflight => reconcile unlocks summary state
        idle_position = _base_position('SOL/USDT', 'pos-sol-idle')
        idle_position.status = 'closed'
        idle_position.remaining_position_size_pct = 0.0
        save_positions([idle_position], base_dir=base_dir)
        save_live_inflight_state({'orders': {}, 'released': {}}, base_dir=base_dir)
        save_live_submit_state({
            'last_client_order_id': 'test-order',
            'last_submit_status': 'submitted',
            'last_symbol': 'SOL/USDT',
            'last_response': {'status': 'submitted'},
            'last_error': None,
        }, base_dir=base_dir)
        save_runner_state({
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'SOL/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        }, base_dir=base_dir)
        reconcile = reconcile_control_plane_state(base_dir=base_dir)
        state4 = build_single_active_trade_state(base_dir=base_dir)
        results['reconcile_unlocks_idle'] = {
            'status': state4.status,
            'active_symbol': state4.lock.active_symbol,
            'lock_reason': state4.lock.lock_reason,
            'reconcile_actions': reconcile.actions,
        }

    return results



def format_single_active_trade_scenarios() -> str:
    return json.dumps(run_single_active_trade_scenarios(), ensure_ascii=False, indent=2)
