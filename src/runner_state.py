from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .utils import ensure_directory, parse_utc_iso, seconds_since_iso, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
RUNNER_STATE_FILE = DEFAULT_EXECUTION_DIR / 'runner_state.json'
RUNNER_STOP_FILE = DEFAULT_EXECUTION_DIR / 'runner_stop.json'
RUNNER_STATE_RECOVERY_KEY = 'runner_state_file_recovery'


def _default_runner_state() -> dict:
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
        'last_loop_mode': None,
        'last_loop_status': 'idle',
        'last_loop_action_mode': None,
        'last_loop_started_at': None,
        'last_loop_finished_at': None,
        'last_loop_exit_reason': None,
        'last_loop_cycle_target': None,
        'last_loop_cycle_count': 0,
        'last_loop_heartbeat_interval_seconds': 5.0,
        'last_loop_sleep_started_at': None,
        'last_loop_sleep_until_at': None,
        'last_loop_sleep_seconds': 0.0,
        'last_loop_sleep_remaining_seconds': 0.0,
        'last_stop_signal_at': None,
        'last_stop_signal_reason': None,
    }


def _runner_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir).resolve() if base_dir else DEFAULT_EXECUTION_DIR.resolve()
    ensure_directory(root)
    return (root / RUNNER_STATE_FILE.name).resolve()


def runner_stop_signal_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir).resolve() if base_dir else DEFAULT_EXECUTION_DIR.resolve()
    ensure_directory(root)
    return (root / RUNNER_STOP_FILE.name).resolve()


def _build_runner_state_recovery(
    *,
    path: Path,
    strategy: str,
    reason: str,
    error: Exception | str | None = None,
) -> dict:
    message = str(error) if error is not None else None
    if isinstance(message, str) and len(message) > 240:
        message = message[:237] + '...'
    recovery = {
        'recovered': True,
        'strategy': strategy,
        'reason': reason,
        'path': str(path),
        'recovered_at': utc_now_iso(),
    }
    if message:
        recovery['error'] = message
    return recovery


def _merge_runner_state(payload: dict | None = None, *, recovery: dict | None = None) -> dict:
    merged = {
        **_default_runner_state(),
        **(payload or {}),
    }
    if recovery is not None:
        merged[RUNNER_STATE_RECOVERY_KEY] = recovery
    return merged


def _salvage_runner_state_payload(raw_text: str) -> tuple[dict | None, str]:
    stripped = raw_text.lstrip()
    if not stripped:
        return None, 'empty_file'
    decoder = json.JSONDecoder()
    try:
        payload, offset = decoder.raw_decode(stripped)
    except json.JSONDecodeError:
        return None, 'json_decode_error'
    if not isinstance(payload, dict):
        return None, 'top_level_not_object'
    trailing = stripped[offset:].strip()
    if trailing:
        return payload, 'trailing_data'
    return payload, 'parsed_prefix_object'


def _atomic_write_json(path: Path, payload: dict) -> None:
    ensure_directory(path.parent)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f'.{path.name}.',
        suffix='.tmp',
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def load_runner_state(base_dir: str | Path | None = None) -> dict:
    path = _runner_state_path(base_dir)
    if not path.exists():
        return _default_runner_state()
    try:
        raw_text = path.read_text(encoding='utf-8')
    except Exception as exc:
        return _merge_runner_state(
            recovery=_build_runner_state_recovery(
                path=path,
                strategy='defaults',
                reason='state_file_read_failed',
                error=exc,
            )
        )

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        salvage_payload, salvage_reason = _salvage_runner_state_payload(raw_text)
        if salvage_payload is not None:
            return _merge_runner_state(
                salvage_payload,
                recovery=_build_runner_state_recovery(
                    path=path,
                    strategy='salvaged_prefix_object',
                    reason=salvage_reason,
                    error=exc,
                ),
            )
        return _merge_runner_state(
            recovery=_build_runner_state_recovery(
                path=path,
                strategy='defaults',
                reason=salvage_reason,
                error=exc,
            )
        )
    except Exception as exc:
        return _merge_runner_state(
            recovery=_build_runner_state_recovery(
                path=path,
                strategy='defaults',
                reason='json_parse_failed',
                error=exc,
            )
        )

    if isinstance(payload, dict):
        return _merge_runner_state(payload)
    return _merge_runner_state(
        recovery=_build_runner_state_recovery(
            path=path,
            strategy='defaults',
            reason='top_level_not_object',
        )
    )


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


