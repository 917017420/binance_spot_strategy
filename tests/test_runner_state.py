from __future__ import annotations

import json
from datetime import datetime, timezone

from src.runner_state import clear_runner_stop_signal, compact_runner_state, derive_runner_runtime_status, load_runner_state, load_runner_stop_signal, mark_runner_cycle_started, runner_stop_signal_path, save_runner_state, save_runner_stop_signal


def test_mark_runner_cycle_started_detects_stale_running_cycle():
    state, stale = mark_runner_cycle_started(
        {
            'last_cycle_stage': 'running',
            'last_cycle_started_at': '2026-03-26T00:00:00+00:00',
            'last_cycle_finished_at': '2026-03-25T23:59:00+00:00',
        },
        started_at='2026-03-26T01:00:00+00:00',
    )

    assert stale is True
    assert state['last_cycle_stage'] == 'running'
    assert state['stale_cycle_recoveries'] == 1
    assert state['last_stale_cycle_recovered_at'] == '2026-03-26T01:00:00+00:00'


def test_compact_runner_state_trims_large_recent_lists():
    compacted = compact_runner_state(
        {
            'last_monitor_messages': [f'msg-{index}' for index in range(20)],
            'last_reconcile_actions': [f'action-{index}' for index in range(20)],
            'last_order_refresh_actions': [f'refresh-{index}' for index in range(20)],
        }
    )

    assert compacted['last_monitor_messages'] == [f'msg-{index}' for index in range(10, 20)]
    assert compacted['last_reconcile_actions'] == [f'action-{index}' for index in range(10, 20)]
    assert compacted['last_order_refresh_actions'] == [f'refresh-{index}' for index in range(10, 20)]


def test_runner_stop_signal_round_trip(tmp_path):
    assert load_runner_stop_signal(base_dir=tmp_path) is None

    path = save_runner_stop_signal('operator_stop', base_dir=tmp_path)
    signal = load_runner_stop_signal(base_dir=tmp_path)

    assert path == runner_stop_signal_path(base_dir=tmp_path)
    assert signal is not None
    assert signal['path'] == str(path)
    assert signal['reason'] == 'operator_stop'
    assert signal['requested_at']
    assert clear_runner_stop_signal(base_dir=tmp_path) is True
    assert clear_runner_stop_signal(base_dir=tmp_path) is False
    assert load_runner_stop_signal(base_dir=tmp_path) is None


def test_derive_runner_runtime_status_reports_sleeping_resident_loop():
    runtime = derive_runner_runtime_status(
        {
            'last_loop_mode': 'resident',
            'last_loop_status': 'sleeping',
            'last_loop_action_mode': 'live',
            'last_loop_started_at': '2026-03-26T00:00:00+00:00',
            'last_successful_cycle_at': '2026-03-26T00:00:05+00:00',
            'last_loop_sleep_until_at': '2026-03-26T00:01:00+00:00',
            'last_loop_sleep_remaining_seconds': 42.0,
            'last_loop_heartbeat_interval_seconds': 5.0,
            'last_heartbeat_at': '2026-03-26T00:00:10+00:00',
        },
        now=datetime(2026, 3, 26, 0, 0, 12, tzinfo=timezone.utc),
    )

    assert runtime['status'] == 'sleeping'
    assert runtime['mode'] == 'resident'
    assert runtime['loop_active'] is True
    assert runtime['heartbeat_stale'] is False
    assert runtime['last_loop_action_mode'] == 'live'
    assert runtime['last_loop_sleep_remaining_seconds'] == 48.0
    assert runtime['last_successful_cycle_at'] == '2026-03-26T00:00:05+00:00'
    assert runtime['operator_hint'] == 'Sleep heartbeat updates should continue while the loop is idle between cycles.'
    assert runtime['commands']['observe'] == 'python3 -m src.main runtime-status'
    assert runtime['commands']['stop_and_wait'] == 'python3 -m src.main runtime-stop --reason operator_stop --wait'


