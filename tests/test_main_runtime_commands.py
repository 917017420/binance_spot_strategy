from __future__ import annotations

import ast
from argparse import Namespace

from src.main import run_runtime_stop


def test_run_runtime_stop_waits_until_runtime_turns_inactive(monkeypatch, capsys):
    statuses = iter(
        [
            {
                'status': 'sleeping',
                'loop_active': True,
                'heartbeat_stale': False,
                'commands': {
                    'observe': 'python3 -m src.main runtime-status',
                    'stop_and_wait': 'python3 -m src.main runtime-stop --reason operator_stop --wait',
                },
            },
            {
                'status': 'stopped',
                'loop_active': False,
                'heartbeat_stale': False,
                'commands': {
                    'observe': 'python3 -m src.main runtime-status',
                    'stop_and_wait': 'python3 -m src.main runtime-stop --reason operator_stop --wait',
                },
            },
        ]
    )

    monkeypatch.setattr('src.main.save_runner_stop_signal', lambda reason: '/tmp/runner_stop.json')
    monkeypatch.setattr('src.main.load_runner_stop_signal', lambda: {'reason': 'operator_stop', 'requested_at': '2026-03-26T00:00:00+00:00'})
    monkeypatch.setattr('src.main.load_runner_state', lambda: {})
    monkeypatch.setattr('src.main.derive_runner_runtime_status', lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr('src.main.time.sleep', lambda _seconds: None)

    rc = run_runtime_stop(
        Namespace(
            reason='operator_stop',
            wait=True,
            timeout_seconds=5.0,
            poll_seconds=0.25,
        )
    )

    assert rc == 0
    payload = ast.literal_eval(capsys.readouterr().out.strip())
    assert payload['ok'] is True
    assert payload['stopped'] is True
    assert payload['timed_out'] is False
    assert payload['runtime']['status'] == 'stopped'


def test_run_runtime_stop_wait_returns_nonzero_for_stale_runtime(monkeypatch, capsys):
    statuses = iter(
        [
            {
                'status': 'running',
                'loop_active': True,
                'heartbeat_stale': False,
                'commands': {
                    'observe': 'python3 -m src.main runtime-status',
                    'stop_and_wait': 'python3 -m src.main runtime-stop --reason operator_stop --wait',
                },
            },
            {
                'status': 'stale',
                'loop_active': True,
                'heartbeat_stale': True,
                'commands': {
                    'observe': 'python3 -m src.main runtime-status',
                    'stop_and_wait': 'python3 -m src.main runtime-stop --reason operator_stop --wait',
                },
            },
        ]
    )

    monkeypatch.setattr('src.main.save_runner_stop_signal', lambda reason: '/tmp/runner_stop.json')
    monkeypatch.setattr('src.main.load_runner_stop_signal', lambda: {'reason': 'operator_stop', 'requested_at': '2026-03-26T00:00:00+00:00'})
    monkeypatch.setattr('src.main.load_runner_state', lambda: {})
    monkeypatch.setattr('src.main.derive_runner_runtime_status', lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr('src.main.time.sleep', lambda _seconds: None)

    rc = run_runtime_stop(
        Namespace(
            reason='operator_stop',
            wait=True,
            timeout_seconds=5.0,
            poll_seconds=0.25,
        )
    )

    assert rc == 2
    payload = ast.literal_eval(capsys.readouterr().out.strip())
    assert payload['ok'] is False
    assert payload['stopped'] is False
    assert payload['timed_out'] is False
    assert payload['runtime']['status'] == 'stale'
