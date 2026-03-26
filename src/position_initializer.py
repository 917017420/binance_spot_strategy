from __future__ import annotations

from .config import ExitSettings
from .models import ExecutionResult, PendingConfirmation, Position, PositionState
from .position_exit_policy import build_initial_stop_price, build_take_profit_price, resolve_exit_settings
from .positions_store import append_position_event, build_position_event, upsert_position
from .utils import utc_now_iso


def _position_truth_domain_tag(execution_mode: str) -> str:
    return 'truth_domain_live' if execution_mode == 'live' else 'truth_domain_simulated'


def build_position_from_execution(
    confirmation: PendingConfirmation,
    execution: ExecutionResult,
    *,
    exit_settings: ExitSettings | None = None,
) -> Position:
    resolved_exit = resolve_exit_settings(exit_settings)
    entry_price = execution.reference_price
    initial_stop_price = confirmation.suggested_stop_price or build_initial_stop_price(entry_price, resolved_exit.initial_stop_loss_pct)
    return Position(
        position_id=f"pos:{confirmation.confirmation_id}",
        symbol=confirmation.symbol,
        status="open",
        entry_time=execution.created_at,
        entry_price=entry_price,
        entry_signal=confirmation.signal,
        entry_secondary_signal=confirmation.secondary_signal,
        entry_decision_action=confirmation.decision_action,
        entry_execution_stage=confirmation.execution_stage,
        entry_attention_level=confirmation.attention_level,
        initial_position_size_pct=confirmation.requested_position_size_pct,
        remaining_position_size_pct=confirmation.requested_position_size_pct,
        entry_quote_amount=execution.estimated_quote_amount,
        entry_base_amount=execution.estimated_base_amount,
        initial_stop_price=initial_stop_price,
        active_stop_price=initial_stop_price,
        suggested_stop_price=confirmation.suggested_stop_price,
        risk_budget=confirmation.risk_budget,
        market_state_at_entry=confirmation.market_state,
        tp1_price=build_take_profit_price(entry_price, resolved_exit.tp1_profit_pct),
        tp2_price=build_take_profit_price(entry_price, resolved_exit.tp2_profit_pct),
        tp1_reduce_pct=resolved_exit.tp1_reduce_pct,
        tp2_reduce_pct=resolved_exit.tp2_reduce_pct,
        move_stop_to_breakeven_on_tp1=resolved_exit.move_stop_to_breakeven_on_tp1,
        enable_trailing_on_tp2=resolved_exit.enable_trailing_on_tp2,
        risk_off_exit_enabled=resolved_exit.risk_off_exit_enabled,
        trailing_drawdown_pct=resolved_exit.trailing_drawdown_pct,
        highest_price_since_entry=entry_price,
        last_price=entry_price,
        tags=["manual_confirmed", execution.mode, _position_truth_domain_tag(execution.mode), "position_initialized"],
    )


def persist_initialized_position(position: Position):
    position_path = upsert_position(position)
    position_state = PositionState(
        position_id=position.position_id,
        symbol=position.symbol,
        updated_at=utc_now_iso(),
        status=position.status,
        last_price=position.last_price,
        remaining_position_size_pct=position.remaining_position_size_pct,
        active_stop_price=position.active_stop_price,
        tp1_hit=position.tp1_hit,
        tp2_hit=position.tp2_hit,
        trailing_enabled=position.trailing_enabled,
        highest_price_since_entry=position.highest_price_since_entry,
        suggested_action="HOLD",
        reasons=["Position initialized from execution result"],
    )
    event = build_position_event(position, position_state)
    event.event_type = "POSITION_OPENED"
    event_path = append_position_event(event)
    return position_path, event_path