def test_derive_runner_runtime_status_detects_stale_resident_loop_and_stop_blocker():
    runtime = derive_runner_runtime_status(
        {
            'last_loop_mode': 'resident',
            'last_loop_status': 'sleeping',
            'last_loop_started_at': '2026-03-26T00:00:00+00:00',
            'last_loop_heartbeat_interval_seconds': 5.0,
            'last_heartbeat_at': '2026-03-26T00:00:00+00:00',
        },
        stop_signal={'requested_at': '2026-03-26T00:01:00+00:00', 'reason': 'operator_stop'},
        now=datetime(2026, 3, 26, 0, 1, 0, tzinfo=timezone.utc),
    )

    assert runtime['status'] == 'stale'
    assert runtime['heartbeat_stale'] is True
    assert runtime['stop_signal_present'] is True
    assert runtime['stop_signal_age_seconds'] == 0.0
    assert runtime['recommended_command'] == 'python3 -m src.main control-plane-brief'

    blocked_runtime = derive_runner_runtime_status(
        {
            'last_loop_mode': 'resident',
            'last_loop_status': 'stopped',
            'last_loop_started_at': '2026-03-26T00:00:00+00:00',
            'last_loop_finished_at': '2026-03-26T00:00:30+00:00',
            'last_heartbeat_at': '2026-03-26T00:00:30+00:00',
        },
        stop_signal={'requested_at': '2026-03-26T00:01:00+00:00', 'reason': 'operator_stop'},
        now=datetime(2026, 3, 26, 0, 1, 0, tzinfo=timezone.utc),
    )

    assert blocked_runtime['status'] == 'stopped'
    assert blocked_runtime['start_blocked_by_stop_signal'] is True
    assert blocked_runtime['stop_signal_age_seconds'] == 0.0
    assert blocked_runtime['operator_hint'] == 'Clear the stop signal before the next supervised runtime start.'
    assert blocked_runtime['recommended_command'] == 'python3 -m src.main clear-runner-stop'


def test_load_runner_state_recovers_salvageable_object_with_trailing_garbage(tmp_path):
    state_path = tmp_path / 'runner_state.json'
    state_path.write_text('{"last_loop_mode":"resident","last_loop_cycle_count":3}{"dangling":', encoding='utf-8')

    state = load_runner_state(base_dir=tmp_path)

    assert state['last_loop_mode'] == 'resident'
    assert state['last_loop_cycle_count'] == 3
    recovery = state.get('runner_state_file_recovery')
    assert isinstance(recovery, dict)
    assert recovery.get('recovered') is True
    assert recovery.get('strategy') == 'salvaged_prefix_object'


def test_load_runner_state_falls_back_to_defaults_for_irrecoverable_json(tmp_path):
    state_path = tmp_path / 'runner_state.json'
    state_path.write_text('this is not json', encoding='utf-8')

    state = load_runner_state(base_dir=tmp_path)

    assert state['last_cycle_stage'] == 'idle'
    assert state['last_loop_status'] == 'idle'
    recovery = state.get('runner_state_file_recovery')
    assert isinstance(recovery, dict)
    assert recovery.get('recovered') is True
    assert recovery.get('strategy') == 'defaults'


def test_save_runner_state_round_trip_writes_valid_json(tmp_path):
    saved_path = save_runner_state(
        {
            'last_loop_mode': 'resident',
            'last_loop_cycle_count': 5,
            'last_monitor_messages': [f'msg-{idx}' for idx in range(14)],
        },
        base_dir=tmp_path,
    )

    payload = json.loads(saved_path.read_text(encoding='utf-8'))
    loaded = load_runner_state(base_dir=tmp_path)

    assert payload['last_loop_mode'] == 'resident'
    assert payload['last_loop_cycle_count'] == 5
    assert payload['last_monitor_messages'] == [f'msg-{idx}' for idx in range(4, 14)]
    assert isinstance(payload.get('updated_at'), str) and payload['updated_at']
    assert loaded['last_loop_mode'] == 'resident'
    assert loaded['last_loop_cycle_count'] == 5
    assert loaded['last_monitor_messages'] == [f'msg-{idx}' for idx in range(4, 14)]
    assert not list(tmp_path.glob('*.tmp'))
