from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, parse_utc_iso, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
RUNNER_STATE_FILE = DEFAULT_EXECUTION_DIR / 'runner_state.json'


def _runner_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir).resolve() if base_dir else DEFAULT_EXECUTION_DIR.resolve()
    ensure_directory(root)
    return (root / RUNNER_STATE_FILE.name).resolve()


def load_runner_state(base_dir: str | Path | None = None) -> dict:
    path = _runner_state_path(base_dir)
    if not path.exists():
        return {
            'last_scan_at': None,
            'last_monitor_at': None,
            'last_cycle_status': None,
            'last_cycle_error': None,
            'last_cycle_stage': 'idle',
            'last_cycle_started_at': None,
            'last_cycle_finished_at': None,
            'last_successful_cycle_at': None,
            'last_heartbeat_at': None,
            'last_heartbeat_status': None,
        }
    state = json.loads(path.read_text(encoding='utf-8'))
    state.setdefault('last_cycle_stage', 'idle')
    state.setdefault('last_cycle_started_at', None)
    state.setdefault('last_cycle_finished_at', None)
    state.setdefault('last_successful_cycle_at', None)
    state.setdefault('last_heartbeat_at', None)
    state.setdefault('last_heartbeat_status', None)
    return state


def compact_runner_state(state: dict) -> dict:
    payload = dict(state)
    for key in ('last_order_refresh_actions', 'last_monitor_messages', 'last_reconcile_actions', 'last_maintenance_messages'):
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = value[-10:]
    error_text = payload.get('last_cycle_error')
    if isinstance(error_text, str) and len(error_text) > 400:
        payload['last_cycle_error'] = error_text[:397] + '...'
    return payload


def runner_cycle_is_stale(state: dict) -> bool:
    if state.get('last_cycle_stage') != 'running':
        return False
    started_at = parse_utc_iso(state.get('last_cycle_started_at'))
    finished_at = parse_utc_iso(state.get('last_cycle_finished_at'))
    if started_at is None:
        return False
    return finished_at is None or finished_at < started_at


def mark_runner_cycle_started(state: dict, *, started_at: str) -> tuple[dict, bool]:
    stale_detected = runner_cycle_is_stale(state)
    next_state = {
        **state,
        'last_cycle_stage': 'running',
        'last_cycle_started_at': started_at,
        'last_heartbeat_at': started_at,
        'last_heartbeat_status': 'running',
    }
    if stale_detected:
        next_state['stale_cycle_recoveries'] = int(state.get('stale_cycle_recoveries', 0) or 0) + 1
        next_state['last_stale_cycle_recovered_at'] = started_at
    return compact_runner_state(next_state), stale_detected


def save_runner_state(state: dict, base_dir: str | Path | None = None) -> Path:
    path = _runner_state_path(base_dir)
    payload = {
        **compact_runner_state(state),
        'updated_at': utc_now_iso(),
    }
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp_path.replace(path)
    return path


def describe_runner_state_file(base_dir: str | Path | None = None) -> dict:
    path = _runner_state_path(base_dir)
    if not path.exists():
        return {
            'path': str(path),
            'exists': False,
        }
    stat = path.stat()
    return {
        'path': str(path),
        'exists': True,
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'inode': getattr(stat, 'st_ino', None),
        'content': path.read_text(encoding='utf-8'),
    }
