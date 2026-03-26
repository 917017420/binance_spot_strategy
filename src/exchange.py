from __future__ import annotations

import logging

import ccxt
import pandas as pd

from .config import Settings


LOGGER = logging.getLogger(__name__)


def create_exchange(settings: Settings) -> ccxt.binance:
    exchange = ccxt.binance(
        {
            "enableRateLimit": True,
            "timeout": settings.exchange.timeout_ms,
            "options": {
                "defaultType": "spot",
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
        }
    )
    if settings.api.enable_private and settings.api.api_key and settings.api.api_secret:
        exchange.apiKey = settings.api.api_key
        exchange.secret = settings.api.api_secret
        LOGGER.info("Using Binance client with credentials enabled")
    else:
        LOGGER.info("Using Binance client in public-data mode")
    return exchange


def fetch_ohlcv_dataframe(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not candles:
        raise ValueError(f"No OHLCV returned for {symbol} {timeframe}")

    frame = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    numeric_columns = ["open", "high", "low", "close", "volume"]
    frame[numeric_columns] = frame[numeric_columns].astype(float)
    frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame


def extract_quote_volume(ticker: dict) -> float:
    quote_volume = ticker.get("quoteVolume")
    if quote_volume is not None:
        return float(quote_volume)

    base_volume = ticker.get("baseVolume")
    last_price = ticker.get("last")
    if base_volume is not None and last_price is not None:
        return float(base_volume) * float(last_price)
    return 0.0
