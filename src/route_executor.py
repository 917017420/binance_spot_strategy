from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .entry_executor import execute_entry_candidate
from .live_route_executor import plan_live_route
from .models import PairAnalysis


@dataclass
class RouteExecutionResult:
    symbol: str
    route: str
    mode: str
    status: str
    message: str
    details: dict = field(default_factory=dict)



def execute_route_candidate(candidate: PairAnalysis, route: str, mode: str | None, total_equity_quote: float, settings: Settings | None = None) -> RouteExecutionResult:
    if route == 'live':
        if mode == 'live':
            plan = plan_live_route(candidate, total_equity_quote=total_equity_quote, settings=settings)
            return RouteExecutionResult(symbol=candidate.symbol, route=route, mode='live', status='planned', message=plan.message, details={**plan.details, 'execution_policy': 'live_only'})
        return RouteExecutionResult(symbol=candidate.symbol, route=route, mode=mode or 'none', status='armed', message='live route available but current mode is not live', details={'symbol': candidate.symbol, 'execution_policy': 'live_only'})

    if route == 'paper':
        if mode in {'dry_run', 'paper'}:
            result = execute_entry_candidate(candidate, total_equity_quote=total_equity_quote, mode=mode, settings=settings)
            return RouteExecutionResult(symbol=candidate.symbol, route=route, mode=mode, status='executed', message='paper route executed', details={**result, 'execution_policy': 'paper_or_dry_run'})
        return RouteExecutionResult(symbol=candidate.symbol, route=route, mode=mode or 'none', status='armed', message='paper route available but current mode is not paper/dry_run', details={'symbol': candidate.symbol, 'execution_policy': 'paper_or_dry_run'})

    if route == 'shadow':
        return RouteExecutionResult(symbol=candidate.symbol, route=route, mode=mode or 'shadow', status='tracked', message='shadow route tracked only', details={'symbol': candidate.symbol, 'execution_policy': 'shadow_track_only'})

    return RouteExecutionResult(symbol=candidate.symbol, route=route, mode=mode or 'none', status='skipped', message='route not executed in current mode', details={'symbol': candidate.symbol, 'execution_policy': 'deny_skip'})
