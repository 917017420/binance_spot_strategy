from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
LIVE_SUBMIT_STATE_FILE = DEFAULT_EXECUTION_DIR / 'live_submit_state.json'
TERMINAL_SUBMIT_STATUSES = {
    'submit_failed',
    'filled',
    'closed',
    'canceled',
    'cancelled',
    'rejected',
    'cleared_after_unlock',
    'adapter_stubbed',
}
PENDING_SUBMIT_STATUSES = {'pending_real_submit', 'submitted', 'open', 'partially_filled', 'partial'}



def _live_submit_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / LIVE_SUBMIT_STATE_FILE.name



def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None



def load_live_submit_state(base_dir: str | Path | None = None) -> dict:
    path = _live_submit_state_path(base_dir)
    if not path.exists():
        return {
            'last_client_order_id': None,
            'last_submit_status': None,
            'last_submit_side': None,
            'last_symbol': None,
            'last_request': None,
            'last_response': None,
            'last_action_intent': None,
            'last_error': None,
            'archived_last_submit': None,
            'updated_at': None,
        }
    state = json.loads(path.read_text(encoding='utf-8'))
    state.setdefault('last_client_order_id', None)
    state.setdefault('last_submit_status', None)
    state.setdefault('last_submit_side', None)
    state.setdefault('last_symbol', None)
    state.setdefault('last_request', None)
    state.setdefault('last_response', None)
    state.setdefault('last_action_intent', None)
    state.setdefault('last_error', None)
    state.setdefault('archived_last_submit', None)
    state.setdefault('updated_at', None)
    return state



def save_live_submit_state(state: dict, base_dir: str | Path | None = None) -> Path:
    path = _live_submit_state_path(base_dir)
    payload = {
        **state,
        'updated_at': utc_now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path



def summarize_live_submit_state(
    state: dict | None = None,
    *,
    active_symbols: set[str] | None = None,
    inflight_symbols: set[str] | None = None,
    stale_after_seconds: float = 1800.0,
) -> dict:
    payload = state or {}
    active_symbols = active_symbols or set()
    inflight_symbols = inflight_symbols or set()

    status = str(payload.get('last_submit_status') or '').lower()
    symbol = payload.get('last_symbol')
    last_request = payload.get('last_request') or {}
    request_metadata = last_request.get('metadata') or {}
    submit_side = str(
        payload.get('last_submit_side')
        or last_request.get('side')
        or (payload.get('last_response') or {}).get('side')
        or ''
    ).strip().lower() or None
    action_intent = str(
        payload.get('last_action_intent')
        or request_metadata.get('action_intent')
        or ''
    ).strip() or None
    updated_at = _parse_iso(payload.get('updated_at'))
    age_seconds = None
    if updated_at is not None:
        age_seconds = max((datetime.now(timezone.utc) - updated_at).total_seconds(), 0.0)

    is_pending = status in PENDING_SUBMIT_STATUSES
    is_terminal = status in TERMINAL_SUBMIT_STATUSES
    is_failed = status == 'submit_failed'
    is_stale = bool(age_seconds is not None and age_seconds >= stale_after_seconds)
    linked_to_active = bool(symbol and symbol in active_symbols)
    linked_to_inflight = bool(symbol and symbol in inflight_symbols)
    should_archive = bool(is_terminal and is_stale and not linked_to_active and not linked_to_inflight)

    classification = 'empty'
    if is_pending:
        classification = 'pending'
    elif is_failed:
        classification = 'failed'
    elif linked_to_active and is_terminal:
        classification = 'active_position_management'
    elif is_terminal:
        classification = 'terminal_residue'
    elif status:
        classification = 'other'

    order_terminality = 'none'
    if is_failed:
        order_terminality = 'failed'
    elif is_pending:
        order_terminality = 'pending'
    elif is_terminal:
        order_terminality = 'terminal'

    flow_terminality = 'none'
    flow_reason = None
    if linked_to_active:
        flow_terminality = 'active_position'
        if submit_side == 'buy':
            flow_reason = 'terminal_buy_order_opened_position'
        elif submit_side == 'sell':
            flow_reason = 'sell_order_reconciled_but_position_remains_open'
        else:
            flow_reason = 'submit_symbol_has_active_live_position'
    elif is_failed:
        flow_terminality = 'failed'
        flow_reason = 'submit_failed'
    elif is_pending or linked_to_inflight:
        flow_terminality = 'pending'
        flow_reason = 'live_order_still_inflight'
    elif is_terminal:
        flow_terminality = 'terminal'
        if submit_side == 'sell':
            flow_reason = 'terminal_sell_flow_without_active_position'
        elif submit_side == 'buy':
            flow_reason = 'terminal_buy_flow_without_active_position'
        else:
            flow_reason = 'terminal_order_without_active_position'

    return {
        'status': status or None,
        'symbol': symbol,
        'client_order_id': payload.get('last_client_order_id'),
        'submit_side': submit_side,
        'action_intent': action_intent,
        'classification': classification,
        'order_terminality': order_terminality,
        'flow_terminality': flow_terminality,
        'flow_reason': flow_reason,
        'is_pending': is_pending,
        'is_terminal': is_terminal,
        'is_failed': is_failed,
        'is_stale': is_stale,
        'age_seconds': age_seconds,
        'linked_to_active': linked_to_active,
        'linked_to_inflight': linked_to_inflight,
        'should_archive': should_archive,
        'archived_last_submit': payload.get('archived_last_submit'),
        'last_error': payload.get('last_error'),
    }


def archive_live_submit_state(state: dict, *, archive_reason: str) -> tuple[dict, dict | None]:
    def _has_payload(value) -> bool:
        if value is None:
            return False
        if isinstance(value, dict):
            return bool(value)
        return value != ''

    has_live_submit_payload = any(
        _has_payload(state.get(key))
        for key in (
            'last_client_order_id',
            'last_submit_status',
            'last_submit_side',
            'last_symbol',
            'last_request',
            'last_response',
            'last_action_intent',
            'last_error',
        )
    )
    if not has_live_submit_payload:
        return state, None

    archived = {
        'archived_at': utc_now_iso(),
        'archive_reason': archive_reason,
        'last_client_order_id': state.get('last_client_order_id'),
        'last_submit_status': state.get('last_submit_status'),
        'last_submit_side': state.get('last_submit_side'),
        'last_symbol': state.get('last_symbol'),
        'last_request': state.get('last_request'),
        'last_response': state.get('last_response'),
        'last_action_intent': state.get('last_action_intent'),
        'last_error': state.get('last_error'),
        'last_updated_at': state.get('updated_at'),
    }
    next_state = {
        **state,
        'archived_last_submit': archived,
        'last_client_order_id': None,
        'last_submit_status': None,
        'last_submit_side': None,
        'last_symbol': None,
        'last_request': None,
        'last_response': None,
        'last_action_intent': None,
        'last_error': None,
    }
    return next_state, archived


def prune_clearable_live_submit_state(
    state: dict,
    *,
    active_symbols: set[str] | None = None,
    inflight_symbols: set[str] | None = None,
    older_than_seconds: float = 1800.0,
) -> tuple[dict, dict | None]:
    summary = summarize_live_submit_state(
        state,
        active_symbols=active_symbols,
        inflight_symbols=inflight_symbols,
        stale_after_seconds=older_than_seconds,
    )
    if not summary.get('should_archive'):
        return state, None

    return archive_live_submit_state(state, archive_reason='stale_terminal_submit_state')
