from __future__ import annotations

from .asset_buckets import classify_asset_bucket
from .asset_risk_profile import bucket_profile, bucket_soft_risk_weight
from .models import PairAnalysis, Position
from .positions_store import load_live_active_positions


def load_open_positions() -> list[Position]:
    return load_live_active_positions()


def build_portfolio_risk_snapshot(candidate: PairAnalysis, bucket_weight_overrides: dict[str, int] | None = None) -> dict:
    open_positions = load_open_positions()
    total_open = len(open_positions)
    total_exposure = sum(max(p.remaining_position_size_pct, 0.0) for p in open_positions)
    candidate_bucket = classify_asset_bucket(candidate.symbol)
    same_bucket_positions = [p for p in open_positions if classify_asset_bucket(p.symbol) == candidate_bucket]
    same_symbol_open = any(p.symbol == candidate.symbol for p in open_positions)
    return {
        "open_positions": total_open,
        "total_exposure_pct": total_exposure,
        "same_bucket_count": len(same_bucket_positions),
        "same_symbol_open": same_symbol_open,
        "candidate_bucket": candidate_bucket,
        "bucket_profile": bucket_profile(candidate.symbol),
        "bucket_soft_risk_weight": bucket_soft_risk_weight(candidate.symbol, bucket_weight_overrides),
    }


def portfolio_risk_checks(candidate: PairAnalysis, max_open_positions: int, max_total_exposure_pct: float, bucket_weight_overrides: dict[str, int] | None = None) -> tuple[dict[str, bool], list[str], dict, list[str], int]:
    snapshot = build_portfolio_risk_snapshot(candidate, bucket_weight_overrides=bucket_weight_overrides)
    projected_exposure = snapshot["total_exposure_pct"] + candidate.position_size_pct
    checks = {
        "within_open_position_limit": snapshot["open_positions"] < max_open_positions,
        "within_total_exposure_limit": projected_exposure <= max_total_exposure_pct,
        "same_symbol_not_open": not snapshot["same_symbol_open"],
        "bucket_not_crowded": snapshot["same_bucket_count"] < 2,
    }
    reasons: list[str] = []
    downgrade_reasons: list[str] = []
    soft_risk_points = 0
    weight = snapshot["bucket_soft_risk_weight"]

    if not checks["bucket_not_crowded"]:
        reasons.append(f"portfolio bucket already crowded: {snapshot['candidate_bucket']}")
    elif snapshot["same_bucket_count"] == 1:
        downgrade_reasons.append(f"portfolio bucket already has 1 position: {snapshot['candidate_bucket']} (profile={snapshot['bucket_profile']} weight={weight})")
        soft_risk_points += max(weight, 1)

    if not checks["same_symbol_not_open"]:
        reasons.append("same symbol already has an open position")

    if not checks["within_open_position_limit"]:
        reasons.append("open position limit reached")
    elif snapshot["open_positions"] == max_open_positions - 1:
        downgrade_reasons.append("portfolio is near max open positions")
        soft_risk_points += 1

    if not checks["within_total_exposure_limit"]:
        reasons.append("total exposure limit would be exceeded")
    elif projected_exposure >= max_total_exposure_pct - 5:
        downgrade_reasons.append("projected exposure is close to portfolio cap")
        soft_risk_points += 1

    snapshot["projected_exposure_pct"] = projected_exposure
    snapshot["soft_risk_points"] = soft_risk_points
    return checks, reasons, snapshot, downgrade_reasons, soft_risk_points
