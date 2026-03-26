from __future__ import annotations

from .indicators import add_indicators, latest_snapshot, require_indicator_history
from .models import IndicatorSnapshot, MarketRegimeReport


def evaluate_market_regime(symbol: str, frame_1h, frame_4h, quote_volume_24h: float) -> MarketRegimeReport:
    enriched_1h = add_indicators(frame_1h)
    enriched_4h = add_indicators(frame_4h)
    require_indicator_history(enriched_1h)
    require_indicator_history(enriched_4h)

    latest_1h = enriched_1h.iloc[-1]
    latest_4h = enriched_4h.iloc[-1]

    score = 0
    reasons: list[str] = []

    if latest_4h["close"] > latest_4h["ema50"]:
        score += 2
        reasons.append("BTC 4h close above EMA50")
    if latest_4h["ema50"] > latest_4h["ema200"]:
        score += 2
        reasons.append("BTC 4h EMA50 above EMA200")
    if latest_1h["close"] > latest_1h["ema50"]:
        score += 1
        reasons.append("BTC 1h close above EMA50")
    if latest_1h["ema50"] > latest_1h["ema200"]:
        score += 1
        reasons.append("BTC 1h EMA50 above EMA200")
    if latest_1h["close"] > latest_1h["ema20"]:
        score += 1
        reasons.append("BTC 1h close above EMA20")

    regime = "neutral"
    if score >= 5:
        regime = "risk_on"
    elif score <= 2:
        regime = "risk_off"

    if regime == "risk_off":
        reasons.append("BTC structure is defensive")
    elif regime == "risk_on":
        reasons.append("BTC structure supports breakout participation")
    else:
        reasons.append("BTC structure is mixed")

    return MarketRegimeReport(
        symbol=symbol,
        regime=regime,
        score=score,
        reasons=reasons,
        indicators_1h=IndicatorSnapshot(**latest_snapshot(enriched_1h, quote_volume_24h)),
        indicators_4h=IndicatorSnapshot(**latest_snapshot(enriched_4h, quote_volume_24h)),
    )
