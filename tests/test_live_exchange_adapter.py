from __future__ import annotations

from types import SimpleNamespace

from src.config import ApiSettings, Settings
from src.live_exchange_adapter import LiveCreateOrderRequest, normalize_sell_request_to_available_balance, submit_live_order
from src.live_order_payload import LiveOrderPayload


class _FakeExchange:
    def __init__(self):
        self.captured_amount = None
        self.captured_params = None

    def load_markets(self):
        return {
            'ADA/USDT': {
                'base': 'ADA',
                'limits': {'cost': {'min': 5.0}, 'amount': {'min': 0.1}},
                'precision': {'amount': 1},
            }
        }

    def fetch_balance(self):
        return {
            'free': {'ADA': 22.1778},
            'used': {'ADA': 0.0},
            'total': {'ADA': 22.1778},
        }

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.captured_amount = amount
        self.captured_params = params
        return {
            'id': 'order-ada-exit',
            'clientOrderId': params['clientOrderId'],
            'status': 'filled',
            'filled': amount,
            'cost': amount * 0.2698,
            'average': 0.2698,
            'remaining': 0.0,
        }

    def close(self):
        return None


def _sell_request(*, base_amount: float, reference_price: float = 0.2698) -> LiveCreateOrderRequest:
    return LiveCreateOrderRequest(
        symbol='ADA/USDT',
        side='sell',
        order_type='market',
        quote_amount=base_amount * reference_price,
        base_amount=base_amount,
        client_order_id='cid-ada-exit',
        reference_price=reference_price,
        metadata={
            'action_intent': 'SELL_EXIT',
            'requested_position_size_pct': 5.0,
        },
    )


def _live_settings() -> Settings:
    return Settings(api=ApiSettings(api_key='key', api_secret='secret', enable_private=True, enable_order_submit=True))


def test_normalize_sell_request_to_available_balance_clamps_exit_to_free_balance_with_buffer():
    request = _sell_request(base_amount=22.2)

    adjusted = normalize_sell_request_to_available_balance(
        request,
        market={
            'base': 'ADA',
            'limits': {'amount': {'min': 0.1}},
            'precision': {'amount': 1},
        },
        balances={
            'free': {'ADA': 22.1778},
            'used': {'ADA': 0.0},
            'total': {'ADA': 22.1778},
        },
    )

    assert adjusted.base_amount == 22.1
    assert adjusted.base_amount < request.base_amount
    assert adjusted.quote_amount == 22.1 * 0.2698
    assert adjusted.metadata['sell_balance_base_asset'] == 'ADA'
    assert adjusted.metadata['sell_balance_source'] == 'free'
    assert adjusted.metadata['sell_requested_base_amount'] == 22.2
    assert adjusted.metadata['sell_adjusted_base_amount'] == 22.1


def test_normalize_sell_request_to_available_balance_preserves_reduce_when_below_cap():
    request = _sell_request(base_amount=6.66)
    request.metadata['action_intent'] = 'SELL_REDUCE'
    request.metadata['requested_reduce_pct'] = 30.0

    adjusted = normalize_sell_request_to_available_balance(
        request,
        market={
            'base': 'ADA',
            'limits': {'amount': {'min': 0.1}},
            'precision': {'amount': 2},
        },
        balances={
            'free': {'ADA': 22.1778},
            'used': {'ADA': 0.0},
            'total': {'ADA': 22.1778},
        },
    )

    assert adjusted.base_amount == 6.66
    assert adjusted.quote_amount == 6.66 * 0.2698
    assert adjusted.metadata['sell_requested_base_amount'] == 6.66
    assert adjusted.metadata['sell_adjusted_base_amount'] == 6.66


def test_submit_live_order_uses_balance_adjusted_sell_amount(monkeypatch, tmp_path):
    exchange = _FakeExchange()

    monkeypatch.setattr('src.live_exchange_adapter.create_exchange', lambda settings: exchange)
    monkeypatch.setattr(
        'src.live_exchange_adapter.run_submit_preflight',
        lambda settings, payload, markets=None: SimpleNamespace(ok=True, blocked_reasons=[], checks=[], normalized={'base_amount': payload.base_amount}),
    )
    monkeypatch.setattr(
        'src.live_exchange_adapter.run_exchange_state_reconcile',
        lambda settings=None: SimpleNamespace(ok=True, blocked_reasons=[], checks=[], remote_summary={}, local_summary={}),
    )
    monkeypatch.setattr(
        'src.live_exchange_adapter.apply_live_order_fact',
        lambda response, request, base_dir=None: SimpleNamespace(ok=True, actions=['stubbed']),
    )

    payload = LiveOrderPayload(
        symbol='ADA/USDT',
        side='sell',
        order_type='market',
        quote_amount=22.2 * 0.2698,
        base_amount=22.2,
        reference_price=0.2698,
        requested_position_size_pct=5.0,
        client_order_id='cid-ada-exit',
        metadata={
            'action_intent': 'SELL_EXIT',
            'requested_position_size_pct': 5.0,
        },
    )

    result = submit_live_order(_live_settings(), payload, base_dir=tmp_path)

    assert result.status == 'submitted'
    assert exchange.captured_amount == 22.1
    assert result.details['request']['base_amount'] == 22.1
    assert result.details['exchange_params']['amount'] == 22.1
    assert result.details['sell_sizing']['sell_requested_base_amount'] == 22.2
    assert result.details['sell_sizing']['sell_adjusted_base_amount'] == 22.1
