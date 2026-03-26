from __future__ import annotations

import pandas as pd

from .config import Settings
from .models import IndicatorSnapshot, ScoreBreakdown


def _has_pullback_reclaim(frame: pd.DataFrame, lookback: int) -> bool:
    recent = frame.tail(max(lookback + 2, 4)).copy()
    if len(recent) < 3:
        return False
    latest = recent.iloc[-1]
    prior = recent.iloc[-2]
    dipped_into_ema20 = (recent.iloc[:-1]["low"] <= recent.iloc[:-1]["ema20"] * 1.01).any()
    reclaimed_ema20 = latest["close"] > latest["ema20"] and prior["close"] <= prior["ema20"] * 1.01
    return bool(dipped_into_ema20 and reclaimed_ema20)


def _has_structure_break(indicators: IndicatorSnapshot) -> bool:
    return indicators.close < indicators.low20 or indicators.close < indicators.ema50


def _has_weak_breakout_profile(indicators: IndicatorSnapshot) -> bool:
    return indicators.upper_wick_pct >= 35 and indicators.body_pct <= 25


def _has_weak_pullback_profile(indicators: IndicatorSnapshot) -> bool:
    return indicators.upper_wick_pct >= 30 and indicators.body_pct <= 20


def determine_signal(
    enriched_1h: pd.DataFrame,
    indicators_1h: IndicatorSnapshot,
    scores: ScoreBreakdown,
    regime: str,
    settings: Settings,
) -> tuple[str, str | None, list[str]]:
    reasons: list[str] = []

    breakout_ready = (
        scores.passed_candidate_gate
        and indicators_1h.close > indicators_1h.high20
        and indicators_1h.volume > indicators_1h.avg_volume20
        and abs(indicators_1h.distance_to_ema20_pct) <= settings.strategy.breakout_max_ema20_distance_pct
        and not _has_weak_breakout_profile(indicators_1h)
        and regime != "risk_off"
    )
    if breakout_ready:
        reasons.append("Breakout above prior 20-bar high with confirming volume")
        return "BUY_READY_BREAKOUT", None, reasons

    pullback_ready = (
        scores.passed_candidate_gate
        and indicators_1h.close > indicators_1h.ema50
        and indicators_1h.ema20 > indicators_1h.ema50
        and not _has_structure_break(indicators_1h)
        and not _has_weak_pullback_profile(indicators_1h)
        and _has_pullback_reclaim(enriched_1h, settings.strategy.pullback_reclaim_lookback)
        and regime != "risk_off"
    )
    if pullback_ready:
        reasons.append("Pullback and EMA20 reclaim remain structurally intact")
        return "BUY_READY_PULLBACK", None, reasons

    near_breakout = (
        scores.total_score >= max(settings.strategy.candidate_min_total_score - 5, 50)
        and indicators_1h.close >= indicators_1h.high20 * 0.985
        and regime != "risk_off"
    )
    if near_breakout:
        reasons.append("Price is close to a breakout trigger but confirmation is incomplete")
        if indicators_1h.volume <= indicators_1h.avg_volume20:
            reasons.append("Volume confirmation is still missing")
        return "WATCH_ONLY", "NEAR_BREAKOUT", reasons

    pullback_forming = (
        indicators_1h.close > indicators_1h.ema50
        and indicators_1h.ema20 > indicators_1h.ema50
        and not _has_structure_break(indicators_1h)
        and regime != "risk_off"
    )
    if pullback_forming:
        reasons.append("Trend remains intact and a pullback setup may be forming")
        return "WATCH_ONLY", "PULLBACK_FORMING", reasons

    relative_strength_watch = (
        scores.strength_score >= 12
        and scores.liquidity_score >= settings.strategy.candidate_min_liquidity_score
        and regime != "risk_off"
    )
    if relative_strength_watch:
        reasons.append("Symbol shows relative strength but lacks a clean entry trigger")
        return "WATCH_ONLY", "RELATIVE_STRENGTH_WATCH", reasons

    if regime == "risk_off":
        reasons.append("BTC regime blocks aggressive long entries")
    if _has_weak_breakout_profile(indicators_1h):
        reasons.append("Breakout profile is weak / rejection-heavy")
    if _has_weak_pullback_profile(indicators_1h):
        reasons.append("Pullback profile is weak / rejection-heavy")
    if not scores.passed_candidate_gate:
        reasons.append("Candidate gate not met")
    if scores.trend_score < settings.strategy.candidate_min_trend_score:
        reasons.append("Trend structure score is below threshold")
    if scores.liquidity_score < settings.strategy.candidate_min_liquidity_score:
        reasons.append("Liquidity score is below threshold")
    if indicators_1h.close <= indicators_1h.high20:
        reasons.append("No confirmed breakout above 20-bar high yet")
    if indicators_1h.volume <= indicators_1h.avg_volume20:
        reasons.append("Volume confirmation is missing")
    if _has_structure_break(indicators_1h):
        reasons.append("Recent structure is too weak")
    return "WATCH_ONLY", None, reasons
