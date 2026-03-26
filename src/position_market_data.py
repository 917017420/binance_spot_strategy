from __future__ import annotations

from .config import load_settings
from .exchange import create_exchange, fetch_ohlcv_dataframe
from .market_regime import evaluate_market_regime


def fetch_position_monitor_inputs(config_path: str, env_file: str, symbol: str = 'BTC/USDT') -> tuple[float, str]:
    settings = load_settings(config_path, env_file)
    exchange = create_exchange(settings)
    try:
        ticker = exchange.fetch_ticker(symbol)
        current_price = float(ticker['last'])
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
        regime_report = evaluate_market_regime('BTC/USDT', btc_1h, btc_4h, float(ticker.get('quoteVolume') or 0.0))
        return current_price, regime_report.regime
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()
