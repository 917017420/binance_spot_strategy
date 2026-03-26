from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .live_submit_executor import build_live_submit_plan
from .models import PairAnalysis


@dataclass
class LiveRoutePlan:
    symbol: str
    status: str
    message: str
    details: dict = field(default_factory=dict)



def plan_live_route(candidate: PairAnalysis, total_equity_quote: float, settings: Settings | None = None) -> LiveRoutePlan:
    submit_plan = build_live_submit_plan(candidate, total_equity_quote=total_equity_quote, settings=settings)
    return LiveRoutePlan(
        symbol=candidate.symbol,
        status=submit_plan.status,
        message=submit_plan.message,
        details=submit_plan.details,
    )
