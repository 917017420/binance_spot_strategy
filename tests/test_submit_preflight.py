from __future__ import annotations

from types import SimpleNamespace

from src.config import ApiSettings, Settings
from src.live_order_payload import LiveOrderPayload
from src.submit_preflight import run_submit_preflight


def _unlocked_state() -> SimpleNamespace:
    return SimpleNamespace(lock=SimpleNamespace(blocking=False, active_symbol=None, lock_reason=None))


def _live_settings() -> Settings:
    return Settings(api=ApiSettings(api_key='key', api_secret='secret', enable_private=True, enable_order_submit=True))


def test_submit_preflight_blocks_non_positive_limit_reference(monkeypatch):
    monkeypatch.setattr('src.submit_preflight.build_single_active_trade_state', _unlocked_state)
    payload = LiveOrderPayload(
        symbol='BTC/USDT',
        side='buy',
        order_type='limit',
        quote_amount=100.0,
        reference_price=0.0,
        requested_position_size_pct=5.0,
        client_order_id='cid-limit-ref',
    )

    result = run_submit_preflight(
        _live_settings(),
        payload,
        markets={'BTC/USDT': {'limits': {'cost': {'min': 10.0}, 'amount': {'min': 0.001}}, 'precision': {}}},
    )

    assert result.ok is False
    assert 'reference_price_non_positive' in result.blocked_reasons


def test_submit_preflight_blocks_base_amount_below_market_minimum(monkeypatch):
    monkeypatch.setattr('src.submit_preflight.build_single_active_trade_state', _unlocked_state)
    payload = LiveOrderPayload(
        symbol='BTC/USDT',
        side='buy',
        order_type='limit',
        quote_amount=10.0,
        reference_price=100.0,
        requested_position_size_pct=5.0,
        client_order_id='cid-limit-min',
    )

    result = run_submit_preflight(
        _live_settings(),
        payload,
        markets={'BTC/USDT': {'limits': {'cost': {'min': 5.0}, 'amount': {'min': 0.2}}, 'precision': {}}},
    )

    assert result.ok is False
    assert 'base_amount_below_min_amount' in result.blocked_reasons
    assert result.normalized['estimated_base_amount'] == 0.1
