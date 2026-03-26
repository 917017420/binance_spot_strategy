from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExecutionReadyDecision:
    symbol: str
    route: str
    route_status: str
    execution_ready: bool
    blocked_by_policy: bool
    reason: str
    notes: list[str] = field(default_factory=list)



def build_execution_ready_decision(
    symbol: str,
    route: str,
    route_status: str,
    cycle_policy: str,
    position_init_status: str,
) -> ExecutionReadyDecision:
    blocked_by_policy = cycle_policy == 'monitor_only'
    notes: list[str] = [f'cycle_policy={cycle_policy}', f'position_init_status={position_init_status}']

    if blocked_by_policy:
        return ExecutionReadyDecision(
            symbol=symbol,
            route=route,
            route_status=route_status,
            execution_ready=False,
            blocked_by_policy=True,
            reason='cycle policy currently blocks new execution',
            notes=notes,
        )

    if route == 'deny' or route_status == 'skipped':
        return ExecutionReadyDecision(
            symbol=symbol,
            route=route,
            route_status=route_status,
            execution_ready=False,
            blocked_by_policy=False,
            reason='route denied or skipped',
            notes=notes,
        )

    if route in {'paper', 'live'} and route_status in {'executed', 'planned', 'armed'}:
        return ExecutionReadyDecision(
            symbol=symbol,
            route=route,
            route_status=route_status,
            execution_ready=True,
            blocked_by_policy=False,
            reason='candidate is execution-ready under current cycle policy',
            notes=notes,
        )

    if route == 'shadow':
        return ExecutionReadyDecision(
            symbol=symbol,
            route=route,
            route_status=route_status,
            execution_ready=False,
            blocked_by_policy=False,
            reason='shadow route is tracking-only',
            notes=notes,
        )

    return ExecutionReadyDecision(
        symbol=symbol,
        route=route,
        route_status=route_status,
        execution_ready=False,
        blocked_by_policy=False,
        reason='candidate is not ready for execution',
        notes=notes,
    )
