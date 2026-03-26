from __future__ import annotations

from .config import Settings
from .models import PairAnalysis


DUST_RISK_WEAK = 'weak_tiny_order_fit'
DUST_RISK_ELEVATED = 'elevated_dust_risk'


BUY_APPROVED = "BUY_APPROVED"
WATCHLIST_ALERT = "WATCHLIST_ALERT"
IGNORE = "IGNORE"


MAJORS = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"}


def _derive_market_state(regime: str, analysis: PairAnalysis) -> tuple[str, str]:
    if regime == "risk_off":
        return "RISK_OFF", "NO_NEW_RISK"
    if regime == "risk_on":
        return "TREND_FAVORABLE", "MEDIUM_RISK_BUDGET"
    if analysis.signal == "BUY_READY_PULLBACK":
        return "NEUTRAL_PULLBACK_BIASED", "LOW_RISK_BUDGET"
    return "NEUTRAL_MIXED", "LOW_RISK_BUDGET"


def _base_priority(analysis: PairAnalysis) -> int:
    if analysis.signal == "BUY_READY_BREAKOUT":
        return 100
    if analysis.signal == "BUY_READY_PULLBACK":
        return 95
    if analysis.secondary_signal == "NEAR_BREAKOUT":
        return 80
    if analysis.secondary_signal == "RELATIVE_STRENGTH_WATCH":
        return 60
    if analysis.secondary_signal == "PULLBACK_FORMING":
        return 55
    return 0


