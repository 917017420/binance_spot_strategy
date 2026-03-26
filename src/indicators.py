from __future__ import annotations

import pandas as pd


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.rolling(14).mean()
    df["high20"] = df["high"].rolling(20).max().shift(1)
    df["low20"] = df["low"].rolling(20).min().shift(1)
    df["avg_volume20"] = df["volume"].rolling(20).mean().shift(1)
    df["atr14_pct"] = (df["atr14"] / df["close"]) * 100.0
    df["distance_to_ema20_pct"] = ((df["close"] / df["ema20"]) - 1.0) * 100.0
    return df


def require_indicator_history(frame: pd.DataFrame, required_rows: int = 220) -> None:
    if len(frame) < required_rows:
        raise ValueError(f"Insufficient OHLCV history: expected >= {required_rows}, got {len(frame)}")


def latest_snapshot(frame: pd.DataFrame, quote_volume_24h: float) -> dict[str, float]:
    latest = frame.iloc[-1]
    close = float(latest["close"])
    open_price = float(latest["open"])
    high = float(latest["high"])
    low = float(latest["low"])

    close_24h_ago = float(frame.iloc[-24]["close"]) if len(frame) >= 24 else close
    close_7d_ago = float(frame.iloc[-168]["close"]) if len(frame) >= 168 else close
    change_24h_pct = ((close / close_24h_ago) - 1.0) * 100.0 if close_24h_ago else 0.0
    change_7d_pct = ((close / close_7d_ago) - 1.0) * 100.0 if close_7d_ago else 0.0

    candle_range = max(high - low, 1e-12)
    upper_wick_pct = ((high - max(open_price, close)) / candle_range) * 100.0
    body_pct = (abs(close - open_price) / candle_range) * 100.0

    return {
        "close": close,
        "ema20": float(latest["ema20"]),
        "ema50": float(latest["ema50"]),
        "ema200": float(latest["ema200"]),
        "atr14": float(latest["atr14"]),
        "atr14_pct": float(latest["atr14_pct"]),
        "high20": float(latest["high20"]),
        "low20": float(latest["low20"]),
        "avg_volume20": float(latest["avg_volume20"]),
        "volume": float(latest["volume"]),
        "quote_volume_24h": float(quote_volume_24h),
        "distance_to_ema20_pct": float(latest["distance_to_ema20_pct"]),
        "change_24h_pct": float(change_24h_pct),
        "change_7d_pct": float(change_7d_pct),
        "upper_wick_pct": float(upper_wick_pct),
        "body_pct": float(body_pct),
    }
