from __future__ import annotations

from src.control_plane_reconcile import reconcile_control_plane_state
from src.live_inflight_state import load_live_inflight_state, save_live_inflight_state
from src.models import Position
from src.positions_store import save_positions
from src.live_submit_state import load_live_submit_state, save_live_submit_state
from src.runner_state import load_runner_state, save_runner_state
from src.utils import utc_now_iso


def _simulated_active_position(symbol: str, position_id: str) -> Position:
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
        entry_execution_stage='manual_confirmation',
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
        tags=['manual_confirmed', 'paper', 'position_initialized'],
    )


def test_control_plane_reconcile_clears_stale_summary_without_unresolved_inflight(tmp_path):
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'SOL/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        },
        base_dir=tmp_path,
    )
    save_live_inflight_state(
        {
            'orders': {
                'SOL/USDT|live|armed': {
                    'status': 'filled',
                    'symbol': 'SOL/USDT',
                    'client_order_id': 'cid-filled',
                    'updated_at': utc_now_iso(),
                }
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-filled',
            'last_submit_status': 'filled',
            'last_symbol': 'SOL/USDT',
            'last_request': {'symbol': 'SOL/USDT'},
            'last_response': {'status': 'filled'},
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    runner_state = load_runner_state(base_dir=tmp_path)
    inflight_state = load_live_inflight_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'idle'
    assert any(action.startswith('RUNNER_ACTIVE_TRADE_CLEARED') for action in result.actions)
    assert runner_state['last_active_trade_status'] == 'idle'
    assert runner_state['last_active_trade_symbol'] is None
    assert runner_state['last_active_trade_lock_reason'] is None
    assert inflight_state['orders']['SOL/USDT|live|armed']['status'] == 'filled'


def test_control_plane_reconcile_ignores_simulated_positions_when_unlocking_live_plane(tmp_path):
    save_positions([_simulated_active_position('SOL/USDT', 'pos-paper-sol')], base_dir=tmp_path)
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'SOL/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        },
        base_dir=tmp_path,
    )
    save_live_inflight_state({'orders': {}, 'released': {}, 'quarantined': {}}, base_dir=tmp_path)
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-paper',
            'last_submit_status': 'paper_submitted',
            'last_symbol': 'SOL/USDT',
            'last_request': {'symbol': 'SOL/USDT'},
            'last_response': {'status': 'paper_submitted'},
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    runner_state = load_runner_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'idle'
    assert any(action.startswith('RUNNER_ACTIVE_TRADE_CLEARED') for action in result.actions)
    assert runner_state['last_active_trade_status'] == 'idle'
    assert runner_state['last_active_trade_symbol'] is None
    assert runner_state['last_active_trade_lock_reason'] is None


def test_control_plane_reconcile_syncs_runner_state_to_active_live_position(tmp_path):
    save_positions([_simulated_active_position('ADA/USDT', 'pos-live-ada').model_copy(update={
        'entry_execution_stage': 'live_fill_reconciled',
        'market_state_at_entry': 'LIVE_RECONCILED',
        'tags': ['live_fill_reconciled', 'truth_domain_live'],
    })], base_dir=tmp_path)
    save_live_inflight_state({'orders': {}, 'released': {}, 'quarantined': {}}, base_dir=tmp_path)
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-live-ada',
            'last_submit_status': 'closed',
            'last_submit_side': 'buy',
            'last_symbol': 'ADA/USDT',
            'last_request': {'symbol': 'ADA/USDT', 'side': 'buy'},
            'last_response': {'status': 'closed'},
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    runner_state = load_runner_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'locked'
    assert any(action.startswith('RUNNER_ACTIVE_TRADE_SYNCED') for action in result.actions)
    assert runner_state['last_active_trade_status'] == 'locked'
    assert runner_state['last_active_trade_symbol'] == 'ADA/USDT'
    assert runner_state['last_active_trade_stage'] == 'position_open'
    assert runner_state['last_active_trade_lock_reason'] == 'active_open_position_exists'


def test_control_plane_reconcile_archives_stale_simulated_active_positions(tmp_path):
    stale_position = _simulated_active_position('XRP/USDT', 'pos-paper-xrp').model_copy(update={
        'entry_time': '2026-03-20T00:00:00+00:00',
    })
    save_positions([stale_position], base_dir=tmp_path)
    save_live_inflight_state({'orders': {}, 'released': {}, 'quarantined': {}}, base_dir=tmp_path)
    save_live_submit_state({}, base_dir=tmp_path)

    result = reconcile_control_plane_state(base_dir=tmp_path)

    assert result.ok is True
    assert any(action.startswith('SIMULATED_POSITION_ARCHIVED position_id=pos-paper-xrp') for action in result.actions)
    assert (tmp_path / 'positions.json').read_text(encoding='utf-8').strip() == '[]'
    archive_path = tmp_path / 'archive' / 'positions_archive.jsonl'
    assert archive_path.exists() is True
    assert 'pos-paper-xrp' in archive_path.read_text(encoding='utf-8')


def test_control_plane_reconcile_auto_archives_terminal_sell_submit_after_true_exit(tmp_path):
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'ADA/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        },
        base_dir=tmp_path,
    )
    save_live_inflight_state({'orders': {}, 'released': {}, 'quarantined': {}}, base_dir=tmp_path)
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-ada-exit',
            'last_submit_status': 'closed',
            'last_submit_side': 'sell',
            'last_symbol': 'ADA/USDT',
            'last_request': {'symbol': 'ADA/USDT', 'side': 'sell'},
            'last_response': {'status': 'closed'},
            'last_action_intent': 'SELL_EXIT',
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    runner_state = load_runner_state(base_dir=tmp_path)
    submit_state = load_live_submit_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'idle'
    assert any(action.startswith('RUNNER_ACTIVE_TRADE_CLEARED') for action in result.actions)
    assert any(action.startswith('LIVE_SUBMIT_STATE_AUTO_ARCHIVED') for action in result.actions)
    assert runner_state['last_active_trade_status'] == 'idle'
    assert runner_state['last_active_trade_symbol'] is None
    assert runner_state['last_active_trade_lock_reason'] is None
    assert submit_state['last_client_order_id'] is None
    assert submit_state['last_submit_status'] is None
    assert submit_state['last_submit_side'] is None
    assert submit_state['last_symbol'] is None
    assert submit_state['last_request'] is None
    assert submit_state['last_response'] is None
    assert submit_state['last_action_intent'] is None
    assert submit_state['archived_last_submit']['archive_reason'] == 'post_exit_control_plane_cleanup'
    assert submit_state['archived_last_submit']['last_submit_status'] == 'closed'
    assert submit_state['archived_last_submit']['last_submit_side'] == 'sell'


def test_control_plane_reconcile_releases_local_preview_pending_inflight_residue(tmp_path):
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'TRX/USDT',
            'last_active_trade_stage': 'submit_pending',
            'last_active_trade_lock_reason': 'live_submit_inflight_pending',
        },
        base_dir=tmp_path,
    )
    save_live_inflight_state(
        {
            'orders': {
                'TRX/USDT|live|armed': {
                    'status': 'pending_real_submit',
                    'symbol': 'TRX/USDT',
                    'client_order_id': 'cid-trx-preview',
                    'updated_at': utc_now_iso(),
                },
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-trx-preview',
            'last_submit_status': 'adapter_stubbed',
            'last_submit_side': 'buy',
            'last_symbol': 'TRX/USDT',
            'last_request': {'symbol': 'TRX/USDT', 'side': 'buy'},
            'last_exchange_params': {
                'call_preview': {
                    'intent': {
                        'submit_enabled': False,
                        'mode': 'preview_only',
                        'reason': 'submit disabled',
                    }
                }
            },
            'last_response': {
                'status': 'pending_real_submit',
                'raw': {'submitMode': 'stubbed', 'submitEnabled': False},
            },
            'last_action_intent': 'BUY_ENTRY',
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    runner_state = load_runner_state(base_dir=tmp_path)
    inflight_state = load_live_inflight_state(base_dir=tmp_path)
    submit_state = load_live_submit_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'idle'
    assert any(action.startswith('LIVE_INFLIGHT_RECOVERY_RELEASED') for action in result.actions)
    assert any(action.startswith('RUNNER_ACTIVE_TRADE_CLEARED') for action in result.actions)
    assert inflight_state['orders'] == {}
    assert runner_state['last_active_trade_status'] == 'idle'
    assert runner_state['last_active_trade_symbol'] is None
    assert runner_state['last_active_trade_lock_reason'] is None
    assert submit_state['archived_last_submit']['archive_reason'] == 'local_preview_submit_residue'
    assert submit_state['last_submit_status'] is None
    assert submit_state['last_symbol'] is None


def test_control_plane_reconcile_keeps_pending_inflight_when_submit_supports_live_order(tmp_path):
    save_live_inflight_state(
        {
            'orders': {
                'BTC/USDT|live|armed': {
                    'status': 'pending_real_submit',
                    'symbol': 'BTC/USDT',
                    'client_order_id': 'cid-btc-live',
                    'updated_at': utc_now_iso(),
                },
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )
    save_live_submit_state(
        {
            'last_client_order_id': 'cid-btc-live',
            'last_submit_status': 'submitted',
            'last_submit_side': 'buy',
            'last_symbol': 'BTC/USDT',
            'last_request': {'symbol': 'BTC/USDT', 'side': 'buy'},
            'last_exchange_params': {
                'call_preview': {
                    'intent': {
                        'submit_enabled': True,
                        'mode': 'live_submit',
                        'reason': 'live enabled',
                    }
                }
            },
            'last_response': {
                'status': 'submitted',
                'raw': {'submitMode': 'live_submit', 'submitEnabled': True},
            },
            'last_action_intent': 'BUY_ENTRY',
            'last_error': None,
        },
        base_dir=tmp_path,
    )

    result = reconcile_control_plane_state(base_dir=tmp_path)
    inflight_state = load_live_inflight_state(base_dir=tmp_path)

    assert result.ok is True
    assert result.after_status == 'locked'
    assert not any(action.startswith('LIVE_INFLIGHT_RECOVERY_RELEASED') for action in result.actions)
    assert 'BTC/USDT|live|armed' in inflight_state['orders']
