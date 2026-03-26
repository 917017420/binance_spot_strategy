from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .active_trade_release_log import append_active_trade_release
from .live_inflight_state import detect_live_release_cooldown, load_live_inflight_state, load_pending_live_orders, prune_clearable_live_order_residue, save_live_inflight_state
from .live_submit_state import archive_live_submit_state, load_live_submit_state, prune_clearable_live_submit_state, save_live_submit_state, summarize_live_submit_state
from .positions_store import archive_stale_simulated_positions, load_live_active_positions
from .runner_state import load_runner_state, save_runner_state
from .single_active_trade_state import build_single_active_trade_state
from .utils import utc_now_iso


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

    after = build_single_active_trade_state(base_dir=base_dir)
    if _sync_runner_active_trade_summary(runner_state, after):
        actions.append(
            'RUNNER_ACTIVE_TRADE_SYNCED '
            f'status={after.status} symbol={after.lock.active_symbol} '
            f'stage={after.lock.active_stage} reason={after.lock.lock_reason}'
        )

    if actions:
        save_runner_state(runner_state, base_dir=base_dir)
        save_live_submit_state(submit_state, base_dir=base_dir)
        save_live_inflight_state(inflight_state, base_dir=base_dir)

    return ControlPlaneReconcileResult(
        ok=True,
        actions=actions,
        before_status=before.status,
        after_status=after.status,
    )
