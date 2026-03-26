from __future__ import annotations

from dataclasses import dataclass, field

from .models import PairAnalysis
from .route_executor import RouteExecutionResult


@dataclass
class RouteLifecycleView:
    symbol: str
    route: str
    execution_status: str
    execution_mode: str
    position_init_status: str
    position_path: str | None = None
    position_event_path: str | None = None
    entry_action_path: str | None = None
    execution_path: str | None = None
    notes: list[str] = field(default_factory=list)



def build_route_lifecycle_view(candidate: PairAnalysis, route_result: RouteExecutionResult) -> RouteLifecycleView:
    details = route_result.details or {}
    if route_result.status == 'executed':
        init_status = 'position_initialized' if details.get('position_path') else 'execution_without_position_state'
    elif route_result.status == 'tracked':
        init_status = 'not_applicable_shadow_tracking'
    elif route_result.status == 'planned':
        init_status = 'planned_live_not_submitted'
    elif route_result.status == 'armed':
        init_status = 'awaiting_matching_mode'
    else:
        init_status = 'not_initialized'

    notes: list[str] = []
    if details.get('entry_action_path'):
        notes.append('entry action log available')
    if details.get('position_path'):
        notes.append('position state persisted')
    if details.get('position_event_path'):
        notes.append('position event log available')

    return RouteLifecycleView(
        symbol=candidate.symbol,
        route=route_result.route,
        execution_status=route_result.status,
        execution_mode=route_result.mode,
        position_init_status=init_status,
        position_path=details.get('position_path'),
        position_event_path=details.get('position_event_path'),
        entry_action_path=details.get('entry_action_path'),
        execution_path=details.get('execution_path'),
        notes=notes,
    )
