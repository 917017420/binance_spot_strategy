from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .models import PairAnalysis
from .pending_confirmation import build_pending_confirmation
from .position_initializer import build_position_from_execution, persist_initialized_position
from .executor import append_execution_result, build_dry_run_execution
from .utils import ensure_directory


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
ENTRY_ACTIONS_FILE = DEFAULT_EXECUTION_DIR / 'entry_actions.jsonl'


def _entry_actions_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / ENTRY_ACTIONS_FILE.name


def append_entry_action(payload: dict, base_dir: str | Path | None = None) -> Path:
    path = _entry_actions_path(base_dir)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return path


def execute_entry_candidate(
    analysis: PairAnalysis,
    total_equity_quote: float,
    mode: str = 'dry_run',
    *,
    settings: Settings | None = None,
) -> dict:
    confirmation = build_pending_confirmation(
        analysis,
        requested_position_size_pct=analysis.position_size_pct or 5.0,
        ttl_minutes=15,
        trigger_source='priority_list',
    )
    confirmation.status = 'confirmed'
    execution = build_dry_run_execution(
        confirmation,
        total_equity_quote=total_equity_quote,
        reference_price=analysis.indicators_1h.close,
    )
    if mode == 'dry_run':
        execution.mode = 'dry_run'
        execution.status = 'simulated'
    elif mode == 'paper':
        execution.mode = 'paper'
        execution.status = 'paper_submitted'
    else:
        execution.mode = 'live'
        execution.status = 'submitted'
    execution_path = append_execution_result(execution)
    position = build_position_from_execution(
        confirmation,
        execution,
        exit_settings=(settings.exit if settings is not None else None),
    )
    position_path, position_event_path = persist_initialized_position(position)
    action_record = {
        'symbol': analysis.symbol,
        'mode': mode,
        'decision_action': analysis.decision_action,
        'execution_stage': analysis.execution_stage,
        'attention_level': analysis.attention_level,
        'position_size_pct': analysis.position_size_pct,
        'execution_path': str(execution_path),
        'position_path': str(position_path),
        'position_event_path': str(position_event_path),
    }
    entry_action_path = append_entry_action(action_record)
    return {
        'ok': True,
        'symbol': analysis.symbol,
        'mode': mode,
        'entry_action_path': str(entry_action_path),
        'execution_path': str(execution_path),
        'position_path': str(position_path),
        'position_event_path': str(position_event_path),
    }
