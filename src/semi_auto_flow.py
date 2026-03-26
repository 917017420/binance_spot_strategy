from __future__ import annotations

from dataclasses import dataclass

from .confirmation_handler import handle_confirmation_command
from .executor import append_execution_result, build_dry_run_execution
from .position_initializer import build_position_from_execution, persist_initialized_position
from .pre_submit_checks import run_pre_submit_checks


@dataclass
class SemiAutoFlowResult:
    ok: bool
    action: str
    message: str
    execution_log_path: str | None = None
    position_path: str | None = None
    position_event_path: str | None = None


def process_confirmation_to_dry_run(
    command_text: str,
    current_price: float,
    market_state: str,
    total_equity_quote: float,
) -> SemiAutoFlowResult:
    confirmation_result = handle_confirmation_command(command_text)
    if not confirmation_result.ok or confirmation_result.confirmation is None:
        return SemiAutoFlowResult(False, confirmation_result.action, confirmation_result.message)

    confirmation = confirmation_result.confirmation
    precheck = run_pre_submit_checks(
        confirmation,
        current_price=current_price,
        market_state=market_state,
    )
    if not precheck.ok:
        return SemiAutoFlowResult(False, precheck.action, precheck.message)

    execution_result = build_dry_run_execution(
        confirmation,
        total_equity_quote=total_equity_quote,
        reference_price=current_price,
    )
    log_path = append_execution_result(execution_result)

    position = build_position_from_execution(confirmation, execution_result)
    position_path, event_path = persist_initialized_position(position)

    return SemiAutoFlowResult(
        ok=True,
        action="dry_run_executed",
        message=execution_result.message,
        execution_log_path=str(log_path),
        position_path=str(position_path),
        position_event_path=str(event_path),
    )
