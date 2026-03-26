from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .live_inflight_state import extract_symbol_from_logical_key, load_live_inflight_state, load_pending_live_orders
from .live_position_residue import partition_live_positions_for_control_plane, summarize_position_residue
from .position_lifecycle import build_position_lifecycle
from .positions_store import classify_position_truth_domain, load_active_positions, load_live_active_positions
from .runner_state import load_runner_state


@dataclass
class ActiveTradeLock:
    active_symbol: str | None
    active_stage: str
    lock_reason: str | None
    lock_owner: str | None
    current_position_id: str | None = None
    live_logical_key: str | None = None
    source_details: dict = field(default_factory=dict)
    blocking: bool = False
    can_admit_new_live_symbol: bool = True
    needs_manual_intervention: bool = False


@dataclass
class ActiveTradeStateSnapshot:
    status: str
    lock: ActiveTradeLock
    observed_positions: list[dict] = field(default_factory=list)
    observed_inflight: list[dict] = field(default_factory=list)
    residue_positions: list[dict] = field(default_factory=list)
    residue_summary: dict = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)


_ACTIVE_LIFECYCLE_TO_STAGE = {
    'open_initial': 'position_open',
    'tp1_reduced': 'tp1_reduced',
    'tp2_reduced_trailing': 'tp2_trailing',
    'fully_exited': 'none',
    'stopped_out': 'none',
    'custom_state': 'position_open',
}


def _normalize_action_intent(value: str | None) -> str | None:
    text = str(value or '').strip().upper()
    return text or None


def _is_sell_management_order(item: dict) -> bool:
    side = str(item.get('side') or '').strip().lower()
    action_intent = _normalize_action_intent(item.get('action_intent'))
    if side == 'sell':
        return True
    return action_intent in {'SELL_EXIT', 'SELL_REDUCE', 'EXIT', 'REDUCE', 'FULL_EXIT', 'PARTIAL_EXIT'}


def _describe_pending_management_orders(pending_orders: dict[str, dict]) -> list[dict]:
    return [
        {
            'logical_key': logical_key,
            'status': item.get('status'),
            'side': item.get('side'),
            'action_intent': item.get('action_intent'),
            'client_order_id': item.get('client_order_id'),
            'updated_at': item.get('updated_at'),
        }
        for logical_key, item in pending_orders.items()
    ]



def _build_open_position_lock(position, *, pending_management_orders: dict[str, dict] | None = None) -> ActiveTradeLock:
    lifecycle = build_position_lifecycle(position)
    source_details = {
        'position_status': position.status,
        'remaining_position_size_pct': position.remaining_position_size_pct,
        'lifecycle_stage': lifecycle.lifecycle_stage,
        'lifecycle_notes': lifecycle.notes,
    }
    if pending_management_orders:
        source_details['pending_live_management_orders'] = _describe_pending_management_orders(pending_management_orders)
    return ActiveTradeLock(
        active_symbol=position.symbol,
        active_stage=_ACTIVE_LIFECYCLE_TO_STAGE.get(lifecycle.lifecycle_stage, 'position_open'),
        lock_reason='active_open_position_exists',
        lock_owner='positions',
        current_position_id=position.position_id,
        source_details=source_details,
        blocking=True,
        can_admit_new_live_symbol=False,
    )



def _build_inflight_lock(logical_key: str, item: dict) -> ActiveTradeLock:
    return ActiveTradeLock(
        active_symbol=extract_symbol_from_logical_key(logical_key),
        active_stage='submit_pending',
        lock_reason='live_submit_inflight_pending',
        lock_owner='live_inflight_state',
        live_logical_key=logical_key,
        source_details={
            'status': item.get('status'),
            'client_order_id': item.get('client_order_id'),
            'updated_at': item.get('updated_at'),
        },
        blocking=True,
        can_admit_new_live_symbol=False,
    )



