from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .active_trade_release_log import append_active_trade_release
from .config import load_settings
from .live_inflight_state import build_live_logical_key, load_pending_live_orders
from .live_exchange_adapter import submit_live_order
from .live_submit_state import PENDING_SUBMIT_STATUSES, load_live_submit_state
from .live_order_payload import build_position_live_order_payload
from .models import Position, PositionActionResult, PositionEvent, PositionState
from .positions_store import append_position_event
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
POSITION_ACTIONS_FILE = DEFAULT_EXECUTION_DIR / 'position_actions.jsonl'
LIVE_MANAGEMENT_PENDING_GUARD_SECONDS = 1800.0


def _position_actions_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITION_ACTIONS_FILE.name


def load_position_action_results(base_dir: str | Path | None = None) -> list[PositionActionResult]:
    path = _position_actions_path(base_dir)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    return [PositionActionResult.model_validate(json.loads(line)) for line in lines]


def should_append_position_action_result(result: PositionActionResult, base_dir: str | Path | None = None) -> bool:
    existing = load_position_action_results(base_dir)
    for item in reversed(existing[-20:]):
        if item.position_id != result.position_id:
            continue
        if item.action == result.action and item.mode == result.mode and item.status == result.status and item.details == result.details:
            return False
        return True
    return True


def append_position_action_result(result: PositionActionResult, base_dir: str | Path | None = None) -> Path:
    path = _position_actions_path(base_dir)
    if not should_append_position_action_result(result, base_dir=base_dir):
        return path
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(result.model_dump(mode='json'), ensure_ascii=False) + '\n')
    return path


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None


def _resolve_executable_action(suggested_action: str) -> str:
    return 'SELL_REDUCE' if suggested_action in {'SELL_REDUCE', 'ENABLE_TRAILING_STOP'} else suggested_action


def _resolve_requested_reduce_pct(position: Position, state: PositionState, executable_action: str) -> float:
    if executable_action != 'SELL_REDUCE':
        return 0.0
    if state.suggested_action == 'ENABLE_TRAILING_STOP':
        return max(float(position.tp2_reduce_pct or 0.0), 0.0)
    return max(float(position.tp1_reduce_pct or 0.0), 0.0)


def _find_pending_live_management_guard(
    position: Position,
    state: PositionState,
    *,
    base_dir: str | Path | None = None,
) -> dict | None:
    executable_action = _resolve_executable_action(state.suggested_action)
    now = datetime.now(timezone.utc)

    for item in reversed(load_position_action_results(base_dir=base_dir)[-20:]):
        if item.position_id != position.position_id or item.mode != 'live':
            continue
        if item.action != executable_action or item.status not in {'submitted', 'simulated'}:
            continue
        if item.details.get('suggested_action') != state.suggested_action:
            continue
        executed_at = _parse_iso(item.executed_at)
        age_seconds = max((now - executed_at).total_seconds(), 0.0) if executed_at is not None else None
        if age_seconds is not None and age_seconds > LIVE_MANAGEMENT_PENDING_GUARD_SECONDS:
            continue
        return {
            'source': 'position_action_result',
            'action_id': item.action_id,
            'status': item.status,
            'executed_at': item.executed_at,
            'age_seconds': age_seconds,
        }

    logical_key = build_live_logical_key(position.symbol, 'live', 'armed')
    pending_live_order = load_pending_live_orders(base_dir=base_dir).get(logical_key)
    if pending_live_order is not None:
        pending_side = str(pending_live_order.get('side') or 'sell').strip().lower()
        pending_action_intent = str(pending_live_order.get('action_intent') or '').strip().upper() or None
        if pending_side == 'sell' and pending_action_intent in {None, executable_action}:
            return {
                'source': 'live_inflight_state',
                'logical_key': logical_key,
                'status': pending_live_order.get('status'),
                'client_order_id': pending_live_order.get('client_order_id'),
                'updated_at': pending_live_order.get('updated_at'),
                'action_intent': pending_action_intent,
            }

    submit_state = load_live_submit_state(base_dir=base_dir)
    submit_status = str(submit_state.get('last_submit_status') or '').strip().lower()
    submit_side = str(submit_state.get('last_submit_side') or '').strip().lower()
    submit_symbol = submit_state.get('last_symbol')
    submit_action_intent = str(submit_state.get('last_action_intent') or '').strip().upper() or None
    pending_submit_statuses = set(PENDING_SUBMIT_STATUSES) | {'adapter_stubbed'}
    if (
        submit_symbol == position.symbol
        and submit_side == 'sell'
        and submit_status in pending_submit_statuses
        and submit_action_intent in {None, executable_action}
    ):
        return {
            'source': 'live_submit_state',
            'status': submit_status,
            'client_order_id': submit_state.get('last_client_order_id'),
            'updated_at': submit_state.get('updated_at'),
            'action_intent': submit_action_intent,
        }

    return None


