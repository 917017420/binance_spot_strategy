from __future__ import annotations

from types import SimpleNamespace

from src.control_plane_brief import format_control_plane_brief
from src.live_execution_snapshot import build_live_execution_snapshot
from src.live_fill_reconcile import apply_live_order_fact
from src.models import Position
from src.positions_store import save_positions
from src.runner_state import save_runner_state, save_runner_stop_signal
from src.utils import utc_now_iso


def _active_position(symbol: str, position_id: str, *, tags: list[str], entry_execution_stage: str = 'armed') -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status='open',
        entry_time=now,
        entry_price=100.0,
        entry_signal='BUY_READY_BREAKOUT',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage=entry_execution_stage,
        entry_attention_level='high',
        initial_position_size_pct=5.0,
        remaining_position_size_pct=5.0,
        entry_quote_amount=50.0,
        entry_base_amount=0.5,
        initial_stop_price=96.0,
        active_stop_price=96.0,
        suggested_stop_price=96.0,
        risk_budget='normal',
        market_state_at_entry='NEUTRAL_MIXED',
        tp1_price=106.0,
        tp2_price=110.0,
        highest_price_since_entry=100.0,
        last_price=100.0,
        notes=[],
        tags=tags,
    )


def test_live_execution_snapshot_reports_active_position_management_after_closed_buy_fill(tmp_path):
    request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-buy',
        metadata={'requested_position_size_pct': 5.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-ada-buy',
        client_order_id='cid-ada-buy',
        status='closed',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'closed', 'average': 0.8},
    )
    apply_live_order_fact(response, request, base_dir=tmp_path)

    snapshot = build_live_execution_snapshot(base_dir=tmp_path)
    summary = snapshot.summary
    submit_summary = summary['live_submit_state']['summary']

    assert snapshot.status == 'locked'
    assert summary['current_state']['active_symbol'] == 'ADA/USDT'
    assert summary['current_state']['active_stage'] == 'position_open'
    assert summary['current_state']['active_position_under_management'] is True
    assert summary['current_state']['primary_reason'] == 'active_open_position_exists'
    assert submit_summary['classification'] == 'active_position_management'
    assert submit_summary['order_terminality'] == 'terminal'
    assert submit_summary['flow_terminality'] == 'active_position'


def test_control_plane_brief_highlights_active_position_management_after_buy_fill(tmp_path):
    request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-brief',
        metadata={'requested_position_size_pct': 5.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-ada-brief',
        client_order_id='cid-ada-brief',
        status='closed',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'closed', 'average': 0.8},
    )
    apply_live_order_fact(response, request, base_dir=tmp_path)

    brief = format_control_plane_brief(base_dir=tmp_path)

    assert '- status: locked' in brief
    assert '- active_symbol: ADA/USDT' in brief
    assert '- active_position_under_management: True' in brief
    assert '- submit_state_classification=active_position_management' in brief
    assert '- submit_flow_terminality=active_position' in brief


def test_live_execution_snapshot_excludes_simulated_positions_from_operational_view(tmp_path):
    save_positions(
        [
            _active_position('ETH/USDT', 'pos-sim-eth', tags=['manual_confirmed', 'dry_run', 'position_initialized']),
            _active_position('ADA/USDT', 'pos-live-ada', tags=['live_fill_reconciled', 'truth_domain_live'], entry_execution_stage='live_fill_reconciled'),
        ],
        base_dir=tmp_path,
    )

    snapshot = build_live_execution_snapshot(base_dir=tmp_path)
    single_active_trade = snapshot.summary['single_active_trade']

    assert single_active_trade['active_symbol'] == 'ADA/USDT'
    assert [item['symbol'] for item in single_active_trade['observed_positions']] == ['ADA/USDT']
    assert single_active_trade['excluded_simulation_positions_count'] == 1


