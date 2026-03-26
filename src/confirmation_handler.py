from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .confirm_parser import ConfirmationCommand, match_pending_confirmation, parse_confirmation_command
from .models import PendingConfirmation
from .pending_confirmation import expire_stale_confirmations, load_pending_confirmations, save_pending_confirmations


ALLOWED_CONFIRM_ACTIONS = {"BUY_APPROVED", "WATCHLIST_ALERT"}
ALLOWED_EXECUTION_STAGES = {"IMMEDIATE_ATTENTION", "MANUAL_CONFIRMATION"}
ALLOWED_ATTENTION_LEVELS = {"HIGH"}


@dataclass
class ConfirmationResult:
    ok: bool
    action: str
    message: str
    confirmation: PendingConfirmation | None = None
    storage_path: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace('Z', '+00:00'))


def handle_confirmation_command(
    text: str,
    base_dir: str | Path | None = None,
) -> ConfirmationResult:
    expire_stale_confirmations(base_dir)
    command = parse_confirmation_command(text)
    if command is None:
        return ConfirmationResult(ok=False, action="invalid_command", message="无法识别确认指令")

    confirmations = load_pending_confirmations(base_dir)
    expired_match = None
    for item in reversed(confirmations):
        if command.symbol_text and item.symbol.upper() == command.symbol_text.upper() and item.status == "expired":
            expired_match = item
            break
    target = match_pending_confirmation(command, confirmations)
    if target is None:
        return ConfirmationResult(ok=False, action="not_found", message="没有找到可处理的 pending confirmation")

    if target.status != "pending":
        return ConfirmationResult(ok=False, action="not_pending", message=f"该确认单当前状态为 {target.status}", confirmation=target)

    if _parse_iso(target.expires_at) <= _utc_now():
        target.status = "expired"
        path = save_pending_confirmations(confirmations, base_dir)
        return ConfirmationResult(ok=False, action="expired", message="该确认单已过期", confirmation=target, storage_path=str(path))

    if command.action == "cancel":
        target.status = "cancelled"
        path = save_pending_confirmations(confirmations, base_dir)
        return ConfirmationResult(ok=True, action="cancelled", message=f"已取消 {target.symbol} 的 pending confirmation", confirmation=target, storage_path=str(path))

    if command.action == "confirm_buy":
        if target.decision_action not in ALLOWED_CONFIRM_ACTIONS:
            return ConfirmationResult(ok=False, action="blocked_by_decision_action", message=f"当前 decision_action={target.decision_action}，不允许确认", confirmation=target)
        if target.execution_stage not in ALLOWED_EXECUTION_STAGES:
            return ConfirmationResult(ok=False, action="blocked_by_execution_stage", message=f"当前 execution_stage={target.execution_stage}，不允许确认", confirmation=target)
        if target.attention_level not in ALLOWED_ATTENTION_LEVELS:
            return ConfirmationResult(ok=False, action="blocked_by_attention_level", message=f"当前 attention_level={target.attention_level}，不允许确认", confirmation=target)
        target.status = "confirmed"
        if command.requested_position_size_pct is not None:
            target.requested_position_size_pct = command.requested_position_size_pct
        path = save_pending_confirmations(confirmations, base_dir)
        return ConfirmationResult(ok=True, action="confirmed", message=f"已确认 {target.symbol}，待进入下单前检查", confirmation=target, storage_path=str(path))

    return ConfirmationResult(ok=False, action="unsupported_action", message="暂不支持该确认动作", confirmation=target)
