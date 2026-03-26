from __future__ import annotations

from .config import Settings
from .models import IndicatorSnapshot, ScoreBreakdown


LOW_UTILITY_BASES = {'RLUSD', 'USDC', 'FDUSD', 'BUSD', 'TUSD', 'USDP', 'DAI', 'AEUR', 'EURS', 'EUR', 'USD1', 'USDS'}


def score_candidate(
    symbol: str,
    indicators_1h: IndicatorSnapshot,
    indicators_4h: IndicatorSnapshot,
    btc_indicators_1h: IndicatorSnapshot,
    regime: str,
    settings: Settings,
) -> ScoreBreakdown:
    trend_score = 0
    liquidity_score = 0
    strength_score = 0
    breakout_score = 0
    mtf_alignment_score = 0
    structure_quality_score = 0
    execution_quality_score = 0
    overextension_penalty = 0
    regime_score = 0
    reasons: list[str] = []
    day_context_bonus = 0

    volume_ratio = indicators_1h.quote_volume_24h / max(settings.universe.min_quote_volume, 1)
    if volume_ratio >= 5:
        liquidity_score = 20
        reasons.append("24h quote volume far above threshold")
    elif volume_ratio >= settings.strategy.volume_strong_multiple:
        liquidity_score = 15
        reasons.append("24h quote volume strong")
    elif volume_ratio >= settings.strategy.volume_healthy_multiple:
        liquidity_score = 10
        reasons.append("24h quote volume healthy")
    elif volume_ratio >= 1:
        liquidity_score = 5
        reasons.append("24h quote volume just above threshold")

    if indicators_1h.close > indicators_1h.ema20:
        trend_score += 5
        reasons.append(f"{symbol} 1h close above EMA20")
    if indicators_1h.close > indicators_1h.ema50:
        trend_score += 5
        reasons.append(f"{symbol} 1h close above EMA50")
    if indicators_1h.close > indicators_1h.ema200:
        trend_score += 5
        reasons.append(f"{symbol} 1h close above EMA200")
    if indicators_1h.ema20 > indicators_1h.ema50:
        trend_score += 5
        reasons.append(f"{symbol} 1h EMA20 above EMA50")
    if indicators_1h.ema50 > indicators_1h.ema200:
        trend_score += 5
        reasons.append(f"{symbol} 1h EMA50 above EMA200")

    if indicators_4h.close > indicators_4h.ema50:
        strength_score += 4
        reasons.append(f"{symbol} 4h close above EMA50")
    if indicators_4h.close > indicators_4h.ema200:
        strength_score += 4
        reasons.append(f"{symbol} 4h close above EMA200")
    if indicators_4h.ema20 > indicators_4h.ema50:
        strength_score += 4
        reasons.append(f"{symbol} 4h EMA20 above EMA50")
    if indicators_4h.ema50 > indicators_4h.ema200:
        mtf_alignment_score += 4
        reasons.append(f"{symbol} 4h EMA50 above EMA200")
    if indicators_1h.close > indicators_1h.ema50 and indicators_4h.close > indicators_4h.ema50:
        mtf_alignment_score += 4
        reasons.append(f"{symbol} multi-timeframe close alignment is bullish")
    if indicators_1h.ema20 > indicators_1h.ema50 and indicators_4h.ema20 > indicators_4h.ema50:
        mtf_alignment_score += 4
        reasons.append(f"{symbol} multi-timeframe trend alignment is supportive")
    if indicators_1h.change_24h_pct > btc_indicators_1h.change_24h_pct:
        strength_score += 4
        reasons.append(f"{symbol} 24h performance is stronger than BTC")
    if indicators_1h.change_7d_pct > btc_indicators_1h.change_7d_pct:
        strength_score += 4
        reasons.append(f"{symbol} 7d performance is stronger than BTC")

    if indicators_1h.close >= indicators_1h.high20 * 0.98:
        breakout_score += 5
        reasons.append("Price is close to the 20-bar high")
    if indicators_1h.avg_volume20 > 0 and indicators_1h.volume > indicators_1h.avg_volume20:
        breakout_score += 5
        reasons.append("Current volume exceeds 20-bar average")
    if 1.0 <= indicators_1h.atr14_pct <= 8.0:
        breakout_score += 5
        reasons.append("ATR profile is tradable")

    if indicators_1h.upper_wick_pct <= 20 and indicators_1h.body_pct >= 45:
        structure_quality_score += 5
        reasons.append("Latest candle structure is clean and decisive")
    elif indicators_1h.upper_wick_pct >= 35 and indicators_1h.body_pct <= 25:
        structure_quality_score -= 5
        reasons.append("Latest candle structure looks weak / rejection-heavy")

    ema50_gap_pct = ((indicators_4h.close - indicators_4h.ema50) / max(indicators_4h.ema50, 1e-9)) * 100
    if ema50_gap_pct >= 1.0:
        structure_quality_score += 3
        reasons.append("4h structure has healthy clearance above EMA50")
    elif ema50_gap_pct < 0:
        structure_quality_score -= 4
        reasons.append("4h structure is slipping back below EMA50")

    if indicators_1h.quote_volume_24h < max(settings.universe.min_quote_volume, 1) * 1.5:
        execution_quality_score -= 3
        reasons.append("Execution quality is weaker near the minimum liquidity threshold")
    if indicators_1h.atr14_pct < 0.35:
        execution_quality_score -= 2
        reasons.append("Very low ATR may reduce tiny-order practicality")
    if (indicators_1h.high20 - indicators_1h.low20) / max(indicators_1h.close, 1e-9) * 100 < 1.5:
        execution_quality_score -= 4
        reasons.append("Recent trading range is narrow, reducing trade value")
    if abs(indicators_1h.change_24h_pct) < 0.2 and indicators_1h.atr14_pct < 0.25:
        execution_quality_score -= 4
        reasons.append("Low realized movement reduces trading value")
    if symbol.split('/')[0] in LOW_UTILITY_BASES:
        execution_quality_score -= 8
        reasons.append("Symbol looks like a low-volatility / stable-value trading candidate")

    distance = abs(indicators_1h.distance_to_ema20_pct)
    if distance > 12:
        overextension_penalty -= 15
        reasons.append("Price is heavily stretched away from EMA20")
    elif distance > 8:
        overextension_penalty -= 10
        reasons.append("Price is extended away from EMA20")
    elif distance > settings.strategy.breakout_max_ema20_distance_pct:
        overextension_penalty -= 5
        reasons.append("Price is slightly stretched away from EMA20")

    if indicators_1h.atr14_pct > 12:
        overextension_penalty -= 5
        reasons.append("Volatility is unusually high for spot trend-following")
    if indicators_1h.change_24h_pct > 15:
        overextension_penalty -= 5
        reasons.append("24h move is already extended")
    if indicators_1h.change_24h_pct > 25:
        overextension_penalty -= 5
        reasons.append("24h move is extremely extended")
    if indicators_1h.upper_wick_pct > 45 and indicators_1h.body_pct < 35:
        overextension_penalty -= 5
        reasons.append("Latest candle shows a long upper wick / rejection")

    healthy_bonus = max(settings.day_context.trending_bonus // 2, 1)
    overheated_penalty = max(settings.day_context.overheated_penalty // 5, 1)
    weak_rebound_penalty = max(settings.day_context.weak_rebound_penalty // 5, 1)

    if indicators_1h.change_24h_pct >= 4 and indicators_1h.upper_wick_pct < 20 and indicators_1h.body_pct >= 45:
        day_context_bonus += healthy_bonus
        reasons.append("24h context looks healthy and trend-supportive")
    if indicators_1h.change_24h_pct >= 8 and indicators_1h.upper_wick_pct >= 30:
        overextension_penalty -= overheated_penalty
        reasons.append("24h context looks overheated after a sharp move")
    if indicators_1h.change_24h_pct <= -2 and indicators_1h.close < indicators_1h.ema20:
        overextension_penalty -= weak_rebound_penalty
        reasons.append("24h context shows weak rebound / downside pressure")

    if regime == "risk_on":
        regime_score = 10
        reasons.append("BTC regime is risk_on")
    elif regime == "neutral":
        regime_score = 5
        reasons.append("BTC regime is neutral")

    total_score = max(
        0,
        min(
            liquidity_score
            + trend_score
            + strength_score
            + breakout_score
            + mtf_alignment_score
            + structure_quality_score
            + execution_quality_score
            + regime_score
            + day_context_bonus
            + overextension_penalty,
            100,
        ),
    )
    passed_candidate_gate = (
        total_score >= settings.strategy.candidate_min_total_score
        and trend_score >= settings.strategy.candidate_min_trend_score
        and liquidity_score >= settings.strategy.candidate_min_liquidity_score
    )
    strong_candidate = total_score >= settings.strategy.strong_total_score
    return ScoreBreakdown(
        trend_score=trend_score,
        liquidity_score=liquidity_score,
        strength_score=strength_score,
        breakout_score=breakout_score,
        mtf_alignment_score=mtf_alignment_score,
        structure_quality_score=structure_quality_score,
        execution_quality_score=execution_quality_score,
        overextension_penalty=overextension_penalty,
        regime_score=regime_score,
        total_score=total_score,
        passed_candidate_gate=passed_candidate_gate,
        strong_candidate=strong_candidate,
        reasons=reasons,
    )