def test_live_execution_snapshot_includes_resident_runtime_summary(tmp_path):
    save_runner_state(
        {
            'last_loop_mode': 'resident',
            'last_loop_status': 'sleeping',
            'last_loop_action_mode': 'live',
            'last_loop_started_at': '2026-03-26T00:00:00+00:00',
            'last_successful_cycle_at': '2026-03-26T00:00:05+00:00',
            'last_loop_cycle_count': 7,
            'last_loop_sleep_until_at': '2026-03-26T00:10:00+00:00',
            'last_loop_sleep_remaining_seconds': 25.0,
            'last_loop_heartbeat_interval_seconds': 5.0,
            'last_heartbeat_at': utc_now_iso(),
            'last_heartbeat_status': 'sleeping',
        },
        base_dir=tmp_path,
    )
    save_runner_stop_signal('nightly_maintenance', base_dir=tmp_path)

    snapshot = build_live_execution_snapshot(base_dir=tmp_path)
    runtime = snapshot.summary['runtime']

    assert runtime['mode'] == 'resident'
    assert runtime['status'] == 'sleeping'
    assert runtime['last_loop_action_mode'] == 'live'
    assert runtime['stop_signal_present'] is True
    assert runtime['commands']['start'] == 'python3 -m src.main runtime-start --action-mode dry_run'
    assert runtime['commands']['stop_and_wait'] == 'python3 -m src.main runtime-stop --reason operator_stop --wait'
    assert snapshot.summary['next_action_plan']['code'] == 'resident_runtime_already_active'
    assert snapshot.summary['next_action_plan']['recommended_command'] == 'python3 -m src.main runtime-status'

    brief = format_control_plane_brief(base_dir=tmp_path)
    assert 'RUNTIME' in brief
    assert '- status=sleeping' in brief
    assert '- action_mode=live' in brief
    assert '- stop_signal_present=True' in brief
    assert '- runtime_stop_and_wait: python3 -m src.main runtime-stop --reason operator_stop --wait' in brief


def test_live_execution_snapshot_prefers_clearing_stop_signal_when_runtime_is_blocked(tmp_path):
    save_runner_state(
        {
            'last_loop_mode': 'resident',
            'last_loop_status': 'stopped',
            'last_loop_started_at': '2026-03-26T00:00:00+00:00',
            'last_loop_finished_at': '2026-03-26T00:00:10+00:00',
            'last_heartbeat_at': '2026-03-26T00:00:10+00:00',
        },
        base_dir=tmp_path,
    )
    save_runner_stop_signal('nightly_maintenance', base_dir=tmp_path)

    snapshot = build_live_execution_snapshot(base_dir=tmp_path)

    assert snapshot.summary['runtime']['start_blocked_by_stop_signal'] is True
    assert snapshot.summary['next_action_plan']['code'] == 'clear_stop_signal_before_restart'
    assert snapshot.summary['next_action_plan']['recommended_command'] == 'python3 -m src.main clear-runner-stop'


def test_live_execution_snapshot_surfaces_recent_recovery_actions(tmp_path):
    save_runner_state(
        {
            'last_reconcile_actions': [
                'LIVE_INFLIGHT_RECOVERY_RELEASED count=1 keys=[TRX/USDT|live|armed] reason=local_preview_submit_residue',
                'LIVE_SUBMIT_STATE_ARCHIVED_LOCAL_PREVIEW status=adapter_stubbed symbol=TRX/USDT',
            ],
        },
        base_dir=tmp_path,
    )

    snapshot = build_live_execution_snapshot(base_dir=tmp_path)
    historical_residue = snapshot.summary['historical_residue']

    assert historical_residue['recent_reconcile_actions'][0].startswith('LIVE_INFLIGHT_RECOVERY_RELEASED')
    assert historical_residue['recent_reconcile_actions'][1].startswith('LIVE_SUBMIT_STATE_ARCHIVED_LOCAL_PREVIEW')
    assert historical_residue['recent_recovery_actions'][0].startswith('LIVE_INFLIGHT_RECOVERY_RELEASED')
