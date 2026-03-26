from __future__ import annotations

import pandas as pd

from src.auto_runner_preview_samples import build_preview_sample_candidates
from src.config import Settings
from src.decision_engine import decide_action
from src.models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown
from src.scan_flow import _estimate_runway_profile, apply_ranked_candidate_handoff
from src.scorer import score_candidate
from src.signals import determine_signal


def _indicator(*, close: float = 100.0, **overrides: float) -> IndicatorSnapshot:
    payload = dict(
        close=close,
        ema20=98.0,
        ema50=96.0,
        ema200=92.0,
        atr14=2.0,
        atr14_pct=2.0,
        high20=101.0,
        low20=90.0,
        avg_volume20=1000.0,
        volume=1400.0,
        quote_volume_24h=30_000_000.0,
        distance_to_ema20_pct=2.0,
        change_24h_pct=4.0,
        change_7d_pct=9.0,
        upper_wick_pct=12.0,
        body_pct=55.0,
    )
    payload.update(overrides)
    return IndicatorSnapshot(**payload)


def _score() -> ScoreBreakdown:
    return ScoreBreakdown(
        trend_score=24,
        liquidity_score=18,
        strength_score=16,
        breakout_score=12,
        runway_score=6,
        runway_penalty=0,
        mtf_alignment_score=8,
        structure_quality_score=4,
        execution_quality_score=2,
        overextension_penalty=0,
        regime_score=10,
        total_score=86,
        passed_candidate_gate=True,
        strong_candidate=True,
        reasons=['test'],
    )


def _analysis(*, rr: float, runway: float, near_high: bool) -> PairAnalysis:
    indicator = _indicator()
    return PairAnalysis(
        symbol='ADA/USDT',
        signal='BUY_READY_BREAKOUT',
        secondary_signal=None,
        regime='risk_on',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=_score(),
        reasons=['test'],
        risk=RiskPlan(invalidation_level=95.0),
        reward_risk_ratio=rr,
        runway_upside_pct=runway,
        near_local_high=near_high,
        planned_initial_stop_price=95.0,
        planned_tp1_price=103.0,
        planned_tp2_price=108.0,
    )


def test_apply_ranked_candidate_handoff_assigns_shared_scan_semantics():
    priority = build_preview_sample_candidates(regime='risk_on')[:2]
    secondary = build_preview_sample_candidates(regime='risk_on')[2:]

    for candidate in priority + secondary:
        candidate.execution_stage = 'MANUAL_CONFIRMATION'
        candidate.attention_level = 'LOW'

    apply_ranked_candidate_handoff(priority, secondary)

    assert priority[0].execution_stage == 'IMMEDIATE_ATTENTION'
    assert priority[0].attention_level == 'HIGH'
    assert priority[1].execution_stage == 'MANUAL_CONFIRMATION'
    assert priority[1].attention_level == 'HIGH'
    assert secondary[0].execution_stage == 'MONITOR_ONLY'
    assert secondary[0].attention_level == 'MEDIUM'


def test_score_candidate_penalizes_near_high_with_limited_runway():
    settings = Settings()
    indicator = _indicator()
    btc = _indicator(close=95.0)

    high_runway = score_candidate(
        'ADA/USDT',
        indicator,
        indicator,
        btc,
        'risk_on',
        settings,
        runway_upside_pct=6.0,
        distance_to_local_high_pct=3.5,
        near_local_high=False,
    )
    low_runway = score_candidate(
        'ADA/USDT',
        indicator,
        indicator,
        btc,
        'risk_on',
        settings,
        runway_upside_pct=0.8,
        distance_to_local_high_pct=0.6,
        near_local_high=True,
    )

    assert low_runway.total_score < high_runway.total_score
    assert low_runway.runway_penalty < 0
    assert any('limited additional upside' in reason for reason in low_runway.reasons)


def test_decide_action_blocks_buy_approval_when_reward_risk_or_runway_is_insufficient():
    settings = Settings()
    candidate = _analysis(rr=1.1, runway=1.2, near_high=True)

    decided = decide_action(candidate, settings)

    assert decided.decision_action == 'WATCHLIST_ALERT'
    assert decided.execution_stage == 'MONITOR_ONLY'
    assert any('Reward/risk ratio' in reason for reason in decided.blocking_reasons)
    assert any('Upside runway' in reason for reason in decided.blocking_reasons)


def test_decide_action_keeps_buy_approved_when_runway_and_reward_risk_pass():
    settings = Settings()
    candidate = _analysis(rr=2.4, runway=4.8, near_high=False)

    decided = decide_action(candidate, settings)

    assert decided.decision_action == 'BUY_APPROVED'


