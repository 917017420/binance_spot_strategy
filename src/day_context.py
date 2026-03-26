from __future__ import annotations

from .models import PairAnalysis


def enrich_day_context(analysis: PairAnalysis, btc_change_24h_pct: float = 0.0) -> PairAnalysis:
    close_24h = analysis.indicators_1h.close
    high_24h = analysis.indicators_1h.high20
    low_24h = analysis.indicators_1h.low20
    change_24h = analysis.indicators_1h.change_24h_pct

    range_pct = ((high_24h - low_24h) / low_24h * 100.0) if low_24h > 0 else 0.0
    close_pos = ((close_24h - low_24h) / (high_24h - low_24h)) if high_24h > low_24h else 0.0
    pullback_pct = ((high_24h - close_24h) / high_24h * 100.0) if high_24h > 0 else 0.0
    vs_btc_delta = change_24h - btc_change_24h_pct

    if change_24h >= 4.0 and close_pos >= 0.75 and pullback_pct <= 2.5:
        label = 'TRENDING_HEALTHY'
    elif change_24h >= 8.0 and range_pct >= 12.0 and pullback_pct >= 3.5:
        label = 'OVERHEATED_BREAKOUT'
    elif change_24h <= -2.0 and close_pos < 0.4:
        label = 'WEAK_REBOUND'
    else:
        label = 'NEUTRAL_DAY_STRUCTURE'

    analysis.symbol_change_24h_pct = change_24h
    analysis.symbol_range_24h_pct = range_pct
    analysis.close_position_in_24h_range = close_pos
    analysis.pullback_from_24h_high_pct = pullback_pct
    analysis.vs_btc_24h_delta = vs_btc_delta
    analysis.day_context_label = label
    return analysis
