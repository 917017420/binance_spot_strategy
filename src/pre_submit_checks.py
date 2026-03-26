from __future__ import annotations

from dataclasses import dataclass

from .models import PendingConfirmation


@dataclass
class PreSubmitCheckResult:
    ok: bool
    action: str
    message: str


ALLOWED_EXECUTION_STAGES = {"IMMEDIATE_ATTENTION", "MANUAL_CONFIRMATION"}
ALLOWED_ATTENTION_LEVELS = {"HIGH"}
ALLOWED_DECISION_ACTIONS = {"BUY_APPROVED", "WATCHLIST_ALERT"}


def run_pre_submit_checks(
    confirmation: PendingConfirmation,
    current_price: float,
    market_state: str,
    max_price_drift_pct: float = 1.0,
) -> PreSubmitCheckResult:
    if confirmation.status != "confirmed":
        return PreSubmitCheckResult(False, "not_confirmed", "确认单尚未处于 confirmed 状态")

    if confirmation.decision_action not in ALLOWED_DECISION_ACTIONS:
        return PreSubmitCheckResult(False, "blocked_by_decision_action", f"decision_action={confirmation.decision_action} 不允许进入下单前检查")

    if confirmation.execution_stage not in ALLOWED_EXECUTION_STAGES:
        return PreSubmitCheckResult(False, "blocked_by_execution_stage", f"execution_stage={confirmation.execution_stage} 不允许进入下单前检查")

    if confirmation.attention_level not in ALLOWED_ATTENTION_LEVELS:
        return PreSubmitCheckResult(False, "blocked_by_attention_level", f"attention_level={confirmation.attention_level} 不允许进入下单前检查")

    if market_state == "RISK_OFF":
        return PreSubmitCheckResult(False, "blocked_by_market", "当前市场状态为 RISK_OFF，禁止新开仓")

    if confirmation.trigger_price <= 0:
        return PreSubmitCheckResult(False, "invalid_trigger_price", "trigger_price 非法")

    drift_pct = abs((current_price / confirmation.trigger_price) - 1.0) * 100.0
    if drift_pct > max_price_drift_pct:
        return PreSubmitCheckResult(False, "price_drift_too_large", f"价格偏移 {drift_pct:.2f}% ，超过允许阈值 {max_price_drift_pct:.2f}%")

    return PreSubmitCheckResult(True, "ok", "通过下单前检查，可进入真实下单步骤")
