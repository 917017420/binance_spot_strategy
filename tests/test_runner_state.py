from __future__ import annotations

from src.runner_state import compact_runner_state, mark_runner_cycle_started


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
