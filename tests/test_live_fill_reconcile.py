from __future__ import annotations

import pytest
from types import SimpleNamespace

from src.live_fill_reconcile import apply_live_order_fact
from src.single_active_trade_state import build_single_active_trade_state
from src.live_submit_state import load_live_submit_state, summarize_live_submit_state
from src.live_inflight_state import load_live_inflight_state
from src.models import Position
from src.positions_store import classify_position_truth_domain, load_active_positions, load_live_active_positions, load_positions, save_positions
from src.runner_state import load_runner_state, save_runner_state
from src.utils import utc_now_iso


def _simulated_active_position(symbol: str, position_id: str) -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status='open',
        entry_time=now,
        entry_price=190.0,
        entry_signal='BUY_READY_BREAKOUT',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='manual_confirmation',
        entry_attention_level='high',
        initial_position_size_pct=4.0,
        remaining_position_size_pct=4.0,
        entry_quote_amount=76.0,
        entry_base_amount=0.4,
        initial_stop_price=182.4,
        active_stop_price=182.4,
        suggested_stop_price=182.4,
        risk_budget='normal',
        market_state_at_entry='NEUTRAL_MIXED',
        tp1_price=201.4,
        tp2_price=209.0,
        highest_price_since_entry=190.0,
        last_price=190.0,
        notes=[],
        tags=['manual_confirmed', 'dry_run', 'position_initialized'],
    )


