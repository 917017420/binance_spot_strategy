from __future__ import annotations

from .models import IndicatorSnapshot, RiskPlan


def build_risk_plan(
    indicators_1h: IndicatorSnapshot,
    indicators_4h: IndicatorSnapshot | None = None,
    *,
    local_resistance_price: float | None = None,
    runway_upside_pct: float | None = None,
) -> RiskPlan:
    invalidation_level = min(indicators_1h.ema50, indicators_1h.low20)
    if indicators_4h is not None:
        invalidation_level = min(invalidation_level, indicators_4h.ema50)
    atr_based_buffer = indicators_1h.atr14 * 1.5
    notes = [
        "Observation mode only; no live order placement is performed.",
        f"Use {invalidation_level:.4f} as a structural invalidation reference and ATR buffer {atr_based_buffer:.4f} for trade planning.",
    ]
    if local_resistance_price and local_resistance_price > indicators_1h.close:
        notes.append(f"Nearest overhead resistance reference: {local_resistance_price:.4f}.")
    if runway_upside_pct is not None:
        notes.append(f"Estimated upside runway: {float(runway_upside_pct):.2f}% from current price.")
    notes.append("Confirm liquidity, spread, and market context before any manual action.")
    return RiskPlan(
        invalidation_level=float(invalidation_level),
        atr_based_buffer=float(atr_based_buffer),
        notes=notes,
    )
