from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Position, PositionState
from .positions_store import append_position_event, build_position_event, upsert_position
from .utils import utc_now_iso


RISK_OFF_STATES = {"RISK_OFF"}


@dataclass
class PositionUpdateResult:
    position: Position
    state: PositionState


def _build_live_managed_position(position: Position, evaluated: Position, state: PositionState) -> Position:
    managed = position.model_copy(deep=True)
    managed.last_price = evaluated.last_price
    managed.unrealized_pnl_pct = evaluated.unrealized_pnl_pct
    managed.highest_price_since_entry = evaluated.highest_price_since_entry
    if state.suggested_action == 'MOVE_STOP_TO_BREAKEVEN':
        managed.active_stop_price = max(managed.active_stop_price, managed.entry_price, evaluated.active_stop_price)
    return managed


def _pnl_pct(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((current_price / entry_price) - 1.0) * 100.0


def _format_pct(value: float) -> str:
    return (f'{float(value or 0.0):.2f}'.rstrip('0').rstrip('.')) or '0'


def _reduce_position_size(position: Position, reduce_pct: float) -> float:
    ratio = max(min(float(reduce_pct or 0.0) / 100.0, 1.0), 0.0)
    return max(position.remaining_position_size_pct - (position.initial_position_size_pct * ratio), 0.0)


def _build_state(position: Position, suggested_action: str, reasons: list[str]) -> PositionState:
    return PositionState(
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
        suggested_action=suggested_action,
        reasons=reasons,
    )


def evaluate_position(
    position: Position,
    current_price: float,
    market_state: str,
) -> PositionUpdateResult:
    updated = position.model_copy(deep=True)
    updated.last_price = current_price
    updated.unrealized_pnl_pct = _pnl_pct(updated.entry_price, current_price)
    updated.highest_price_since_entry = max(updated.highest_price_since_entry, current_price)

    reasons: list[str] = []

    if updated.risk_off_exit_enabled and market_state in RISK_OFF_STATES:
        updated.status = "closed"
        updated.remaining_position_size_pct = 0.0
        updated.realized_pnl_pct = updated.unrealized_pnl_pct
        reasons.append("Market state turned RISK_OFF")
        return PositionUpdateResult(updated, _build_state(updated, "SELL_EXIT", reasons))

    if current_price <= updated.active_stop_price:
        updated.status = "stopped"
        updated.remaining_position_size_pct = 0.0
        updated.realized_pnl_pct = updated.unrealized_pnl_pct
        reasons.append("Current price fell below active stop")
        return PositionUpdateResult(updated, _build_state(updated, "SELL_EXIT", reasons))

    if not updated.tp1_hit and current_price >= updated.tp1_price:
        updated.tp1_hit = True
        updated.tp1_hit_time = utc_now_iso()
        updated.remaining_position_size_pct = _reduce_position_size(updated, updated.tp1_reduce_pct)
        if updated.move_stop_to_breakeven_on_tp1:
            updated.active_stop_price = max(updated.active_stop_price, updated.entry_price)
        updated.status = "partially_reduced"
        if updated.move_stop_to_breakeven_on_tp1:
            reasons.append(f"TP1 reached: reduce {_format_pct(updated.tp1_reduce_pct)}% and move stop to breakeven")
        else:
            reasons.append(f"TP1 reached: reduce {_format_pct(updated.tp1_reduce_pct)}% and keep current stop")
        return PositionUpdateResult(updated, _build_state(updated, "SELL_REDUCE", reasons))

    if updated.tp1_hit and not updated.tp2_hit and current_price >= updated.tp2_price:
        updated.tp2_hit = True
        updated.tp2_hit_time = utc_now_iso()
        updated.remaining_position_size_pct = _reduce_position_size(updated, updated.tp2_reduce_pct)
        if updated.enable_trailing_on_tp2:
            updated.trailing_enabled = True
        updated.status = "partially_reduced"
        if updated.enable_trailing_on_tp2:
            reasons.append(f"TP2 reached: reduce another {_format_pct(updated.tp2_reduce_pct)}% and enable trailing stop")
        else:
            reasons.append(f"TP2 reached: reduce another {_format_pct(updated.tp2_reduce_pct)}% and keep trailing disabled")
        return PositionUpdateResult(updated, _build_state(updated, "ENABLE_TRAILING_STOP", reasons))

    if updated.trailing_enabled and updated.highest_price_since_entry > 0:
        drawdown_pct = ((updated.highest_price_since_entry - current_price) / updated.highest_price_since_entry) * 100.0
        if drawdown_pct >= updated.trailing_drawdown_pct:
            updated.status = "closed"
            updated.remaining_position_size_pct = 0.0
            updated.realized_pnl_pct = updated.unrealized_pnl_pct
            reasons.append(f"Trailing stop triggered after {drawdown_pct:.2f}% drawdown from peak")
            return PositionUpdateResult(updated, _build_state(updated, "SELL_EXIT", reasons))

    if updated.tp1_hit and updated.move_stop_to_breakeven_on_tp1 and updated.active_stop_price < updated.entry_price:
        reasons.append("TP1 already hit; stop should stay at or above breakeven")
        return PositionUpdateResult(updated, _build_state(updated, "MOVE_STOP_TO_BREAKEVEN", reasons))

    reasons.append("Position remains valid; hold")
    return PositionUpdateResult(updated, _build_state(updated, "HOLD", reasons))


def _position_changed(before: Position, after: Position) -> bool:
    before_data = before.model_dump(mode="json")
    after_data = after.model_dump(mode="json")
    return before_data != after_data


def evaluate_and_persist_position(
    position: Position,
    current_price: float,
    market_state: str,
    *,
    persist_trade_mutations: bool = True,
    base_dir: str | Path | None = None,
):
    result = evaluate_position(position, current_price=current_price, market_state=market_state)
    persisted_position = result.position if persist_trade_mutations else _build_live_managed_position(position, result.position, result.state)
    result.position = persisted_position
    result.changed = _position_changed(position, persisted_position)
    position_path = upsert_position(persisted_position, base_dir=base_dir)
    event_path = None
    if result.changed:
        event = build_position_event(persisted_position, result.state)
        event_path = append_position_event(event, base_dir=base_dir)
    return result, position_path, event_path
