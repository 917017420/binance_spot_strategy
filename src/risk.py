from __future__ import annotations

from .models import IndicatorSnapshot, RiskPlan


def build_risk_plan(indicators_1h: IndicatorSnapshot) -> RiskPlan:
    invalidation_level = min(indicators_1h.ema50, indicators_1h.low20)
    atr_based_buffer = indicators_1h.atr14 * 1.5
    return RiskPlan(
        invalidation_level=float(invalidation_level),
        atr_based_buffer=float(atr_based_buffer),
        notes=[
            f"Observation mode only; no live order placement is performed.",
            f"Use {invalidation_level:.4f} as a structural invalidation reference and ATR buffer {atr_based_buffer:.4f} for trade planning.",
            "Confirm liquidity, spread, and market context before any manual action.",
        ],
    )
