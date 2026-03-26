from __future__ import annotations

from src.auto_runner import AutoRunnerCycleResult, run_auto_loop


def test_run_auto_loop_forever_stops_gracefully_during_sleep(monkeypatch):
    state_store = {
        'fuse_open': False,
        'last_recovery_reason': None,
        'last_health_status': 'healthy',
        'next_sleep_seconds': 0.6,
    }
    stop_holder = {'signal': None}
    saved_states: list[dict] = []

    def _load_state():
        return dict(state_store)

    def _save_state(state):
        state_store.clear()
        state_store.update(dict(state))
        saved_states.append(dict(state_store))
        return None

    def _run_cycle(**kwargs):
        state_store['next_sleep_seconds'] = 0.6
        state_store['last_health_status'] = 'healthy'
        state_store['fuse_open'] = False
        state_store['last_recovery_reason'] = None
        state_store['last_heartbeat_status'] = 'ok'
        return AutoRunnerCycleResult(
            ok=True,
            cycle_started_at='2026-03-26T00:00:00+00:00',
            cycle_finished_at='2026-03-26T00:00:01+00:00',
            steps=['cycle-ok'],
        )

    def _sleep(_seconds):
        stop_holder['signal'] = {
            'requested_at': '2026-03-26T00:00:02+00:00',
            'reason': 'operator_stop',
        }

    monkeypatch.setattr('src.auto_runner.load_runner_state', _load_state)
    monkeypatch.setattr('src.auto_runner.save_runner_state', _save_state)
    monkeypatch.setattr('src.auto_runner.run_auto_cycle', _run_cycle)
    monkeypatch.setattr('src.auto_runner.load_runner_stop_signal', lambda: stop_holder['signal'])
    monkeypatch.setattr('src.auto_runner.time.sleep', _sleep)

    result = run_auto_loop(
        config_path='config/strategy.example.yaml',
        env_file='.env',
        action_mode='dry_run',
        cycles=0,
        sleep_seconds=0.6,
        run_forever=True,
        sleep_heartbeat_seconds=0.2,
    )

    assert result.ok is True
    assert result.stop_requested is True
    assert result.stop_reason == 'operator_stop'
    assert len(result.cycles) == 1
    assert any(state.get('last_heartbeat_status') == 'sleeping' for state in saved_states)
    assert state_store['last_loop_mode'] == 'resident'
    assert state_store['last_loop_status'] == 'stopped'
    assert state_store['last_loop_action_mode'] == 'dry_run'
    assert state_store['last_loop_heartbeat_interval_seconds'] == 0.25
    assert state_store['last_loop_exit_reason'] == 'stop_requested'
    assert state_store['last_loop_cycle_count'] == 1
    assert state_store['last_stop_signal_reason'] == 'operator_stop'
    assert state_store['last_heartbeat_status'] == 'stopped'
