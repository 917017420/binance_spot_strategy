from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


TERMINAL_LIVE_RESIDUE_STATUSES = {'filled', 'closed', 'canceled', 'cancelled', 'rejected', 'submit_failed'}
ACTIVE_LIVE_INFLIGHT_STATUSES = {
    'pending_real_submit',
    'adapter_stubbed',
    'submitted',
    'open',
    'new',
    'partial',
    'partially_filled',
    'partially-filled',
}


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
LIVE_INFLIGHT_STATE_FILE = DEFAULT_EXECUTION_DIR / 'live_inflight_state.json'



def _live_inflight_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / LIVE_INFLIGHT_STATE_FILE.name



def load_live_inflight_state(base_dir: str | Path | None = None) -> dict:
    path = _live_inflight_state_path(base_dir)
    if not path.exists():
        return {'orders': {}, 'released': {}, 'quarantined': {}, 'updated_at': None}
    state = json.loads(path.read_text(encoding='utf-8'))
    state.setdefault('orders', {})
    state.setdefault('released', {})
    state.setdefault('quarantined', {})
    return state



def save_live_inflight_state(state: dict, base_dir: str | Path | None = None) -> Path:
    path = _live_inflight_state_path(base_dir)
    state['updated_at'] = utc_now_iso()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return path



def build_live_logical_key(symbol: str, route: str, route_status: str) -> str:
    return f'{symbol}|{route}|{route_status}'



def extract_symbol_from_logical_key(logical_key: str | None) -> str | None:
    if not logical_key:
        return None
    parts = logical_key.split('|')
    if not parts:
        return None
    return parts[0] or None



def load_pending_live_orders(state: dict | None = None, base_dir: str | Path | None = None) -> dict[str, dict]:
    payload = state if state is not None else load_live_inflight_state(base_dir=base_dir)
    orders = payload.get('orders') or {}
    return {
        logical_key: item
        for logical_key, item in orders.items()
        if str(item.get('status') or '').lower() in ACTIVE_LIVE_INFLIGHT_STATUSES
    }



def load_live_order_residue(state: dict | None = None, base_dir: str | Path | None = None) -> dict[str, dict]:
    payload = state if state is not None else load_live_inflight_state(base_dir=base_dir)
    orders = payload.get('orders') or {}
    return {
        logical_key: item
        for logical_key, item in orders.items()
        if str(item.get('status') or '').lower() not in ACTIVE_LIVE_INFLIGHT_STATUSES
    }



def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None



def detect_stale_live_inflight(state: dict, stale_after_seconds: float = 900.0) -> dict:
    now = datetime.now(timezone.utc)
    orders = state.get('orders') or {}
    stale_orders: dict[str, dict] = {}
    for logical_key, item in orders.items():
        updated_at = _parse_iso(item.get('updated_at'))
        if updated_at is None:
            continue
        age_seconds = max((now - updated_at).total_seconds(), 0.0)
        if item.get('status') in {'pending_real_submit', 'adapter_stubbed'} and age_seconds >= stale_after_seconds:
            stale_orders[logical_key] = {
                **item,
                'age_seconds': age_seconds,
                'stale_after_seconds': stale_after_seconds,
            }
    return {
        'count': len(stale_orders),
        'orders': stale_orders,
    }



def escalate_stale_live_inflight(state: dict, stale_after_seconds: float = 900.0, escalate_after_seconds: float = 1800.0) -> dict:
    stale = detect_stale_live_inflight(state, stale_after_seconds=stale_after_seconds)
    escalated_orders: dict[str, dict] = {}
    for logical_key, item in (stale.get('orders') or {}).items():
        if item.get('age_seconds', 0.0) >= escalate_after_seconds:
            escalated_orders[logical_key] = {
                **item,
                'escalate_after_seconds': escalate_after_seconds,
            }
    return {
        'count': len(escalated_orders),
        'orders': escalated_orders,
    }



def release_escalated_live_inflight(state: dict, escalation: dict) -> tuple[dict, list[str]]:
    orders = dict(state.get('orders') or {})
    released = dict(state.get('released') or {})
    released_keys: list[str] = []
    now_iso = utc_now_iso()
    for logical_key, item in (escalation.get('orders') or {}).items():
        if logical_key in orders:
            orders.pop(logical_key, None)
            released[logical_key] = {
                'released_at': now_iso,
                'last_status': item.get('status'),
                'client_order_id': item.get('client_order_id'),
                'reason': 'stale_escalation',
            }
            released_keys.append(logical_key)
    next_state = {
        **state,
        'orders': orders,
        'released': released,
    }
    return next_state, released_keys



def detect_live_release_cooldown(state: dict, cooldown_seconds: float = 900.0) -> dict:
    now = datetime.now(timezone.utc)
    released = state.get('released') or {}
    active: dict[str, dict] = {}
    for logical_key, item in released.items():
        released_at = _parse_iso(item.get('released_at'))
        if released_at is None:
            continue
        age_seconds = max((now - released_at).total_seconds(), 0.0)
        if age_seconds < cooldown_seconds:
            active[logical_key] = {
                **item,
                'age_seconds': age_seconds,
                'cooldown_seconds': cooldown_seconds,
            }
    return {
        'count': len(active),
        'orders': active,
    }



def summarize_live_order_residue(state: dict, active_symbol: str | None = None, stale_after_seconds: float = 1800.0) -> dict:
    now = datetime.now(timezone.utc)
    residue = load_live_order_residue(state)
    partial_fill = 0
    terminal = 0
    other = 0
    stale = 0
    orphan_symbols: set[str] = set()

    for logical_key, item in residue.items():
        status = str(item.get('status') or '').lower()
        symbol = extract_symbol_from_logical_key(logical_key)
        if status in {'partial', 'partially_filled', 'partially-filled', 'open'}:
            partial_fill += 1
        elif status in TERMINAL_LIVE_RESIDUE_STATUSES:
            terminal += 1
        else:
            other += 1

        updated_at = _parse_iso(item.get('updated_at'))
        if updated_at is not None:
            age_seconds = max((now - updated_at).total_seconds(), 0.0)
            if age_seconds >= stale_after_seconds:
                stale += 1

        if symbol and active_symbol and symbol != active_symbol:
            orphan_symbols.add(symbol)

    return {
        'count': len(residue),
        'partial_fill_count': partial_fill,
        'terminal_count': terminal,
        'other_count': other,
        'stale_count': stale,
        'orphan_symbols': sorted(orphan_symbols),
        'needs_manual_attention': partial_fill > 0 and bool(orphan_symbols),
    }



def prune_clearable_live_order_residue(state: dict, active_symbols: set[str] | None = None, older_than_seconds: float = 1800.0) -> tuple[dict, list[str]]:
    now = datetime.now(timezone.utc)
    active_symbols = active_symbols or set()
    orders = dict(state.get('orders') or {})
    removed: list[str] = []

    for logical_key, item in list(orders.items()):
        status = str(item.get('status') or '').lower()
        if status not in TERMINAL_LIVE_RESIDUE_STATUSES:
            continue
        symbol = extract_symbol_from_logical_key(logical_key)
        if symbol in active_symbols:
            continue
        updated_at = _parse_iso(item.get('updated_at'))
        age_seconds = max((now - updated_at).total_seconds(), 0.0) if updated_at is not None else older_than_seconds
        if age_seconds < older_than_seconds:
            continue
        orders.pop(logical_key, None)
        removed.append(logical_key)

    return {
        **state,
        'orders': orders,
    }, removed
