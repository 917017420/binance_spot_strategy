from __future__ import annotations

from types import SimpleNamespace

from src.position_action_executor import load_position_action_results
from src.live_inflight_state import save_live_inflight_state
from src.position_monitor import run_position_monitor, run_position_monitor_auto
from src.models import Position
from src.positions_store import load_positions, save_positions
from src.runner_state import load_runner_state
from src.single_active_trade_state import build_single_active_trade_state
from src.utils import utc_now_iso


def _live_position(
    symbol: str,
    position_id: str,
    *,
    status: str = 'open',
    entry_price: float = 1.0,
    active_stop_price: float = 0.96,
    initial_position_size_pct: float = 5.0,
    remaining_position_size_pct: float = 5.0,
    entry_base_amount: float = 100.0,
    tp1_hit: bool = False,
    tp2_hit: bool = False,
    trailing_enabled: bool = False,
    tp1_reduce_pct: float = 30.0,
    tp2_reduce_pct: float = 30.0,
    move_stop_to_breakeven_on_tp1: bool = True,
    enable_trailing_on_tp2: bool = True,
) -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status=status,
        entry_time=now,
        entry_price=entry_price,
        entry_signal='BUY_READY_BREAKOUT',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='live_fill_reconciled',
        entry_attention_level='high',
        initial_position_size_pct=initial_position_size_pct,
        remaining_position_size_pct=remaining_position_size_pct,
        entry_quote_amount=entry_base_amount * entry_price,
        entry_base_amount=entry_base_amount,
        initial_stop_price=active_stop_price,
        active_stop_price=active_stop_price,
        suggested_stop_price=active_stop_price,
        risk_budget='normal',
        market_state_at_entry='LIVE_RECONCILED',
        tp1_price=entry_price * 1.06,
        tp2_price=entry_price * 1.10,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp1_reduce_pct=tp1_reduce_pct,
        tp2_reduce_pct=tp2_reduce_pct,
        move_stop_to_breakeven_on_tp1=move_stop_to_breakeven_on_tp1,
        enable_trailing_on_tp2=enable_trailing_on_tp2,
        trailing_enabled=trailing_enabled,
        highest_price_since_entry=entry_price,
        last_price=entry_price,
        notes=[],
        tags=['live_fill_reconciled', 'truth_domain_live'],
    )


def _simulated_position(symbol: str, position_id: str, *, status: str = 'open') -> Position:
    now = utc_now_iso()
    return Position(
        position_id=position_id,
        symbol=symbol,
        status=status,
        entry_time=now,
        entry_price=1.0,
        entry_signal='BUY_READY_BREAKOUT',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='armed',
        entry_attention_level='high',
        initial_position_size_pct=5.0,
        remaining_position_size_pct=5.0,
        entry_quote_amount=100.0,
        entry_base_amount=100.0,
        initial_stop_price=0.95,
        active_stop_price=0.95,
        suggested_stop_price=0.95,
        risk_budget='normal',
        market_state_at_entry='NEUTRAL_MIXED',
        tp1_price=1.06,
        tp2_price=1.10,
        highest_price_since_entry=1.0,
        last_price=1.0,
        notes=[],
        tags=['manual_confirmed', 'dry_run', 'position_initialized'],
    )


