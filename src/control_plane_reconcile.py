from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .active_trade_release_log import append_active_trade_release
from .live_inflight_state import (
    detect_live_release_cooldown,
    extract_symbol_from_logical_key,
    load_live_inflight_state,
    load_pending_live_orders,
    prune_clearable_live_order_residue,
    save_live_inflight_state,
)
from .live_submit_state import (
    archive_live_submit_state,
    load_live_submit_state,
    prune_clearable_live_submit_state,
    save_live_submit_state,
    summarize_live_submit_state,
)
from .positions_store import archive_stale_simulated_positions, load_live_active_positions
from .runner_state import load_runner_state, save_runner_state
from .single_active_trade_state import build_single_active_trade_state
from .utils import utc_now_iso


_RECOVERABLE_LOCAL_PREVIEW_PENDING_STATUSES = {'pending_real_submit', 'adapter_stubbed'}


@dataclass
class ControlPlaneReconcileResult:
    ok: bool
    actions: list[str] = field(default_factory=list)
    before_status: str | None = None
    after_status: str | None = None


def _sync_runner_active_trade_summary(runner_state: dict, snapshot) -> bool:
    next_summary = {
        'last_active_trade_status': snapshot.status,
        'last_active_trade_symbol': snapshot.lock.active_symbol,
        'last_active_trade_stage': snapshot.lock.active_stage,
        'last_active_trade_lock_reason': snapshot.lock.lock_reason,
    }
    changed = False
    for key, value in next_summary.items():
        if runner_state.get(key) == value:
            continue
        runner_state[key] = value
        changed = True
    return changed


def _archive_terminal_sell_submit_after_true_exit(
    submit_state: dict,
    *,
    active_symbols: set[str],
    inflight_symbols: set[str],
) -> tuple[dict, dict | None]:
    summary = summarize_live_submit_state(
        submit_state,
        active_symbols=active_symbols,
        inflight_symbols=inflight_symbols,
        stale_after_seconds=0.0,
    )
    should_archive = (
        summary.get('submit_side') == 'sell'
        and summary.get('is_terminal')
        and summary.get('flow_terminality') == 'terminal'
        and not summary.get('linked_to_active')
        and not summary.get('linked_to_inflight')
    )
    if not should_archive:
        return submit_state, None
    return archive_live_submit_state(submit_state, archive_reason='post_exit_control_plane_cleanup')


def _release_local_preview_pending_inflight_residue(
    inflight_state: dict,
    submit_state: dict,
    *,
    active_symbols: set[str],
) -> tuple[dict, list[str]]:
    submit_summary = summarize_live_submit_state(
        submit_state,
        active_symbols=active_symbols,
        inflight_symbols=set(),
        stale_after_seconds=0.0,
    )
    if not submit_summary.get('is_local_only_preview'):
        return inflight_state, []

    submit_symbol = submit_summary.get('symbol')
    submit_client_order_id = submit_summary.get('client_order_id')
    orders = dict(inflight_state.get('orders') or {})
    quarantined = dict(inflight_state.get('quarantined') or {})
    released_keys: list[str] = []
    now_iso = utc_now_iso()

    for logical_key, item in list(orders.items()):
        status = str(item.get('status') or '').strip().lower()
        if status not in _RECOVERABLE_LOCAL_PREVIEW_PENDING_STATUSES:
            continue
        symbol = extract_symbol_from_logical_key(logical_key)
        if symbol in active_symbols:
            continue
        client_order_id = item.get('client_order_id') or item.get('clientOrderId') or item.get('order_client_id')
        if (
            submit_client_order_id
            and client_order_id
            and str(submit_client_order_id) != str(client_order_id)
        ):
            continue
        same_symbol = bool(submit_symbol and symbol and str(submit_symbol) == str(symbol))
        same_client_order = bool(
            submit_client_order_id
            and client_order_id
            and str(submit_client_order_id) == str(client_order_id)
        )
        if not (same_symbol or same_client_order):
            continue

        orders.pop(logical_key, None)
        quarantined[logical_key] = {
            **item,
            'released_at': now_iso,
            'release_reason': 'local_preview_submit_residue',
            'submit_status': submit_state.get('last_submit_status'),
            'submit_symbol': submit_symbol,
            'submit_client_order_id': submit_client_order_id,
        }
        released_keys.append(logical_key)

    if not released_keys:
        return inflight_state, []

    next_state = {
        **inflight_state,
        'orders': orders,
        'quarantined': quarantined,
    }
    return next_state, released_keys


def _archive_local_preview_submit_residue(
    submit_state: dict,
    *,
    active_symbols: set[str],
    inflight_symbols: set[str],
) -> tuple[dict, dict | None]:
    summary = summarize_live_submit_state(
        submit_state,
        active_symbols=active_symbols,
        inflight_symbols=inflight_symbols,
        stale_after_seconds=0.0,
    )
    should_archive = (
        summary.get('is_local_only_preview')
        and summary.get('is_terminal')
        and not summary.get('linked_to_active')
        and not summary.get('linked_to_inflight')
    )
    if not should_archive:
        return submit_state, None
    return archive_live_submit_state(submit_state, archive_reason='local_preview_submit_residue')



