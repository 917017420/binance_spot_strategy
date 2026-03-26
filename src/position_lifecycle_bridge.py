from __future__ import annotations

from dataclasses import dataclass, field

from .models import PositionActionResult, PositionEvent


@dataclass
class PositionLifecycleEventView:
    position_id: str
    symbol: str
    lifecycle_stage: str
    source: str
    source_event_type: str | None = None
    source_action: str | None = None
    notes: list[str] = field(default_factory=list)



def build_lifecycle_view_from_event(event: PositionEvent) -> PositionLifecycleEventView:
    stage = 'position_observed'
    notes: list[str] = []
    if event.event_type == 'TP1_HIT':
        stage = 'tp1_reduced'
    elif event.event_type == 'TP2_HIT':
        stage = 'tp2_reduced_trailing'
    elif event.event_type in {'TRAILING_EXIT', 'RISK_OFF_EXIT'}:
        stage = 'fully_exited'
        notes.append('exit confirmed from event')
    elif event.event_type == 'STOP_EXIT':
        stage = 'stopped_out'
        notes.append('stop exit confirmed from event')
    elif event.event_type == 'POSITION_OPENED':
        stage = 'open_initial'
    return PositionLifecycleEventView(
        position_id=event.position_id,
        symbol=event.symbol,
        lifecycle_stage=stage,
        source='event',
        source_event_type=event.event_type,
        notes=notes,
    )



def build_lifecycle_view_from_action(action: PositionActionResult) -> PositionLifecycleEventView:
    stage = 'position_observed'
    notes: list[str] = []
    if (
        action.mode == 'live'
        and action.status == 'skipped'
        and (action.details or {}).get('skip_reason') == 'pending_live_management_order_exists'
    ):
        if action.action == 'SELL_REDUCE':
            stage = 'reduce_pending_fill'
            notes.append('existing reduce remains unresolved; resubmit suppressed')
        elif action.action == 'SELL_EXIT':
            stage = 'exit_pending_fill'
            notes.append('existing exit remains unresolved; resubmit suppressed')
    elif action.mode == 'live' and action.status == 'submitted':
        if action.action == 'SELL_REDUCE':
            stage = 'reduce_pending_fill'
            notes.append('reduce submitted; awaiting fill reconcile')
        elif action.action == 'SELL_EXIT':
            stage = 'exit_pending_fill'
            notes.append('exit submitted; awaiting fill reconcile')
    elif action.action == 'SELL_REDUCE':
        stage = 'tp1_reduced'
        notes.append('reduce action executed')
    elif action.action == 'SELL_EXIT' and action.resulting_position_status == 'closed':
        stage = 'fully_exited'
        notes.append('exit action closed position')
    elif action.action == 'SELL_EXIT' and action.resulting_position_status == 'stopped':
        stage = 'stopped_out'
        notes.append('exit action stopped position')
    return PositionLifecycleEventView(
        position_id=action.position_id,
        symbol=action.symbol,
        lifecycle_stage=stage,
        source='action',
        source_action=action.action,
        notes=notes,
    )