def test_apply_live_order_fact_open_buy_without_fill_does_not_create_position(tmp_path):
    request = SimpleNamespace(
        symbol='BTC/USDT',
        side='buy',
        client_order_id='cid-open',
        metadata={'requested_position_size_pct': 5.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-1',
        client_order_id='cid-open',
        status='open',
        filled_base_amount=0.0,
        filled_quote_amount=0.0,
        average_fill_price=None,
        remaining_base_amount=0.5,
        raw={'status': 'open'},
    )

    result = apply_live_order_fact(response, request, base_dir=tmp_path)

    assert result.ok is True
    assert load_positions(base_dir=tmp_path) == []
    assert load_active_positions(base_dir=tmp_path) == []
    inflight_state = load_live_inflight_state(base_dir=tmp_path)
    assert inflight_state['orders']['BTC/USDT|live|armed']['status'] == 'open'


def test_apply_live_order_fact_filled_buy_creates_active_position(tmp_path):
    request = SimpleNamespace(
        symbol='ETH/USDT',
        side='buy',
        client_order_id='cid-filled',
        metadata={'requested_position_size_pct': 6.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-2',
        client_order_id='cid-filled',
        status='filled',
        filled_base_amount=0.5,
        filled_quote_amount=100.0,
        average_fill_price=200.0,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 200.0},
    )

    result = apply_live_order_fact(response, request, base_dir=tmp_path)

    assert result.ok is True
    positions = load_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.symbol == 'ETH/USDT'
    assert position.status == 'open'
    assert position.initial_position_size_pct == 6.0
    assert position.remaining_position_size_pct == 6.0
    assert position.entry_quote_amount == 100.0
    assert position.entry_base_amount == 0.5


def test_apply_live_order_fact_filled_buy_uses_exit_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(
        'src.live_fill_reconcile.load_settings',
        lambda: SimpleNamespace(
            exit=SimpleNamespace(
                initial_stop_loss_pct=2.5,
                tp1_profit_pct=4.0,
                tp2_profit_pct=8.0,
                tp1_reduce_pct=20.0,
                tp2_reduce_pct=35.0,
                trailing_drawdown_pct=2.0,
                move_stop_to_breakeven_on_tp1=False,
                enable_trailing_on_tp2=False,
                risk_off_exit_enabled=False,
            )
        ),
    )
    request = SimpleNamespace(
        symbol='ETH/USDT',
        side='buy',
        client_order_id='cid-filled-exit-settings',
        metadata={'requested_position_size_pct': 6.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-2a',
        client_order_id='cid-filled-exit-settings',
        status='filled',
        filled_base_amount=0.5,
        filled_quote_amount=100.0,
        average_fill_price=200.0,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 200.0},
    )

    apply_live_order_fact(response, request, base_dir=tmp_path)

    position = load_live_active_positions(base_dir=tmp_path)[0]
    assert position.initial_stop_price == 195.0
    assert position.tp1_price == 208.0
    assert position.tp2_price == 216.0
    assert position.tp1_reduce_pct == 20.0
    assert position.tp2_reduce_pct == 35.0
    assert position.trailing_drawdown_pct == 2.0
    assert position.move_stop_to_breakeven_on_tp1 is False
    assert position.enable_trailing_on_tp2 is False
    assert position.risk_off_exit_enabled is False


def test_apply_live_order_fact_does_not_reuse_simulated_position(tmp_path):
    save_positions([_simulated_active_position('ETH/USDT', 'pos-sim-eth')], base_dir=tmp_path)

    request = SimpleNamespace(
        symbol='ETH/USDT',
        side='buy',
        client_order_id='cid-live-eth',
        metadata={'requested_position_size_pct': 6.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-live-eth',
        client_order_id='cid-live-eth',
        status='filled',
        filled_base_amount=0.5,
        filled_quote_amount=100.0,
        average_fill_price=200.0,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 200.0},
    )

    result = apply_live_order_fact(response, request, base_dir=tmp_path)

    assert result.ok is True
    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 2
    live_positions = load_live_active_positions(base_dir=tmp_path)
    assert len(live_positions) == 1
    assert live_positions[0].position_id == 'pos:live:cid-live-eth'
    assert classify_position_truth_domain(live_positions[0]) == 'live'
    simulated_position = next(position for position in positions if position.position_id == 'pos-sim-eth')
    assert classify_position_truth_domain(simulated_position) == 'simulation'
    assert simulated_position.entry_price == 190.0
    assert len(load_active_positions(base_dir=tmp_path)) == 2


def test_apply_live_order_fact_closed_buy_is_active_position_management_not_terminal_residue(tmp_path):
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

    result = apply_live_order_fact(response, request, base_dir=tmp_path)

    assert result.ok is True
    positions = load_live_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    assert positions[0].symbol == 'ADA/USDT'
    assert positions[0].status == 'open'

    summary = summarize_live_submit_state(
        load_live_submit_state(base_dir=tmp_path),
        active_symbols={'ADA/USDT'},
    )
    assert summary['status'] == 'closed'
    assert summary['submit_side'] == 'buy'
    assert summary['classification'] == 'active_position_management'
    assert summary['order_terminality'] == 'terminal'
    assert summary['flow_terminality'] == 'active_position'
    assert summary['flow_reason'] == 'terminal_buy_order_opened_position'
    assert summary['should_archive'] is False


def test_apply_live_order_fact_true_sell_exit_auto_cleans_control_plane_residue(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry',
        client_order_id='cid-ada-entry',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'ADA/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        },
        base_dir=tmp_path,
    )

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-exit',
        metadata={'action_intent': 'SELL_EXIT'},
    )
    sell_response = SimpleNamespace(
        exchange_order_id='order-ada-exit',
        client_order_id='cid-ada-exit',
        status='closed',
        filled_base_amount=100.0,
        filled_quote_amount=84.0,
        average_fill_price=0.84,
        remaining_base_amount=0.0,
        raw={'status': 'closed', 'average': 0.84},
    )

    result = apply_live_order_fact(sell_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    assert load_live_active_positions(base_dir=tmp_path) == []
    assert load_live_inflight_state(base_dir=tmp_path)['orders'] == {}

    submit_state = load_live_submit_state(base_dir=tmp_path)
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
    assert submit_state['archived_last_submit']['last_symbol'] == 'ADA/USDT'

    summary = summarize_live_submit_state(submit_state)
    assert summary['status'] is None
    assert summary['submit_side'] is None
    assert summary['classification'] == 'empty'
    assert summary['flow_terminality'] == 'none'

    runner_state = load_runner_state(base_dir=tmp_path)
    assert runner_state['last_active_trade_status'] == 'idle'
    assert runner_state['last_active_trade_symbol'] is None
    assert runner_state['last_active_trade_stage'] == 'none'
    assert runner_state['last_active_trade_lock_reason'] is None

    state = build_single_active_trade_state(base_dir=tmp_path)
    assert state.status == 'idle'
    assert any(action.startswith('LIVE_SUBMIT_STATE_AUTO_ARCHIVED') for action in result.actions)
    assert any(action.startswith('CONTROL_PLANE_POST_EXIT_RECONCILED') for action in result.actions)


def test_apply_live_order_fact_sell_submit_open_does_not_cleanup_before_true_exit(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-open-exit',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-open-exit',
        client_order_id='cid-ada-entry-open-exit',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)
    save_runner_state(
        {
            'last_active_trade_status': 'locked',
            'last_active_trade_symbol': 'ADA/USDT',
            'last_active_trade_stage': 'position_open',
            'last_active_trade_lock_reason': 'active_open_position_exists',
        },
        base_dir=tmp_path,
    )

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-exit-open',
        metadata={'action_intent': 'SELL_EXIT'},
    )
    sell_response = SimpleNamespace(
        exchange_order_id='order-ada-exit-open',
        client_order_id='cid-ada-exit-open',
        status='open',
        filled_base_amount=0.0,
        filled_quote_amount=0.0,
        average_fill_price=None,
        remaining_base_amount=100.0,
        raw={'status': 'open'},
    )

    result = apply_live_order_fact(sell_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    assert len(load_live_active_positions(base_dir=tmp_path)) == 1
    assert load_live_inflight_state(base_dir=tmp_path)['orders']['ADA/USDT|live|armed']['status'] == 'open'

    submit_state = load_live_submit_state(base_dir=tmp_path)
    assert submit_state['last_client_order_id'] == 'cid-ada-exit-open'
    assert submit_state['last_submit_status'] == 'open'
    assert submit_state['last_submit_side'] == 'sell'
    assert submit_state['last_symbol'] == 'ADA/USDT'
    assert submit_state['archived_last_submit'] is None

    runner_state = load_runner_state(base_dir=tmp_path)
    assert runner_state['last_active_trade_status'] == 'locked'
    assert runner_state['last_active_trade_symbol'] == 'ADA/USDT'
    assert runner_state['last_active_trade_stage'] == 'position_open'
    assert runner_state['last_active_trade_lock_reason'] == 'active_open_position_exists'
    assert not any(action.startswith('LIVE_SUBMIT_STATE_AUTO_ARCHIVED') for action in result.actions)
    assert not any(action.startswith('CONTROL_PLANE_POST_EXIT_RECONCILED') for action in result.actions)


def test_apply_live_order_fact_small_buy_below_tiny_threshold_remains_active_live_position(tmp_path):
    request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-small-buy',
        metadata={'requested_position_size_pct': 6.0},
    )
    response = SimpleNamespace(
        exchange_order_id='order-ada-small-buy',
        client_order_id='cid-ada-small-buy',
        status='filled',
        filled_base_amount=10.0,
        filled_quote_amount=5.94,
        average_fill_price=0.594,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.594},
    )

    result = apply_live_order_fact(response, request, base_dir=tmp_path)

    assert result.ok is True
    positions = load_live_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    assert positions[0].symbol == 'ADA/USDT'
    assert positions[0].status == 'open'
    assert positions[0].entry_quote_amount == 5.94

    state = build_single_active_trade_state(base_dir=tmp_path)

    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
    assert state.lock.active_symbol == 'ADA/USDT'
    assert state.residue_positions == []
    assert state.observed_positions[0]['participates_in_live_control_plane'] is True
    assert state.observed_positions[0]['blocking'] is True