def test_live_monitor_exit_keeps_position_open_until_true_fill(monkeypatch, tmp_path):
    save_positions([_live_position('ADA/USDT', 'pos-live-ada-stop', active_stop_price=0.90)], base_dir=tmp_path)

    captured = {}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['payload'] = payload
        captured['base_dir'] = base_dir
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    result = run_position_monitor(current_price=0.88, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert result.scanned == 1
    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.status == 'open'
    assert position.remaining_position_size_pct == 5.0
    assert position.last_price == 0.88

    assert captured['payload'].base_amount == 100.0
    assert captured['base_dir'] == tmp_path
    assert captured['payload'].metadata['lifecycle_trigger'] == 'stop_exit'
    assert captured['payload'].metadata['expected_position_status_after_fill'] == 'stopped'

    actions = load_position_action_results(base_dir=tmp_path)
    assert len(actions) == 1
    assert actions[0].action == 'SELL_EXIT'
    assert actions[0].status == 'submitted'
    assert actions[0].details['lifecycle_truth_source'] == 'submit_fact_only'
    assert 'active_trade_release_log_path' not in actions[0].details

    state = build_single_active_trade_state(base_dir=tmp_path)
    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
    assert state.lock.active_symbol == 'ADA/USDT'



def test_live_monitor_tp2_reduce_submits_real_sell_without_preapplying_tp2(monkeypatch, tmp_path):
    save_positions(
        [
            _live_position(
                'ADA/USDT',
                'pos-live-ada-tp2',
                status='partially_reduced',
                remaining_position_size_pct=3.5,
                tp1_hit=True,
                tp2_hit=False,
                trailing_enabled=False,
            )
        ],
        base_dir=tmp_path,
    )

    captured = {}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['payload'] = payload
        captured['base_dir'] = base_dir
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    result = run_position_monitor(current_price=1.11, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert result.scanned == 1
    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.status == 'partially_reduced'
    assert position.tp2_hit is False
    assert position.trailing_enabled is False
    assert position.remaining_position_size_pct == 3.5

    assert captured['payload'].base_amount == 30.0
    assert captured['base_dir'] == tmp_path
    assert captured['payload'].metadata['lifecycle_trigger'] == 'tp2_reduce'
    assert captured['payload'].metadata['enable_trailing_after_fill'] is True

    actions = load_position_action_results(base_dir=tmp_path)
    assert len(actions) == 1
    assert actions[0].action == 'SELL_REDUCE'
    assert actions[0].status == 'submitted'
    assert actions[0].details['suggested_action'] == 'ENABLE_TRAILING_STOP'


def test_live_monitor_repeated_stop_exit_suppresses_duplicate_sell_submit(monkeypatch, tmp_path):
    save_positions([_live_position('ADA/USDT', 'pos-live-ada-repeat-stop', active_stop_price=0.90)], base_dir=tmp_path)

    captured = {'calls': 0}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['calls'] += 1
        captured['payload'] = payload
        captured['base_dir'] = base_dir
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    first = run_position_monitor(current_price=0.88, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)
    second = run_position_monitor(current_price=0.87, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert first.scanned == 1
    assert second.scanned == 1
    assert captured['calls'] == 1
    assert captured['base_dir'] == tmp_path

    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.status == 'open'
    assert position.last_price == 0.87
    assert position.remaining_position_size_pct == 5.0

    actions = load_position_action_results(base_dir=tmp_path)
    assert len(actions) == 2
    assert actions[0].action == 'SELL_EXIT'
    assert actions[0].status == 'submitted'
    assert actions[1].action == 'SELL_EXIT'
    assert actions[1].status == 'skipped'
    assert actions[1].details['skip_reason'] == 'pending_live_management_order_exists'
    assert actions[1].details['lifecycle_truth_source'] == 'pending_submit_guard'


def test_live_monitor_repeated_tp2_reduce_suppresses_duplicate_submit(monkeypatch, tmp_path):
    save_positions(
        [
            _live_position(
                'ADA/USDT',
                'pos-live-ada-repeat-tp2',
                status='partially_reduced',
                remaining_position_size_pct=3.5,
                tp1_hit=True,
                tp2_hit=False,
                trailing_enabled=False,
            )
        ],
        base_dir=tmp_path,
    )

    captured = {'calls': 0}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['calls'] += 1
        captured['payload'] = payload
        captured['base_dir'] = base_dir
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    first = run_position_monitor(current_price=1.11, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)
    second = run_position_monitor(current_price=1.12, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert first.scanned == 1
    assert second.scanned == 1
    assert captured['calls'] == 1
    assert captured['base_dir'] == tmp_path

    positions = load_positions(base_dir=tmp_path)
    assert len(positions) == 1
    position = positions[0]
    assert position.status == 'partially_reduced'
    assert position.tp2_hit is False
    assert position.trailing_enabled is False
    assert position.last_price == 1.12

    actions = load_position_action_results(base_dir=tmp_path)
    assert len(actions) == 2
    assert actions[0].action == 'SELL_REDUCE'
    assert actions[0].status == 'submitted'
    assert actions[1].action == 'SELL_REDUCE'
    assert actions[1].status == 'skipped'
    assert actions[1].details['suggested_action'] == 'ENABLE_TRAILING_STOP'
    assert actions[1].details['skip_reason'] == 'pending_live_management_order_exists'


def test_live_monitor_with_same_symbol_pending_sell_keeps_active_owner_and_syncs_runner_state(monkeypatch, tmp_path):
    save_positions([_live_position('ADA/USDT', 'pos-live-ada-pending-exit', active_stop_price=0.90)], base_dir=tmp_path)
    save_live_inflight_state(
        {
            'orders': {
                'ADA/USDT|live|armed': {
                    'status': 'open',
                    'side': 'sell',
                    'action_intent': 'SELL_EXIT',
                    'client_order_id': 'cid-ada-pending-exit',
                    'updated_at': utc_now_iso(),
                }
            },
            'released': {},
            'quarantined': {},
        },
        base_dir=tmp_path,
    )

    captured = {'calls': 0}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['calls'] += 1
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    result = run_position_monitor(current_price=0.87, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert result.scanned == 1
    assert captured['calls'] == 0

    position = load_positions(base_dir=tmp_path)[0]
    assert position.status == 'open'
    assert position.last_price == 0.87

    state = build_single_active_trade_state(base_dir=tmp_path)
    assert state.status == 'locked'
    assert state.lock.lock_reason == 'active_open_position_exists'
    assert state.lock.active_symbol == 'ADA/USDT'

    runner_state = load_runner_state(base_dir=tmp_path)
    assert runner_state['last_active_trade_status'] == 'locked'
    assert runner_state['last_active_trade_symbol'] == 'ADA/USDT'
    assert runner_state['last_active_trade_stage'] == 'position_open'
    assert runner_state['last_active_trade_lock_reason'] == 'active_open_position_exists'

    actions = load_position_action_results(base_dir=tmp_path)
    assert len(actions) == 1
    assert actions[0].action == 'SELL_EXIT'
    assert actions[0].status == 'skipped'
    assert actions[0].details['skip_reason'] == 'pending_live_management_order_exists'
    assert actions[0].details['pending_guard']['source'] == 'live_inflight_state'


def test_live_monitor_ignores_simulated_open_positions(tmp_path):
    save_positions(
        [
            _simulated_position('ETH/USDT', 'pos-sim-eth'),
            _simulated_position('ZEC/USDT', 'pos-sim-zec', status='partially_reduced'),
            _live_position('ADA/USDT', 'pos-live-ada-hold'),
        ],
        base_dir=tmp_path,
    )

    result = run_position_monitor(current_price=1.01, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert result.scanned == 1
    assert all('ETH/USDT' not in message for message in result.messages)
    assert all('ZEC/USDT' not in message for message in result.messages)

    positions = {position.position_id: position for position in load_positions(base_dir=tmp_path)}
    assert positions['pos-live-ada-hold'].last_price == 1.01
    assert positions['pos-sim-eth'].last_price == 1.0
    assert positions['pos-sim-zec'].last_price == 1.0


def test_live_monitor_auto_ignores_simulated_open_positions(monkeypatch, tmp_path):
    save_positions(
        [
            _simulated_position('ETH/USDT', 'pos-sim-eth-auto'),
            _live_position('ADA/USDT', 'pos-live-ada-auto'),
        ],
        base_dir=tmp_path,
    )

    requested_symbols: list[str] = []

    monkeypatch.setattr('src.position_monitor.fetch_market_regime_baseline', lambda **_: 'NEUTRAL_MIXED')

    def _fetch_symbol_last_price(*, symbol: str, **_kwargs) -> float:
        requested_symbols.append(symbol)
        return 1.02

    monkeypatch.setattr('src.position_monitor.fetch_symbol_last_price', _fetch_symbol_last_price)

    auto_result = run_position_monitor_auto(
        config_path='config.json',
        env_file='.env',
        action_mode='live',
        base_dir=tmp_path,
    )

    assert auto_result.scanned == 1
    assert requested_symbols == ['ADA/USDT']

    positions = {position.position_id: position for position in load_positions(base_dir=tmp_path)}
    assert positions['pos-live-ada-auto'].last_price == 1.02
    assert positions['pos-sim-eth-auto'].last_price == 1.0


def test_live_monitor_tp2_reduce_uses_position_exit_reduce_pct(monkeypatch, tmp_path):
    save_positions(
        [
            _live_position(
                'ADA/USDT',
                'pos-live-ada-tp2-custom',
                status='partially_reduced',
                remaining_position_size_pct=4.0,
                tp1_hit=True,
                tp2_reduce_pct=45.0,
            )
        ],
        base_dir=tmp_path,
    )

    captured = {}

    monkeypatch.setattr('src.position_action_executor.load_settings', lambda: SimpleNamespace())

    def _submit(_settings, payload, *, base_dir=None):
        captured['payload'] = payload
        return SimpleNamespace(status='submitted', details={'client_order_id': payload.client_order_id})

    monkeypatch.setattr('src.position_action_executor.submit_live_order', _submit)

    run_position_monitor(current_price=1.11, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert captured['payload'].base_amount == 45.0


def test_position_monitor_continues_after_single_position_error(monkeypatch, tmp_path):
    first = _live_position('ADA/USDT', 'pos-live-ada-error')
    second = _live_position('SOL/USDT', 'pos-live-sol-ok')
    save_positions([first, second], base_dir=tmp_path)

    original = __import__('src.position_monitor', fromlist=['evaluate_and_persist_position']).evaluate_and_persist_position

    def _patched(position, *args, **kwargs):
        if position.position_id == 'pos-live-ada-error':
            raise RuntimeError('boom')
        return original(position, *args, **kwargs)

    monkeypatch.setattr('src.position_monitor.evaluate_and_persist_position', _patched)

    result = run_position_monitor(current_price=1.01, market_state='NEUTRAL_MIXED', action_mode='live', base_dir=tmp_path)

    assert result.scanned == 2
    assert result.failed == 1
    assert any('POSITION_MONITOR_ERROR symbol=ADA/USDT' in message for message in result.messages)
    positions = {position.position_id: position for position in load_positions(base_dir=tmp_path)}
    assert positions['pos-live-sol-ok'].last_price == 1.01