def build_single_active_trade_state(base_dir: str | Path | None = None) -> ActiveTradeStateSnapshot:
    runner_state = load_runner_state(base_dir=base_dir)
    inflight_state = load_live_inflight_state(base_dir=base_dir)
    active_positions = load_active_positions(base_dir=base_dir)
    live_active_positions = load_live_active_positions(base_dir=base_dir)
    blocking_positions, residue_positions = partition_live_positions_for_control_plane(live_active_positions)
    residue_summary = summarize_position_residue(residue_positions)
    pending_live_orders = load_pending_live_orders(inflight_state)
    blocking_position_symbols = {position.symbol for position in blocking_positions}
    pending_management_orders = {
        logical_key: item
        for logical_key, item in pending_live_orders.items()
        if extract_symbol_from_logical_key(logical_key) in blocking_position_symbols and _is_sell_management_order(item)
    }
    ownership_pending_orders = {
        logical_key: item
        for logical_key, item in pending_live_orders.items()
        if logical_key not in pending_management_orders
    }
    observed_positions = [
        {
            'position_id': position.position_id,
            'symbol': position.symbol,
            'status': position.status,
            'remaining_position_size_pct': position.remaining_position_size_pct,
            'truth_domain': classify_position_truth_domain(position),
            'participates_in_live_control_plane': position in live_active_positions,
            'blocking': position in blocking_positions,
        }
        for position in active_positions
    ]
    for item in observed_positions:
        residue_match = next((residue for residue in residue_positions if residue.get('position_id') == item['position_id']), None)
        if residue_match is None:
            continue
        item['blocking'] = False
        item['residue_kind'] = residue_match.get('residue_kind')
        item['residue_reason'] = residue_match.get('reason')
        item['estimated_remaining_quote_amount'] = residue_match.get('estimated_remaining_quote_amount')
        item['estimated_remaining_base_amount'] = residue_match.get('estimated_remaining_base_amount')
    observed_inflight = [
        {
            'logical_key': logical_key,
            'symbol': extract_symbol_from_logical_key(logical_key),
            'status': item.get('status'),
            'client_order_id': item.get('client_order_id'),
            'updated_at': item.get('updated_at'),
            'side': item.get('side'),
            'action_intent': item.get('action_intent'),
            'pending_management': logical_key in pending_management_orders,
        }
        for logical_key, item in pending_live_orders.items()
    ]

    anomalies: list[str] = []

    if bool(runner_state.get('fuse_open')):
        lock = ActiveTradeLock(
            active_symbol=None,
            active_stage='fused',
            lock_reason='runner_fuse_open',
            lock_owner='runner_state',
            source_details={
                'last_health_status': runner_state.get('last_health_status'),
                'last_health_reason': runner_state.get('last_health_reason'),
            },
            blocking=True,
            can_admit_new_live_symbol=False,
            needs_manual_intervention=True,
        )
        return ActiveTradeStateSnapshot(
            status='fused',
            lock=lock,
            observed_positions=observed_positions,
            observed_inflight=observed_inflight,
            residue_positions=residue_positions,
            residue_summary=residue_summary,
            anomalies=anomalies,
        )

    if runner_state.get('last_health_status') == 'warm_restart':
        lock = ActiveTradeLock(
            active_symbol=None,
            active_stage='warm_restart',
            lock_reason='recovery_warm_restart_active',
            lock_owner='runner_state',
            source_details={
                'warm_restart_cycles_remaining': runner_state.get('warm_restart_cycles_remaining'),
                'last_recovery_reason': runner_state.get('last_recovery_reason'),
            },
            blocking=True,
            can_admit_new_live_symbol=False,
        )
        return ActiveTradeStateSnapshot(
            status='warm_restart',
            lock=lock,
            observed_positions=observed_positions,
            observed_inflight=observed_inflight,
            residue_positions=residue_positions,
            residue_summary=residue_summary,
            anomalies=anomalies,
        )

    pending_symbols = {extract_symbol_from_logical_key(logical_key) for logical_key in ownership_pending_orders.keys()}
    active_position_symbols = blocking_position_symbols

    if len(blocking_positions) > 1:
        anomalies.append('multiple_active_positions_detected')
    if len(ownership_pending_orders) > 1:
        anomalies.append('multiple_live_inflight_detected')
    if pending_symbols and active_position_symbols and pending_symbols != active_position_symbols:
        anomalies.append('live_domain_symbol_conflict')

    if anomalies:
        conflict_symbol = next(iter(active_position_symbols or pending_symbols), None)
        lock = ActiveTradeLock(
            active_symbol=conflict_symbol,
            active_stage='anomalous',
            lock_reason=anomalies[0],
            lock_owner='single_active_trade_state',
            source_details={
                'pending_symbols': sorted(symbol for symbol in pending_symbols if symbol),
                'active_position_symbols': sorted(symbol for symbol in active_position_symbols if symbol),
            },
            blocking=True,
            can_admit_new_live_symbol=False,
            needs_manual_intervention=True,
        )
        return ActiveTradeStateSnapshot(
            status='anomalous',
            lock=lock,
            observed_positions=observed_positions,
            observed_inflight=observed_inflight,
            residue_positions=residue_positions,
            residue_summary=residue_summary,
            anomalies=anomalies,
        )

    if ownership_pending_orders:
        logical_key, item = next(iter(ownership_pending_orders.items()))
        return ActiveTradeStateSnapshot(
            status='locked',
            lock=_build_inflight_lock(logical_key, item),
            observed_positions=observed_positions,
            observed_inflight=observed_inflight,
            residue_positions=residue_positions,
            residue_summary=residue_summary,
            anomalies=anomalies,
        )

    if blocking_positions:
        return ActiveTradeStateSnapshot(
            status='locked',
            lock=_build_open_position_lock(
                blocking_positions[0],
                pending_management_orders={
                    logical_key: item
                    for logical_key, item in pending_management_orders.items()
                    if extract_symbol_from_logical_key(logical_key) == blocking_positions[0].symbol
                },
            ),
            observed_positions=observed_positions,
            observed_inflight=observed_inflight,
            residue_positions=residue_positions,
            residue_summary=residue_summary,
            anomalies=anomalies,
        )

    return ActiveTradeStateSnapshot(
        status='idle',
        lock=ActiveTradeLock(
            active_symbol=None,
            active_stage='none',
            lock_reason=None,
            lock_owner=None,
            source_details={},
            blocking=False,
            can_admit_new_live_symbol=True,
        ),
        observed_positions=observed_positions,
        observed_inflight=observed_inflight,
        residue_positions=residue_positions,
        residue_summary=residue_summary,
        anomalies=anomalies,
    )