def test_apply_live_order_fact_sell_reduce_tiny_leftover_is_still_classified_as_residue(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-small-residue',
        metadata={'requested_position_size_pct': 6.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-small-residue',
        client_order_id='cid-ada-entry-small-residue',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=100.0,
        average_fill_price=1.0,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 1.0},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-reduce-small-residue',
        metadata={'action_intent': 'SELL_REDUCE'},
    )
    sell_response = SimpleNamespace(
        exchange_order_id='order-ada-reduce-small-residue',
        client_order_id='cid-ada-reduce-small-residue',
        status='filled',
        filled_base_amount=95.0,
        filled_quote_amount=95.0,
        average_fill_price=1.0,
        remaining_base_amount=5.0,
        raw={'status': 'filled', 'average': 1.0},
    )

    result = apply_live_order_fact(sell_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    assert any(action.startswith('POSITION_RESIDUE_CLASSIFIED symbol=ADA/USDT') for action in result.actions)
    assert load_live_active_positions(base_dir=tmp_path) == []

    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 1
    assert positions[0].status == 'closed'
    assert 'residue_dust' in positions[0].tags


def test_apply_live_order_fact_tp1_reduce_fill_marks_tp1_and_moves_stop(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-tp1',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-tp1',
        client_order_id='cid-ada-entry-tp1',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-tp1',
        metadata={
            'action_intent': 'SELL_REDUCE',
            'lifecycle_trigger': 'tp1_reduce',
            'expected_position_status_after_fill': 'partially_reduced',
            'move_stop_to_breakeven_after_fill': True,
            'requested_reduce_pct': 30.0,
        },
    )
    sell_response = SimpleNamespace(
        exchange_order_id='order-ada-tp1',
        client_order_id='cid-ada-tp1',
        status='filled',
        filled_base_amount=30.0,
        filled_quote_amount=25.2,
        average_fill_price=0.84,
        remaining_base_amount=None,
        raw={'status': 'filled', 'average': 0.84},
    )

    result = apply_live_order_fact(sell_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    positions = load_live_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.status == 'partially_reduced'
    assert position.tp1_hit is True
    assert position.tp1_hit_time is not None
    assert position.tp2_hit is False
    assert position.trailing_enabled is False
    assert position.active_stop_price == position.entry_price
    assert position.remaining_position_size_pct == 3.5


def test_apply_live_order_fact_tp2_reduce_fill_marks_tp2_and_enables_trailing(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-tp2',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-tp2',
        client_order_id='cid-ada-entry-tp2',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)

    positions = load_positions(base_dir=tmp_path)
    positions[0].status = 'partially_reduced'
    positions[0].remaining_position_size_pct = 3.5
    positions[0].tp1_hit = True
    positions[0].tp1_hit_time = utc_now_iso()
    positions[0].active_stop_price = positions[0].entry_price
    save_positions(positions, base_dir=tmp_path)

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-tp2',
        metadata={
            'action_intent': 'SELL_REDUCE',
            'lifecycle_trigger': 'tp2_reduce',
            'expected_position_status_after_fill': 'partially_reduced',
            'move_stop_to_breakeven_after_fill': True,
            'enable_trailing_after_fill': True,
            'requested_reduce_pct': 30.0,
        },
    )
    sell_response = SimpleNamespace(
        exchange_order_id='order-ada-tp2',
        client_order_id='cid-ada-tp2',
        status='filled',
        filled_base_amount=30.0,
        filled_quote_amount=26.4,
        average_fill_price=0.88,
        remaining_base_amount=None,
        raw={'status': 'filled', 'average': 0.88},
    )

    result = apply_live_order_fact(sell_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    live_positions = load_live_active_positions(base_dir=tmp_path)
    assert len(live_positions) == 1
    position = live_positions[0]
    assert position.status == 'partially_reduced'
    assert position.tp1_hit is True
    assert position.tp2_hit is True
    assert position.tp2_hit_time is not None
    assert position.trailing_enabled is True
    assert position.active_stop_price == position.entry_price
    assert position.remaining_position_size_pct == pytest.approx(2.0)


def test_apply_live_order_fact_reduce_refresh_uses_incremental_fill_delta(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-refresh-delta',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-refresh-delta',
        client_order_id='cid-ada-entry-refresh-delta',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-reduce-refresh-delta',
        metadata={
            'action_intent': 'SELL_REDUCE',
            'lifecycle_trigger': 'tp1_reduce',
            'expected_position_status_after_fill': 'partially_reduced',
            'move_stop_to_breakeven_after_fill': True,
            'requested_reduce_pct': 30.0,
        },
    )
    partial_response = SimpleNamespace(
        exchange_order_id='order-ada-reduce-refresh-delta',
        client_order_id='cid-ada-reduce-refresh-delta',
        status='open',
        filled_base_amount=20.0,
        filled_quote_amount=16.8,
        average_fill_price=0.84,
        remaining_base_amount=10.0,
        raw={'status': 'open', 'average': 0.84},
    )

    apply_live_order_fact(partial_response, sell_request, base_dir=tmp_path)

    positions = load_live_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    assert positions[0].status == 'partially_reduced'
    assert positions[0].remaining_position_size_pct == pytest.approx(4.0)

    filled_response = SimpleNamespace(
        exchange_order_id='order-ada-reduce-refresh-delta',
        client_order_id='cid-ada-reduce-refresh-delta',
        status='closed',
        filled_base_amount=30.0,
        filled_quote_amount=25.2,
        average_fill_price=0.84,
        remaining_base_amount=0.0,
        raw={'status': 'closed', 'average': 0.84},
    )

    apply_live_order_fact(filled_response, sell_request, base_dir=tmp_path)

    positions = load_live_active_positions(base_dir=tmp_path)
    assert len(positions) == 1
    assert positions[0].status == 'partially_reduced'
    assert positions[0].remaining_position_size_pct == pytest.approx(3.5)


def test_apply_live_order_fact_partial_exit_does_not_release_before_true_exit(tmp_path):
    buy_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='buy',
        client_order_id='cid-ada-entry-partial-exit',
        metadata={'requested_position_size_pct': 5.0},
    )
    buy_response = SimpleNamespace(
        exchange_order_id='order-ada-entry-partial-exit',
        client_order_id='cid-ada-entry-partial-exit',
        status='filled',
        filled_base_amount=100.0,
        filled_quote_amount=80.0,
        average_fill_price=0.8,
        remaining_base_amount=0.0,
        raw={'status': 'filled', 'average': 0.8},
    )
    apply_live_order_fact(buy_response, buy_request, base_dir=tmp_path)

    sell_request = SimpleNamespace(
        symbol='ADA/USDT',
        side='sell',
        client_order_id='cid-ada-exit-partial',
        metadata={
            'action_intent': 'SELL_EXIT',
            'expected_position_status_after_fill': 'closed',
        },
    )
    partial_exit_response = SimpleNamespace(
        exchange_order_id='order-ada-exit-partial',
        client_order_id='cid-ada-exit-partial',
        status='canceled',
        filled_base_amount=30.0,
        filled_quote_amount=25.2,
        average_fill_price=0.84,
        remaining_base_amount=70.0,
        raw={'status': 'canceled', 'average': 0.84},
    )

    result = apply_live_order_fact(partial_exit_response, sell_request, base_dir=tmp_path)

    assert result.ok is True
    live_positions = load_live_active_positions(base_dir=tmp_path)
    assert len(live_positions) == 1
    assert live_positions[0].status == 'partially_reduced'
    assert live_positions[0].remaining_position_size_pct == pytest.approx(3.5)
    assert any(action.startswith('POSITION_SELL_PARTIAL_RECONCILED symbol=ADA/USDT') for action in result.actions)
    assert (tmp_path / 'active_trade_releases.jsonl').exists() is False

    state = build_single_active_trade_state(base_dir=tmp_path)
    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