def test_determine_signal_downgrades_breakout_when_runway_is_insufficient():
    settings = Settings()
    enriched = pd.DataFrame(
        {
            'low': [97.5, 98.0, 98.6, 99.0, 99.5],
            'ema20': [98.0, 98.3, 98.6, 98.9, 99.2],
            'close': [98.4, 98.9, 99.3, 99.8, 100.1],
        }
    )
    indicators = _indicator(
        close=102.0,
        high20=100.5,
        volume=2200.0,
        avg_volume20=1000.0,
        upper_wick_pct=10.0,
        body_pct=60.0,
        distance_to_ema20_pct=2.1,
    )
    score = _score()

    signal, secondary, reasons = determine_signal(
        enriched,
        indicators,
        score,
        'risk_on',
        settings,
        runway_upside_pct=1.1,
        near_local_high=True,
    )

    assert signal == 'WATCH_ONLY'
    assert secondary is None
    assert any('runway' in reason.lower() for reason in reasons)


def test_determine_signal_blocks_breakout_readiness_when_runway_is_compressed():
    settings = Settings()
    enriched = pd.DataFrame(
        {
            'low': [97.5, 98.0, 98.6, 99.0, 99.5],
            'ema20': [98.0, 98.3, 98.6, 98.9, 99.2],
            'close': [98.4, 98.9, 99.3, 99.8, 100.1],
        }
    )
    indicators = _indicator(
        close=102.0,
        high20=100.5,
        volume=2200.0,
        avg_volume20=1000.0,
        upper_wick_pct=10.0,
        body_pct=60.0,
        distance_to_ema20_pct=2.1,
    )
    score = _score()

    signal, secondary, reasons = determine_signal(
        enriched,
        indicators,
        score,
        'risk_on',
        settings,
        runway_upside_pct=2.8,
        near_local_high=False,
    )

    assert signal == 'WATCH_ONLY'
    assert secondary is None
    assert any('compressed' in reason.lower() or 'marginally above' in reason.lower() for reason in reasons)


def test_determine_signal_blocks_pullback_readiness_when_runway_is_compressed():
    settings = Settings()
    enriched = pd.DataFrame(
        {
            'low': [99.4, 99.0, 98.8, 98.3, 98.1, 98.6],
            'ema20': [99.1, 98.9, 98.7, 98.6, 98.5, 98.7],
            'close': [99.2, 98.8, 98.6, 98.4, 98.3, 99.3],
        }
    )
    indicators = _indicator(
        close=99.3,
        high20=101.0,
        ema20=98.7,
        ema50=96.0,
        upper_wick_pct=11.0,
        body_pct=52.0,
        distance_to_ema20_pct=0.6,
    )
    score = _score()

    signal, secondary, reasons = determine_signal(
        enriched,
        indicators,
        score,
        'risk_on',
        settings,
        runway_upside_pct=2.9,
        near_local_high=False,
    )

    assert signal == 'WATCH_ONLY'
    assert secondary is None
    assert any('pullback' in reason.lower() for reason in reasons)
    assert any('compressed' in reason.lower() or 'marginally above' in reason.lower() for reason in reasons)


def test_score_candidate_applies_mild_penalty_when_runway_is_only_marginally_above_minimum():
    settings = Settings()
    indicator = _indicator()
    btc = _indicator(close=95.0)

    healthy_runway = score_candidate(
        'ADA/USDT',
        indicator,
        indicator,
        btc,
        'risk_on',
        settings,
        runway_upside_pct=6.0,
        distance_to_local_high_pct=3.5,
        near_local_high=False,
    )
    compressed_runway = score_candidate(
        'ADA/USDT',
        indicator,
        indicator,
        btc,
        'risk_on',
        settings,
        runway_upside_pct=2.9,
        distance_to_local_high_pct=2.9,
        near_local_high=False,
    )

    assert compressed_runway.runway_penalty < 0
    assert (
        compressed_runway.runway_score + compressed_runway.runway_penalty
        < healthy_runway.runway_score + healthy_runway.runway_penalty
    )
    assert any('marginally above' in reason for reason in compressed_runway.reasons)


def test_estimate_runway_profile_prefers_clustered_swing_resistance():
    settings = Settings()
    baseline_highs = [96.0 + ((index % 6) * 0.2) for index in range(80)]
    primary_highs = baseline_highs + [100.4, 99.8, 101.0, 100.9, 101.2, 100.8, 101.15, 100.95, 101.05, 100.85]
    context_highs = [95.5 + ((index % 4) * 0.3) for index in range(30)] + [100.9, 101.1, 101.2, 101.0, 101.15]
    enriched_primary = pd.DataFrame({'high': primary_highs})
    enriched_context = pd.DataFrame({'high': context_highs})

    indicators_1h = _indicator(close=100.0, high20=101.0, atr14=1.0)
    indicators_4h = _indicator(close=100.0, high20=101.2, atr14=1.2)

    profile = _estimate_runway_profile(enriched_primary, enriched_context, indicators_1h, indicators_4h, settings)

    assert profile['runway_source'] == 'swing_cluster'
    assert float(profile['runway_resistance_price'] or 0.0) > 100.8
    assert float(profile['runway_upside_pct'] or 0.0) < settings.strategy.runway_min_upside_pct
    assert profile['near_local_high'] is True
