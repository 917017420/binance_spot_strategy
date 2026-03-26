from __future__ import annotations

from types import SimpleNamespace

from src.config import Settings
from src.order_refresh_reconcile import run_order_refresh_reconcile


class _FakeExchange:
    def fetch_open_orders(self, symbol):
        assert symbol == 'BTC/USDT'
        return [
            {
                'symbol': symbol,
                'clientOrderId': 'cid-refresh',
                'side': 'buy',
                'type': 'market',
                'amount': 0.5,
                'average': None,
                'price': None,
                'cost': 0.0,
                'filled': 0.5,
                'status': 'filled',
            }
        ]

    def fetch_closed_orders(self, symbol):
        return []

    def close(self):
        return None


def test_order_refresh_reconcile_reuses_request_snapshot_for_missing_order_cost(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        'src.order_refresh_reconcile.load_live_submit_state',
        lambda base_dir=None: {
            'last_symbol': 'BTC/USDT',
            'last_client_order_id': 'cid-refresh',
            'last_submit_status': 'submitted',
            'last_request': {
                'quote_amount': 120.0,
                'reference_price': 240.0,
                'requested_position_size_pct': 7.5,
            },
        },
    )
    monkeypatch.setattr('src.order_refresh_reconcile.load_live_inflight_state', lambda base_dir=None: {'orders': {}})
    monkeypatch.setattr('src.order_refresh_reconcile.create_exchange', lambda settings: _FakeExchange())
    monkeypatch.setattr('src.order_refresh_reconcile.has_recent_order_lifecycle_event', lambda **kwargs: True)

    def _capture_apply(response, request, *, base_dir=None):
        captured['response'] = response
        captured['request'] = request
        captured['base_dir'] = base_dir
        return SimpleNamespace(actions=['reconciled'])

    monkeypatch.setattr('src.order_refresh_reconcile.apply_live_order_fact', _capture_apply)

    result = run_order_refresh_reconcile(settings=Settings(), base_dir='/tmp/order-refresh-reconcile')

    assert result.ok is True
    assert captured['request'].quote_amount == 120.0
    assert captured['request'].reference_price == 240.0
    assert captured['request'].metadata['requested_position_size_pct'] == 7.5
    assert captured['response'].filled_quote_amount == 120.0
    assert captured['base_dir'] == '/tmp/order-refresh-reconcile'
