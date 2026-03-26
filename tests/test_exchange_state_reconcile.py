from __future__ import annotations

from src.config import ApiSettings, Settings
from src.exchange_state_reconcile import run_exchange_state_reconcile


class _FakeExchange:
    def fetch_balance(self):
        return {'total': {'USDT': 100.0, 'BTC': 0.0}}

    def fetch_open_orders(self):
        return [
            {
                'symbol': 'BTC/USDT',
                'id': 'order-1',
                'clientOrderId': 'cid-1',
                'status': 'open',
                'type': 'limit',
                'side': 'buy',
            }
        ]

    def close(self):
        return None


def test_exchange_state_reconcile_blocks_remote_open_order_without_local_inflight(monkeypatch):
    monkeypatch.setattr('src.exchange_state_reconcile.load_live_active_positions', lambda: [])
    monkeypatch.setattr('src.exchange_state_reconcile.load_live_inflight_state', lambda: {'orders': {}})
    monkeypatch.setattr('src.exchange_state_reconcile.create_exchange', lambda settings: _FakeExchange())

    result = run_exchange_state_reconcile(
        settings=Settings(api=ApiSettings(api_key='key', api_secret='secret', enable_private=True))
    )

    assert result.ok is False
    assert 'local_remote_open_order_mismatch' in result.blocked_reasons
    assert result.local_summary['inflight_symbols'] == []
    assert result.remote_summary['open_order_symbols'] == ['BTC/USDT']