def execute_position_action(
    position: Position,
    state: PositionState,
    mode: str = 'dry_run',
    *,
    base_dir: str | Path | None = None,
) -> tuple[PositionActionResult, str, str | None]:
    suggested_action = state.suggested_action
    executable_action = _resolve_executable_action(suggested_action)
    if executable_action not in {'SELL_REDUCE', 'SELL_EXIT'}:
        result = PositionActionResult(
            action_id=f"{position.position_id}:{utc_now_iso()}:{suggested_action}",
            position_id=position.position_id,
            symbol=position.symbol,
            mode=mode,
            action='HOLD',
            status='skipped',
            executed_at=utc_now_iso(),
            resulting_position_status=position.status,
            message='No executable position action was produced.',
            details={'suggested_action': suggested_action},
        )
        path = append_position_action_result(result, base_dir=base_dir)
        return result, str(path), None

    if mode == 'live':
        pending_guard = _find_pending_live_management_guard(position, state, base_dir=base_dir)
        if pending_guard is not None:
            result = PositionActionResult(
                action_id=f"{position.position_id}:{utc_now_iso()}:{executable_action}:pending-guard",
                position_id=position.position_id,
                symbol=position.symbol,
                mode=mode,
                action=executable_action,
                status='skipped',
                executed_at=utc_now_iso(),
                resulting_position_status=position.status,
                message='Skipped duplicate live management submit while an earlier sell remains unresolved.',
                details={
                    'suggested_action': suggested_action,
                    'last_price': position.last_price,
                    'remaining_position_size_pct': position.remaining_position_size_pct,
                    'active_stop_price': position.active_stop_price,
                    'lifecycle_truth_source': 'pending_submit_guard',
                    'skip_reason': 'pending_live_management_order_exists',
                    'pending_guard': pending_guard,
                },
            )
            path = append_position_action_result(result, base_dir=base_dir)
            return result, str(path), None

    requested_reduce_pct = _resolve_requested_reduce_pct(position, state, executable_action)
    status = 'simulated' if mode == 'dry_run' else 'executed'
    submit_details = None
    submit_status = None
    if mode == 'live':
        payload = build_position_live_order_payload(position, state, requested_reduce_pct=requested_reduce_pct)
        settings = load_settings()
        submit_result = submit_live_order(settings, payload, base_dir=base_dir)
        submit_details = submit_result.details
        submit_status = submit_result.status
        if submit_result.status == 'submitted':
            status = 'submitted'
        elif submit_result.status == 'adapter_stubbed':
            status = 'simulated'
        else:
            status = 'failed'
    result = PositionActionResult(
        action_id=f"{position.position_id}:{utc_now_iso()}:{executable_action}",
        position_id=position.position_id,
        symbol=position.symbol,
        mode=mode,
        action=executable_action,
        status=status,
        executed_at=utc_now_iso(),
        requested_reduce_pct=requested_reduce_pct,
        resulting_position_status=position.status,
        message=(
            f'Position action {executable_action} submitted in live mode; await fill reconcile before lifecycle mutation.'
            if mode == 'live' and status == 'submitted'
            else f'Position action {executable_action} previewed in live mode without a real submit.'
            if mode == 'live' and status == 'simulated'
            else f'Position action {executable_action} handled in live mode via exchange adapter.'
            if mode == 'live'
            else f'Position action {executable_action} handled in {mode} mode.'
        ),
        details={
            'suggested_action': suggested_action,
            'last_price': position.last_price,
            'remaining_position_size_pct': position.remaining_position_size_pct,
            'active_stop_price': position.active_stop_price,
            'lifecycle_truth_source': 'submit_fact_only' if mode == 'live' and status == 'submitted' else 'position_state',
            'live_submit_status': submit_status,
            'live_submit_details': submit_details,
        },
    )
    action_path = append_position_action_result(result, base_dir=base_dir)

    event = PositionEvent(
        event_id=f"{position.position_id}:{utc_now_iso()}:POSITION_ACTION_EXECUTED",
        position_id=position.position_id,
        symbol=position.symbol,
        event_type='POSITION_ACTION_EXECUTED',
        created_at=utc_now_iso(),
        position_status=position.status,
        suggested_action=executable_action,
        reasons=state.reasons,
        details={
            'mode': mode,
            'action_status': status,
            'requested_reduce_pct': requested_reduce_pct,
            'original_suggested_action': suggested_action,
        },
    )
    event_path = append_position_event(event, base_dir=base_dir)

    if mode != 'live' and executable_action == 'SELL_EXIT' and position.status in {'closed', 'stopped', 'cancelled'}:
        release_path = append_active_trade_release(
            {
                'position_id': position.position_id,
                'symbol': position.symbol,
                'release_reason': 'position_exit_completed',
                'resulting_position_status': position.status,
                'action_id': result.action_id,
                'action_mode': mode,
            },
            base_dir=base_dir,
        )
        result.details['active_trade_release_log_path'] = str(release_path)

    return result, str(action_path), str(event_path)