def _coerce_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _seconds_until_iso(value: str | None, *, now: datetime | None = None) -> float | None:
    target = parse_utc_iso(value)
    if target is None:
        return None
    current = now or datetime.now(target.tzinfo)
    remaining = (target - current).total_seconds()
    return max(remaining, 0.0)


def derive_runner_runtime_status(
    state: dict,
    *,
    stop_signal: dict | None = None,
    now: datetime | None = None,
) -> dict:
    loop_mode = str(state.get('last_loop_mode') or 'idle')
    loop_status = str(state.get('last_loop_status') or 'idle')
    heartbeat_interval_seconds = max(_coerce_float(state.get('last_loop_heartbeat_interval_seconds'), 5.0) or 5.0, 0.25)
    heartbeat_age_seconds = seconds_since_iso(state.get('last_heartbeat_at'), now=now)

    active_statuses = {'running', 'sleeping', 'stop_requested'}
    if loop_status == 'running':
        heartbeat_timeout_seconds = 1800.0
    elif loop_status in {'sleeping', 'stop_requested'}:
        heartbeat_timeout_seconds = max(heartbeat_interval_seconds * 3.0, 2.0)
    else:
        heartbeat_timeout_seconds = None

    heartbeat_stale = bool(
        loop_status in active_statuses
        and heartbeat_age_seconds is not None
        and heartbeat_timeout_seconds is not None
        and heartbeat_age_seconds > heartbeat_timeout_seconds
    )

    if heartbeat_stale:
        runtime_status = 'stale'
    elif loop_status == 'sleeping':
        runtime_status = 'sleeping'
    elif loop_status == 'stop_requested':
        runtime_status = 'stopping'
    elif loop_status == 'running':
        runtime_status = 'running'
    elif loop_status == 'stopped':
        runtime_status = 'stopped'
    elif state.get('last_loop_started_at'):
        runtime_status = 'idle'
    else:
        runtime_status = 'never_started'

    stop_signal_present = stop_signal is not None
    stop_signal_reason = None
    stop_signal_requested_at = None
    if stop_signal_present:
        stop_signal_reason = stop_signal.get('reason') or 'manual_stop'
        stop_signal_requested_at = stop_signal.get('requested_at')
    stop_signal_requested_at = stop_signal_requested_at or state.get('last_stop_signal_at')
    stop_signal_age_seconds = seconds_since_iso(stop_signal_requested_at, now=now)

    sleep_until_at = state.get('last_loop_sleep_until_at')
    persisted_sleep_remaining_seconds = _coerce_float(state.get('last_loop_sleep_remaining_seconds'), 0.0) or 0.0
    derived_sleep_remaining_seconds = _seconds_until_iso(sleep_until_at, now=now)
    sleep_remaining_seconds = (
        derived_sleep_remaining_seconds
        if derived_sleep_remaining_seconds is not None
        else persisted_sleep_remaining_seconds
    )

    loop_active = runtime_status in {'running', 'sleeping', 'stopping'}
    start_blocked_by_stop_signal = stop_signal_present and not loop_active and not heartbeat_stale

    if start_blocked_by_stop_signal:
        recommended_command = 'python3 -m src.main clear-runner-stop'
        summary = f"Resident runtime start is blocked by stop signal ({stop_signal_reason or 'manual_stop'})."
        operator_hint = 'Clear the stop signal before the next supervised runtime start.'
    elif runtime_status == 'stale':
        recommended_command = 'python3 -m src.main control-plane-brief'
        summary = 'Resident runtime heartbeat looks stale; inspect state before restarting live operations.'
        operator_hint = 'A stale heartbeat usually means the resident loop stopped reporting during sleep or cycle execution.'
    elif loop_active:
        if runtime_status == 'stopping':
            recommended_command = 'python3 -m src.main runtime-stop --reason operator_stop --wait'
            summary = 'Resident runtime is draining toward a graceful stop.'
            operator_hint = 'Wait for the current cycle boundary or sleep heartbeat before forcing any restart decision.'
        elif runtime_status == 'sleeping':
            recommended_command = 'python3 -m src.main runtime-status'
            summary = f"Resident runtime is sleeping between cycles; next wake is scheduled for {sleep_until_at or 'unknown'}."
            operator_hint = 'Sleep heartbeat updates should continue while the loop is idle between cycles.'
        else:
            recommended_command = 'python3 -m src.main runtime-status'
            summary = 'Resident runtime is actively cycling under supervision.'
            operator_hint = 'Observe control-plane ownership before launching any manual cycle or intervention.'
    else:
        recommended_command = 'python3 -m src.main runtime-start --action-mode dry_run'
        summary = 'Resident runtime is not active; start the supervised loop when ready.'
        operator_hint = 'If live control-plane state is already owned by an active position, keep monitoring rather than forcing new entry.'

    return {
        'mode': loop_mode,
        'loop_status': loop_status,
        'status': runtime_status,
        'loop_active': loop_active,
        'last_loop_action_mode': state.get('last_loop_action_mode'),
        'heartbeat_interval_seconds': heartbeat_interval_seconds,
        'heartbeat_age_seconds': heartbeat_age_seconds,
        'heartbeat_timeout_seconds': heartbeat_timeout_seconds,
        'heartbeat_stale': heartbeat_stale,
        'stop_signal_present': stop_signal_present,
        'stop_signal_reason': stop_signal_reason or state.get('last_stop_signal_reason'),
        'stop_signal_requested_at': stop_signal_requested_at,
        'stop_signal_age_seconds': stop_signal_age_seconds,
        'start_blocked_by_stop_signal': start_blocked_by_stop_signal,
        'last_loop_started_at': state.get('last_loop_started_at'),
        'last_loop_finished_at': state.get('last_loop_finished_at'),
        'last_loop_exit_reason': state.get('last_loop_exit_reason'),
        'last_loop_cycle_target': state.get('last_loop_cycle_target'),
        'last_loop_cycle_count': int(state.get('last_loop_cycle_count', 0) or 0),
        'last_loop_sleep_until_at': sleep_until_at,
        'last_loop_sleep_remaining_seconds': sleep_remaining_seconds,
        'last_successful_cycle_at': state.get('last_successful_cycle_at'),
        'last_heartbeat_at': state.get('last_heartbeat_at'),
        'last_heartbeat_status': state.get('last_heartbeat_status'),
        'summary': summary,
        'operator_hint': operator_hint,
        'recommended_command': recommended_command,
        'commands': {
            'start': 'python3 -m src.main runtime-start --action-mode dry_run',
            'stop': 'python3 -m src.main runtime-stop --reason operator_stop',
            'stop_and_wait': 'python3 -m src.main runtime-stop --reason operator_stop --wait',
            'observe': 'python3 -m src.main runtime-status',
            'control_plane': 'python3 -m src.main control-plane-brief',
            'clear_stop': 'python3 -m src.main clear-runner-stop',
        },
    }


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
    _atomic_write_json(path, payload)
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


def load_runner_stop_signal(base_dir: str | Path | None = None) -> dict | None:
    path = runner_stop_signal_path(base_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        'path': str(path),
        'requested_at': payload.get('requested_at'),
        'reason': payload.get('reason') or 'manual_stop',
    }


def save_runner_stop_signal(reason: str = 'manual_stop', *, base_dir: str | Path | None = None) -> Path:
    path = runner_stop_signal_path(base_dir)
    payload = {
        'requested_at': utc_now_iso(),
        'reason': reason or 'manual_stop',
    }
    _atomic_write_json(path, payload)
    return path


def clear_runner_stop_signal(base_dir: str | Path | None = None) -> bool:
    path = runner_stop_signal_path(base_dir)
    if not path.exists():
        return False
    path.unlink()
    return True
