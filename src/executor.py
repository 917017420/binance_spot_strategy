from __future__ import annotations

import json
from pathlib import Path

from .models import ExecutionResult, PendingConfirmation
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / "data" / "execution"
EXECUTION_LOG_FILE = DEFAULT_EXECUTION_DIR / "executed_orders.jsonl"


def _execution_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / EXECUTION_LOG_FILE.name


def estimate_quote_amount(total_equity_quote: float, requested_position_size_pct: float) -> float:
    return max(total_equity_quote * (requested_position_size_pct / 100.0), 0.0)


def estimate_base_amount(quote_amount: float, reference_price: float) -> float:
    if reference_price <= 0:
        return 0.0
    return quote_amount / reference_price


def build_dry_run_execution(
    confirmation: PendingConfirmation,
    total_equity_quote: float,
    reference_price: float | None = None,
) -> ExecutionResult:
    price = reference_price if reference_price is not None else confirmation.trigger_price
    quote_amount = estimate_quote_amount(total_equity_quote, confirmation.requested_position_size_pct)
    base_amount = estimate_base_amount(quote_amount, price)
    return ExecutionResult(
        confirmation_id=confirmation.confirmation_id,
        status="simulated",
        symbol=confirmation.symbol,
        requested_position_size_pct=confirmation.requested_position_size_pct,
        reference_price=price,
        estimated_quote_amount=quote_amount,
        estimated_base_amount=base_amount,
        message="Dry-run execution only; no live order has been submitted.",
        created_at=utc_now_iso(),
        details={
            "suggested_stop_price": confirmation.suggested_stop_price,
            "decision_action": confirmation.decision_action,
            "execution_stage": confirmation.execution_stage,
            "attention_level": confirmation.attention_level,
        },
    )


def append_execution_result(result: ExecutionResult, base_dir: str | Path | None = None) -> Path:
    path = _execution_log_path(base_dir)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return path
