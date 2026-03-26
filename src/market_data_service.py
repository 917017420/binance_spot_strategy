from __future__ import annotations

from .config import load_settings
from .exchange import create_exchange, fetch_ohlcv_dataframe
from .market_regime import evaluate_market_regime


def fetch_last_price(exchange, symbol: str) -> float:
    ticker = exchange.fetch_ticker(symbol)
    return float(ticker['last'])


def fetch_market_regime_baseline(config_path: str, env_file: str) -> str:
    settings = load_settings(config_path, env_file)
    exchange = create_exchange(settings)
    try:
        btc_last = fetch_last_price(exchange, 'BTC/USDT')
        btc_1h = fetch_ohlcv_dataframe(
            exchange,
            'BTC/USDT',
            timeframe=settings.data.primary_timeframe,
            limit=settings.data.ohlcv_limit,
        )
        btc_4h = fetch_ohlcv_dataframe(
            exchange,
            'BTC/USDT',
            timeframe=settings.data.context_timeframe,
            limit=settings.data.ohlcv_limit,
        )
        regime = evaluate_market_regime('BTC/USDT', btc_1h, btc_4h, btc_last)
        return regime.regime
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()


def fetch_symbol_last_price(config_path: str, env_file: str, symbol: str) -> float:
    settings = load_settings(config_path, env_file)
    exchange = create_exchange(settings)
    try:
        return fetch_last_price(exchange, symbol)
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()
