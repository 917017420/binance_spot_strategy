from __future__ import annotations

from .models import PairAnalysis


MAJOR_L1 = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"}


def _theme_bucket(symbol: str) -> str:
    if symbol in MAJOR_L1:
        return "major_beta"
    return symbol.split("/")[0][:3]


def _sorted_candidates(candidates: list[PairAnalysis]) -> list[PairAnalysis]:
    return sorted(
        candidates,
        key=lambda item: (
            item.decision_action == "BUY_APPROVED",
            item.decision_action == "WATCHLIST_ALERT",
            item.decision_priority,
            item.reward_risk_ratio,
            item.scores.runway_score,
            item.scores.mtf_alignment_score,
            item.scores.structure_quality_score,
            item.scores.execution_quality_score,
            item.scores.trend_score,
            item.scores.strength_score,
            item.scores.liquidity_score,
            -len(item.penalty_reasons),
        ),
        reverse=True,
    )


def split_priority_and_secondary(candidates: list[PairAnalysis], top_n: int) -> tuple[list[PairAnalysis], list[PairAnalysis]]:
    ranked = _sorted_candidates(candidates)
    quality_candidates = [
        c for c in ranked
        if c.decision_action == "BUY_APPROVED"
        or (
            c.decision_action == "WATCHLIST_ALERT"
            and c.decision_priority >= 120
            and c.scores.total_score >= 55
            and c.runway_upside_pct >= 1.0
        )
    ]

    priority: list[PairAnalysis] = []
    secondary: list[PairAnalysis] = []
    bucket_leaders: dict[str, PairAnalysis] = {}

    for candidate in quality_candidates:
        bucket = _theme_bucket(candidate.symbol)
        leader = bucket_leaders.get(bucket)
        if leader is None:
            priority.append(candidate)
            bucket_leaders[bucket] = candidate
            if len(priority) >= min(top_n, 2):
                continue
            continue

        if bucket == "major_beta":
            same_signal_family = (leader.secondary_signal or leader.signal) == (candidate.secondary_signal or candidate.signal)
            close_priority = abs(leader.decision_priority - candidate.decision_priority) <= 5
            better_structure = candidate.scores.structure_quality_score > leader.scores.structure_quality_score
            better_mtf = candidate.scores.mtf_alignment_score > leader.scores.mtf_alignment_score
            if same_signal_family and not (better_structure or better_mtf):
                secondary.append(candidate)
                continue
            if not close_priority and not (better_structure and better_mtf):
                secondary.append(candidate)
                continue
            if len(priority) < min(top_n, 2):
                priority.append(candidate)
            else:
                secondary.append(candidate)
            continue

        secondary.append(candidate)

    for candidate in quality_candidates:
        if candidate in priority or candidate in secondary:
            continue
        secondary.append(candidate)

    return priority[: min(top_n, 2)], secondary[: max(top_n - min(top_n, 2), 0) + 5]