def decide_action(analysis: PairAnalysis, settings: Settings) -> PairAnalysis:
    market_state, risk_budget = _derive_market_state(analysis.regime, analysis)
    analysis.market_state = market_state
    analysis.risk_budget = risk_budget
    analysis.decision_priority = _base_priority(analysis) + analysis.scores.total_score

    estimated_quote_amount = max(settings.auto_entry.scan_reference_equity_quote * (analysis.position_size_pct / 100.0), 0.0)
    estimated_base_amount = estimated_quote_amount / analysis.indicators_1h.close if analysis.indicators_1h.close > 0 else 0.0
    analysis.execution_estimated_quote_amount = estimated_quote_amount
    analysis.execution_estimated_base_amount = estimated_base_amount
    analysis.execution_min_notional_ok = estimated_quote_amount >= 5.0 if analysis.position_size_pct > 0 else True
    analysis.execution_min_amount_ok = estimated_base_amount >= 0.001 if analysis.position_size_pct > 0 else True
    analysis.execution_dust_risk = None

    positive_reasons: list[str] = []
    blocking_reasons: list[str] = []
    penalty_reasons: list[str] = []

    if analysis.scores.liquidity_score >= settings.strategy.candidate_min_liquidity_score:
        positive_reasons.append("Liquidity passes minimum tradability threshold")
    else:
        blocking_reasons.append("Liquidity is below minimum threshold")

    if analysis.scores.trend_score >= settings.strategy.candidate_min_trend_score:
        positive_reasons.append("Trend structure passes minimum threshold")
    else:
        blocking_reasons.append("Trend structure is below minimum threshold")

    if analysis.scores.total_score >= settings.strategy.candidate_min_total_score:
        positive_reasons.append("Total score passes minimum threshold")
    else:
        blocking_reasons.append("Total score is below minimum threshold")

    if analysis.secondary_signal == "NEAR_BREAKOUT":
        positive_reasons.append("Price is close to a breakout trigger")
    if analysis.secondary_signal == "RELATIVE_STRENGTH_WATCH":
        positive_reasons.append("Relative strength is notable versus the current market")

    for reason in analysis.scores.reasons:
        if "stretched" in reason or "extended" in reason or "upper wick" in reason or "Volatility is unusually high" in reason or "weak / rejection-heavy" in reason:
            penalty_reasons.append(reason)

    if analysis.scores.structure_quality_score < 0:
        penalty_reasons.append('Structure quality is weak for immediate execution')
    if analysis.scores.mtf_alignment_score >= 8:
        positive_reasons.append('Multi-timeframe alignment is supportive')
    if analysis.scores.execution_quality_score < 0:
        penalty_reasons.append('Tiny-order execution quality is not ideal')

    if analysis.regime == "risk_off":
        blocking_reasons.append("Market regime is risk_off, so no new spot entries are allowed")
        analysis.decision_action = IGNORE
        analysis.position_size_pct = 0.0
        analysis.execution_stage = "BLOCKED_BY_MARKET"
        analysis.attention_level = "LOW"
    elif analysis.signal in {"BUY_READY_BREAKOUT", "BUY_READY_PULLBACK"}:
        if blocking_reasons:
            analysis.decision_action = IGNORE
            analysis.execution_stage = "SETUP_BLOCKED"
            analysis.attention_level = "LOW"
            analysis.position_size_pct = 0.0
        elif analysis.scores.overextension_penalty <= -10:
            penalty_reasons.append("Overextension penalty is too severe for a fresh spot entry")
            analysis.decision_action = IGNORE
            analysis.execution_stage = "SETUP_BLOCKED"
            analysis.attention_level = "LOW"
            analysis.position_size_pct = 0.0
        else:
            analysis.decision_action = BUY_APPROVED
            analysis.execution_stage = "MANUAL_CONFIRMATION"
            analysis.attention_level = "HIGH"
            analysis.position_size_pct = 7.5 if analysis.signal == "BUY_READY_BREAKOUT" and analysis.regime == "risk_on" else 5.0
            positive_reasons.append("Primary signal qualifies for a spot entry under v2 rules")
            estimated_quote_amount = max(settings.auto_entry.scan_reference_equity_quote * (analysis.position_size_pct / 100.0), 0.0)
            estimated_base_amount = estimated_quote_amount / analysis.indicators_1h.close if analysis.indicators_1h.close > 0 else 0.0
            analysis.execution_estimated_quote_amount = estimated_quote_amount
            analysis.execution_estimated_base_amount = estimated_base_amount
            analysis.execution_min_notional_ok = estimated_quote_amount >= 5.0
            analysis.execution_min_amount_ok = estimated_base_amount >= 0.001
            tiny_notional = settings.auto_entry.scan_tiny_live_notional_quote
            analysis.execution_tiny_live_quote_amount = tiny_notional
            analysis.execution_tiny_live_base_amount = tiny_notional / analysis.indicators_1h.close if analysis.indicators_1h.close > 0 else 0.0
            if not analysis.execution_tiny_live_min_amount_ok:
                analysis.execution_dust_risk = DUST_RISK_ELEVATED
            elif tiny_notional < 8.0:
                analysis.execution_dust_risk = DUST_RISK_ELEVATED
            elif tiny_notional < 12.0:
                analysis.execution_dust_risk = DUST_RISK_WEAK
    elif analysis.secondary_signal == "NEAR_BREAKOUT":
        analysis.decision_action = WATCHLIST_ALERT
        analysis.execution_stage = "IMMEDIATE_ATTENTION"
        analysis.attention_level = "HIGH"
        analysis.position_size_pct = 0.0
        positive_reasons.append("Near-breakout setups are promoted to active watchlist alerts")
    elif analysis.secondary_signal in {"RELATIVE_STRENGTH_WATCH", "PULLBACK_FORMING"}:
        analysis.decision_action = WATCHLIST_ALERT
        analysis.execution_stage = "MONITOR_ONLY"
        analysis.attention_level = "MEDIUM"
        analysis.position_size_pct = 0.0
        positive_reasons.append("Secondary setup deserves monitoring but not execution")
    else:
        analysis.decision_action = IGNORE
        analysis.execution_stage = "IGNORE"
        analysis.attention_level = "LOW"
        analysis.position_size_pct = 0.0
        blocking_reasons.append("No execution-grade setup is present")

    if analysis.decision_action == BUY_APPROVED and analysis.scores.structure_quality_score < 0:
        analysis.decision_action = WATCHLIST_ALERT
        analysis.execution_stage = 'MANUAL_CONFIRMATION'
        analysis.decision_reasons = ['Weak structure quality downgraded BUY_APPROVED to WATCHLIST_ALERT']
    if analysis.decision_action == BUY_APPROVED and analysis.execution_dust_risk == DUST_RISK_ELEVATED:
        penalty_reasons.append('Estimated tiny-order size has elevated dust risk')
        analysis.decision_priority = max(analysis.decision_priority - 8, 0)
    if analysis.decision_action == BUY_APPROVED and analysis.scores.execution_quality_score <= -6:
        analysis.decision_action = WATCHLIST_ALERT
        analysis.execution_stage = 'MONITOR_ONLY'
        analysis.attention_level = 'MEDIUM'
        analysis.decision_reasons = ['Low trading-value profile downgraded BUY_APPROVED to WATCHLIST_ALERT']
        analysis.decision_priority = max(analysis.decision_priority - 15, 0)

    if analysis.day_context_label == "OVERHEATED_BREAKOUT":
        penalty_reasons.append("24h day context is overheated; reducing aggressiveness")
        analysis.decision_priority = max(analysis.decision_priority - settings.day_context.overheated_penalty, 0)
        analysis.position_size_pct = max(analysis.position_size_pct - 2.0, 0.0)
        if analysis.decision_action == BUY_APPROVED:
            analysis.decision_action = WATCHLIST_ALERT
            analysis.execution_stage = "MANUAL_CONFIRMATION"
            analysis.decision_reasons = ["Overheated day context downgraded BUY_APPROVED to WATCHLIST_ALERT"]
    elif analysis.day_context_label == "TRENDING_HEALTHY":
        positive_reasons.append("24h day context supports trend continuation")
        analysis.decision_priority += settings.day_context.trending_bonus
        analysis.position_size_pct += 1.0
    elif analysis.day_context_label == "WEAK_REBOUND":
        penalty_reasons.append("24h day context shows weak rebound structure")
        analysis.decision_priority = max(analysis.decision_priority - settings.day_context.weak_rebound_penalty, 0)

    summary_reasons: list[str] = []
    if analysis.decision_reasons:
        summary_reasons.extend(analysis.decision_reasons)
    elif analysis.decision_action == BUY_APPROVED:
        summary_reasons.append("Decision engine approved a spot entry")
    elif analysis.decision_action == WATCHLIST_ALERT:
        summary_reasons.append("Decision engine elevated this symbol to the watchlist")
    else:
        summary_reasons.append("Decision engine rejected execution for now")

    analysis.decision_reasons = summary_reasons
    analysis.blocking_reasons = blocking_reasons
    analysis.positive_reasons = positive_reasons
    analysis.penalty_reasons = penalty_reasons
    return analysis
