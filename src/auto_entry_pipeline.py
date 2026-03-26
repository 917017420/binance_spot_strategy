from __future__ import annotations

from dataclasses import dataclass

from .auto_entry_gate import evaluate_auto_entry
from .entry_executor import execute_entry_candidate
from .models import PairAnalysis


@dataclass
class AutoEntryPipelineResult:
    executed: int
    denied: int
    messages: list[str]


def run_auto_entry_pipeline(
    candidates: list[PairAnalysis],
    market_state: str,
    total_equity_quote: float,
    mode: str = 'dry_run',
) -> AutoEntryPipelineResult:
    executed = 0
    denied = 0
    messages: list[str] = []

    for candidate in candidates:
        decision = evaluate_auto_entry(candidate, market_state=market_state)
        if not decision.allow:
            denied += 1
            messages.append(f"DENY {candidate.symbol}: {'; '.join(decision.reasons)}")
            continue
        result = execute_entry_candidate(candidate, total_equity_quote=total_equity_quote, mode=mode)
        executed += 1
        messages.append(f"EXECUTE {candidate.symbol}: mode={mode} entry_action_path={result['entry_action_path']}")

    return AutoEntryPipelineResult(executed=executed, denied=denied, messages=messages)
