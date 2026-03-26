from __future__ import annotations

from dataclasses import dataclass, field

from .models import PairAnalysis
from .portfolio_risk import portfolio_risk_checks


@dataclass
class AutoEntryConfig:
    live_min_score: int = 85
    paper_min_score: int = 65
    shadow_min_score: int = 40
    shadow_soft_risk_points: int = 2
    max_open_positions: int = 3
    max_single_position_pct: float = 10.0
    max_total_exposure_pct: float = 30.0
    bucket_weight_overrides: dict[str, int] = field(default_factory=dict)
    trending_bonus: int = 8
    overheated_penalty: int = 20
    weak_rebound_penalty: int = 15
    allow_watchlist_alert_to_paper: bool = True
    allow_manual_confirmation_to_paper: bool = True
    deny_when_market_risk_off: bool = True


@dataclass
class AutoEntryDecision:
    allow: bool
    route: str
    severity: str
    reasons: list[str] = field(default_factory=list)
    score: int = 0
    checks: dict[str, bool] = field(default_factory=dict)


DEFAULT_CONFIG = AutoEntryConfig()
BLOCKED_MARKET_STATES = {"RISK_OFF", "risk_off"}


def evaluate_auto_entry(
    analysis: PairAnalysis,
    market_state: str,
    config: AutoEntryConfig | None = None,
) -> AutoEntryDecision:
    cfg = config or DEFAULT_CONFIG
    reasons: list[str] = []
    checks: dict[str, bool] = {}
    score = 100

    checks["market_not_risk_off"] = market_state not in BLOCKED_MARKET_STATES
    if cfg.deny_when_market_risk_off and not checks["market_not_risk_off"]:
        reasons.append(f"market_state={market_state} blocks auto entry")
        return AutoEntryDecision(False, "deny", "hard", reasons, score=0, checks=checks)

    checks["within_single_position_limit"] = analysis.position_size_pct <= cfg.max_single_position_pct
    if not checks["within_single_position_limit"]:
        reasons.append(f"position_size_pct too large: {analysis.position_size_pct} > {cfg.max_single_position_pct}")
        return AutoEntryDecision(False, "deny", "hard", reasons, score=0, checks=checks)

    portfolio_checks, portfolio_reasons, portfolio_snapshot, downgrade_reasons, soft_risk_points = portfolio_risk_checks(
        analysis,
        max_open_positions=cfg.max_open_positions,
        max_total_exposure_pct=cfg.max_total_exposure_pct,
        bucket_weight_overrides=cfg.bucket_weight_overrides,
    )
    checks.update(portfolio_checks)
    checks["portfolio_candidate_bucket_known"] = bool(portfolio_snapshot.get("candidate_bucket"))
    checks["bucket_profile"] = portfolio_snapshot.get("bucket_profile", "unknown")
    checks["bucket_soft_risk_weight"] = portfolio_snapshot.get("bucket_soft_risk_weight", 0)
    checks["portfolio_soft_risk_points"] = soft_risk_points
    checks["shadow_soft_risk_points_threshold"] = cfg.shadow_soft_risk_points
    if portfolio_reasons:
        reasons.extend(portfolio_reasons)
    if not all(portfolio_checks.values()):
        reasons.append(
            f"portfolio_snapshot=open_positions:{portfolio_snapshot['open_positions']} total_exposure_pct:{portfolio_snapshot['total_exposure_pct']:.2f} projected_exposure_pct:{portfolio_snapshot['projected_exposure_pct']:.2f} candidate_bucket:{portfolio_snapshot['candidate_bucket']} bucket_profile:{portfolio_snapshot['bucket_profile']} same_bucket_count:{portfolio_snapshot['same_bucket_count']} bucket_soft_risk_weight:{portfolio_snapshot['bucket_soft_risk_weight']} soft_risk_points:{portfolio_snapshot['soft_risk_points']}"
        )
        return AutoEntryDecision(False, "deny", "hard", reasons, score=0, checks=checks)

    if analysis.decision_action == "BUY_APPROVED":
        checks["decision_action_ok"] = True
        score += 10
    elif analysis.decision_action == "WATCHLIST_ALERT" and cfg.allow_watchlist_alert_to_paper:
        checks["decision_action_ok"] = True
        score -= 25
        reasons.append("WATCHLIST_ALERT downgraded to non-live route")
    else:
        checks["decision_action_ok"] = False
        reasons.append(f"decision_action={analysis.decision_action} not allowed")
        return AutoEntryDecision(False, "deny", "hard", reasons, score=0, checks=checks)

    if analysis.execution_stage == "IMMEDIATE_ATTENTION":
        checks["execution_stage_ok"] = True
        score += 10
    elif analysis.execution_stage == "MANUAL_CONFIRMATION" and cfg.allow_manual_confirmation_to_paper:
        checks["execution_stage_ok"] = True
        score -= 15
        reasons.append("MANUAL_CONFIRMATION downgraded to non-live route")
    else:
        checks["execution_stage_ok"] = False
        reasons.append(f"execution_stage={analysis.execution_stage} not allowed")
        return AutoEntryDecision(False, "deny", "hard", reasons, score=0, checks=checks)

    checks["attention_level_ok"] = analysis.attention_level == "HIGH"
    if not checks["attention_level_ok"]:
        score -= 20
        reasons.append(f"attention_level={analysis.attention_level} reduces routing confidence")

    checks['tiny_live_min_amount_ok'] = analysis.execution_tiny_live_min_amount_ok
    if not analysis.execution_tiny_live_min_amount_ok:
        score -= 20
        reasons.append('tiny live notional would violate market min amount')
    elif analysis.execution_dust_risk == 'elevated_dust_risk':
        score -= 10
        reasons.append('tiny live notional has elevated dust risk')
    elif analysis.execution_dust_risk == 'weak_tiny_order_fit':
        score -= 5
        reasons.append('tiny live notional fit is only moderate')

    score += min(max(analysis.decision_priority // 10, 0), 20)
    score += min(max((analysis.scores.total_score - 50) // 2, 0), 15)

    if analysis.day_context_label == "TRENDING_HEALTHY":
        score += cfg.trending_bonus
        reasons.append("day_context supports stronger routing")
    elif analysis.day_context_label == "OVERHEATED_BREAKOUT":
        score -= cfg.overheated_penalty
        reasons.append("day_context shows overheated breakout risk")
    elif analysis.day_context_label == "WEAK_REBOUND":
        score -= cfg.weak_rebound_penalty
        reasons.append("day_context shows weak rebound structure")

    requested_route = "live"
    if not (analysis.decision_action == "BUY_APPROVED" and analysis.execution_stage == "IMMEDIATE_ATTENTION" and analysis.attention_level == "HIGH"):
        requested_route = "paper"

    if downgrade_reasons:
        reasons.extend(downgrade_reasons)
        if soft_risk_points >= cfg.shadow_soft_risk_points:
            requested_route = "shadow"
        elif requested_route == "live":
            requested_route = "paper"
        elif requested_route == "paper":
            requested_route = "shadow"

    if score < cfg.shadow_min_score:
        return AutoEntryDecision(False, "deny", "hard", reasons or ["score below shadow threshold"], score=score, checks=checks)
    if requested_route == "live" and score >= cfg.live_min_score:
        return AutoEntryDecision(True, "live", "info", reasons or ["live route passed"], score=score, checks=checks)
    if requested_route in {"live", "paper"} and score >= cfg.paper_min_score:
        return AutoEntryDecision(True, "paper", "soft", reasons or ["paper route passed"], score=score, checks=checks)
    return AutoEntryDecision(False, "shadow", "soft", reasons or ["shadow route only"], score=score, checks=checks)
