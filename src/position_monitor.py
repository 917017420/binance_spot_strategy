from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .market_data_service import fetch_market_regime_baseline, fetch_symbol_last_price
from .position_action_executor import execute_position_action
from .position_lifecycle_event_log import snapshot_position_lifecycle_events
from .position_lifecycle_store import snapshot_given_positions
from .control_plane_reconcile import reconcile_control_plane_state
from .position_manager import evaluate_and_persist_position
from .positions_store import load_monitor_positions


@dataclass
class PositionMonitorResult:
    scanned: int
    updated: int
    messages: list[str] = field(default_factory=list)
    failed: int = 0
    reconcile_actions: list[str] = field(default_factory=list)



def run_position_monitor(current_price: float, market_state: str, action_mode: str = "dry_run", *, base_dir: str | Path | None = None) -> PositionMonitorResult:
    positions = load_monitor_positions(action_mode=action_mode, base_dir=base_dir)
    scanned = 0
    updated = 0
    failed = 0
    messages: list[str] = []
    updated_positions = []

    for position in positions:
        scanned += 1
        try:
            result, position_path, event_path = evaluate_and_persist_position(
                position,
                current_price=current_price,
                market_state=market_state,
                persist_trade_mutations=action_mode != 'live',
                base_dir=base_dir,
            )
            updated_positions.append(result.position)
            action_path = None
            action_event_path = None
            if result.state.suggested_action in {"SELL_REDUCE", "SELL_EXIT", "ENABLE_TRAILING_STOP"}:
                _, action_path, action_event_path = execute_position_action(
                    result.position,
                    result.state,
                    mode=action_mode,
                    base_dir=base_dir,
                )
            if result.changed:
                updated += 1
            messages.append(
                f"{result.position.symbol} price={current_price} {result.state.suggested_action} status={result.position.status} "
                f"remaining={result.position.remaining_position_size_pct} changed={result.changed} "
                f"position_path={position_path} event_path={event_path} action_path={action_path} action_event_path={action_event_path}"
            )
        except Exception as error:
            failed += 1
            messages.append(
                f'POSITION_MONITOR_ERROR symbol={position.symbol} position_id={position.position_id} '
                f'error={type(error).__name__}: {error}'
            )

    lifecycle_path = snapshot_given_positions(updated_positions, base_dir=base_dir)
    lifecycle_event_path = snapshot_position_lifecycle_events(base_dir=base_dir)
    reconcile = reconcile_control_plane_state(base_dir=base_dir)
    messages.append(f"POSITION_LIFECYCLE_SNAPSHOT path={lifecycle_path}")
    messages.append(f"POSITION_LIFECYCLE_EVENT_SNAPSHOT path={lifecycle_event_path}")
    messages.extend(reconcile.actions)
    messages.append(
        f'POSITION_MONITOR_SUMMARY scanned={scanned} updated={updated} failed={failed} reconcile_actions={len(reconcile.actions)}'
    )
    return PositionMonitorResult(
        scanned=scanned,
        updated=updated,
        failed=failed,
        messages=messages,
        reconcile_actions=list(reconcile.actions),
    )



def run_position_monitor_auto(config_path: str, env_file: str, action_mode: str = "dry_run", *, base_dir: str | Path | None = None) -> PositionMonitorResult:
    positions = load_monitor_positions(action_mode=action_mode, base_dir=base_dir)
    scanned = 0
    updated = 0
    failed = 0
    messages: list[str] = []
    updated_positions = []
    market_state = fetch_market_regime_baseline(config_path=config_path, env_file=env_file)

    for position in positions:
        scanned += 1
        try:
            symbol_price = fetch_symbol_last_price(config_path=config_path, env_file=env_file, symbol=position.symbol)
            result, position_path, event_path = evaluate_and_persist_position(
                position,
                current_price=symbol_price,
                market_state=market_state,
                persist_trade_mutations=action_mode != 'live',
                base_dir=base_dir,
            )
            updated_positions.append(result.position)
            action_path = None
            action_event_path = None
            if result.state.suggested_action in {"SELL_REDUCE", "SELL_EXIT", "ENABLE_TRAILING_STOP"}:
                _, action_path, action_event_path = execute_position_action(
                    result.position,
                    result.state,
                    mode=action_mode,
                    base_dir=base_dir,
                )
            if result.changed:
                updated += 1
            messages.append(
                f"{result.position.symbol} price={symbol_price} {result.state.suggested_action} status={result.position.status} "
                f"remaining={result.position.remaining_position_size_pct} changed={result.changed} "
                f"position_path={position_path} event_path={event_path} action_path={action_path} action_event_path={action_event_path}"
            )
        except Exception as error:
            failed += 1
            messages.append(
                f'POSITION_MONITOR_ERROR symbol={position.symbol} position_id={position.position_id} '
                f'error={type(error).__name__}: {error}'
            )

    lifecycle_path = snapshot_given_positions(updated_positions, base_dir=base_dir)
    lifecycle_event_path = snapshot_position_lifecycle_events(base_dir=base_dir)
    reconcile = reconcile_control_plane_state(base_dir=base_dir)
    messages.append(f"POSITION_LIFECYCLE_SNAPSHOT path={lifecycle_path}")
    messages.append(f"POSITION_LIFECYCLE_EVENT_SNAPSHOT path={lifecycle_event_path}")
    messages.extend(reconcile.actions)
    messages.append(
        f'POSITION_MONITOR_SUMMARY scanned={scanned} updated={updated} failed={failed} reconcile_actions={len(reconcile.actions)}'
    )
    return PositionMonitorResult(
        scanned=scanned,
        updated=updated,
        failed=failed,
        messages=messages,
        reconcile_actions=list(reconcile.actions),
    )