def reconcile_control_plane_state(base_dir: str | Path | None = None) -> ControlPlaneReconcileResult:
    actions: list[str] = []
    cleanup = archive_stale_simulated_positions(base_dir=base_dir)
    actions.extend(cleanup.messages)
    before = build_single_active_trade_state(base_dir=base_dir)

    active_positions = load_live_active_positions(base_dir=base_dir)
    active_symbols = {position.symbol for position in active_positions}
    inflight_state = load_live_inflight_state(base_dir=base_dir)
    submit_state = load_live_submit_state(base_dir=base_dir)
    runner_state = load_runner_state(base_dir=base_dir)

    next_inflight_state, released_preview_keys = _release_local_preview_pending_inflight_residue(
        inflight_state,
        submit_state,
        active_symbols=active_symbols,
    )
    if released_preview_keys:
        inflight_state = next_inflight_state
        actions.append(
            'LIVE_INFLIGHT_RECOVERY_RELEASED '
            f'count={len(released_preview_keys)} keys={released_preview_keys} '
            'reason=local_preview_submit_residue'
        )

    unresolved_inflight_orders = load_pending_live_orders(inflight_state)
    unresolved_inflight_symbols = {
        logical_key.split('|')[0]
        for logical_key in unresolved_inflight_orders.keys()
        if logical_key and '|' in logical_key
    }

    # If there is no active live position and no unresolved inflight order, clear stale summary-style blockers.
    if not active_positions and not unresolved_inflight_orders:
        if runner_state.get('last_active_trade_lock_reason') or runner_state.get('last_active_trade_symbol'):
            released_symbol = runner_state.get('last_active_trade_symbol')
            release_path = append_active_trade_release(
                {
                    'symbol': released_symbol,
                    'release_reason': 'control_plane_reconcile_unlock',
                    'resulting_position_status': 'none',
                    'source': 'runner_state',
                },
                base_dir=base_dir,
            )
            runner_state['last_active_trade_status'] = 'idle'
            runner_state['last_active_trade_symbol'] = None
            runner_state['last_active_trade_stage'] = 'none'
            runner_state['last_active_trade_lock_reason'] = None
            actions.append(f'RUNNER_ACTIVE_TRADE_CLEARED no_active_positions_no_unresolved_inflight release_log={release_path}')

        next_submit_state, archived_local_preview = _archive_local_preview_submit_residue(
            submit_state,
            active_symbols=active_symbols,
            inflight_symbols=unresolved_inflight_symbols,
        )
        if archived_local_preview is not None:
            submit_state = next_submit_state
            actions.append(
                'LIVE_SUBMIT_STATE_ARCHIVED_LOCAL_PREVIEW '
                f"status={archived_local_preview.get('last_submit_status')} "
                f"symbol={archived_local_preview.get('last_symbol')} "
                f"reason={archived_local_preview.get('archive_reason')}"
            )
        else:
            next_submit_state, archived_terminal_sell = _archive_terminal_sell_submit_after_true_exit(
                submit_state,
                active_symbols=active_symbols,
                inflight_symbols=unresolved_inflight_symbols,
            )
            if archived_terminal_sell is not None:
                submit_state = next_submit_state
                actions.append(
                    'LIVE_SUBMIT_STATE_AUTO_ARCHIVED '
                    f"status={archived_terminal_sell.get('last_submit_status')} "
                    f"symbol={archived_terminal_sell.get('last_symbol')} "
                    f"reason={archived_terminal_sell.get('archive_reason')}"
                )
            else:
                response = submit_state.get('last_response') or {}
                if submit_state.get('last_symbol') or submit_state.get('last_submit_status'):
                    submit_state['last_submit_status'] = 'cleared_after_unlock'
                    submit_state['last_symbol'] = None
                    submit_state['last_error'] = None
                    submit_state['last_response'] = {
                        **response,
                        'status': 'cleared_after_unlock',
                        'cleared_at': utc_now_iso(),
                    }
                    actions.append('LIVE_SUBMIT_STATE_CLEARED no_active_positions_no_unresolved_inflight')

    # Drop expired release records from inflight state so snapshot noise shrinks over time.
    released = inflight_state.get('released') or {}
    active_release_cooldown = (detect_live_release_cooldown(inflight_state, cooldown_seconds=900.0).get('orders') or {})
    if len(active_release_cooldown) != len(released):
        trimmed_count = max(len(released) - len(active_release_cooldown), 0)
        inflight_state['released'] = active_release_cooldown
        actions.append(f'LIVE_RELEASE_RECORDS_TRIMMED removed={trimmed_count}')

    next_inflight_state, removed_residue = prune_clearable_live_order_residue(
        inflight_state,
        active_symbols=active_symbols,
        older_than_seconds=1800.0,
    )
    if removed_residue:
        inflight_state = next_inflight_state
        actions.append(f"LIVE_ORDER_RESIDUE_PRUNED removed={len(removed_residue)} keys={removed_residue}")

    inflight_symbols = {
        symbol
        for symbol in [item.get('symbol') for item in (inflight_state.get('orders') or {}).values()]
        if symbol
    }
    next_submit_state, archived_submit = prune_clearable_live_submit_state(
        submit_state,
        active_symbols=active_symbols,
        inflight_symbols=inflight_symbols,
        older_than_seconds=1800.0,
    )
    if archived_submit is not None:
        submit_state = next_submit_state
        actions.append(
            f"LIVE_SUBMIT_STATE_ARCHIVED status={archived_submit.get('last_submit_status')} symbol={archived_submit.get('last_symbol')}"
        )

    if actions:
        save_runner_state(runner_state, base_dir=base_dir)
        save_live_submit_state(submit_state, base_dir=base_dir)
        save_live_inflight_state(inflight_state, base_dir=base_dir)

    after = build_single_active_trade_state(base_dir=base_dir)
    if _sync_runner_active_trade_summary(runner_state, after):
        actions.append(
            'RUNNER_ACTIVE_TRADE_SYNCED '
            f'status={after.status} symbol={after.lock.active_symbol} '
            f'stage={after.lock.active_stage} reason={after.lock.lock_reason}'
        )
        save_runner_state(runner_state, base_dir=base_dir)

    return ControlPlaneReconcileResult(
        ok=True,
        actions=actions,
        before_status=before.status,
        after_status=after.status,
    )
