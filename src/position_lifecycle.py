from __future__ import annotations

from dataclasses import dataclass, field

from .models import Position


@dataclass
class PositionLifecycleView:
    position_id: str
    symbol: str
    lifecycle_stage: str
    status: str
    exit_action: str | None = None
    notes: list[str] = field(default_factory=list)



def build_position_lifecycle(position: Position) -> PositionLifecycleView:
    exit_action: str | None = None
    if position.status == 'open' and not position.tp1_hit:
        stage = 'open_initial'
    elif position.tp1_hit and not position.tp2_hit and position.status in {'open', 'partially_reduced'}:
        stage = 'tp1_reduced'
    elif position.tp2_hit and position.status in {'open', 'partially_reduced'}:
        stage = 'tp2_reduced_trailing'
    elif position.status == 'closed':
        stage = 'fully_exited'
        exit_action = 'SELL_EXIT'
    elif position.status == 'stopped':
        stage = 'stopped_out'
        exit_action = 'SELL_EXIT'
    else:
        stage = 'custom_state'

    notes: list[str] = []
    if position.trailing_enabled:
        notes.append('trailing enabled')
    if position.tp1_hit:
        notes.append('tp1 already hit')
    if position.tp2_hit:
        notes.append('tp2 already hit')
    if position.remaining_position_size_pct <= 0:
        notes.append('fully reduced')

    return PositionLifecycleView(
        position_id=position.position_id,
        symbol=position.symbol,
        lifecycle_stage=stage,
        status=position.status,
        exit_action=exit_action,
        notes=notes,
    )
