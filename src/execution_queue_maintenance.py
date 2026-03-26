from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .execution_candidate_queue import _execution_candidate_queue_path
from .execution_queue_log import _execution_queue_log_path
from .execution_queue_state import _execution_queue_state_path, load_execution_queue_state, save_execution_queue_state
from .utils import utc_now_iso


@dataclass
class ExecutionQueueMaintenanceResult:
    queue_compacted: int
    log_compacted: int
    state_trimmed: bool
    messages: list[str] = field(default_factory=list)



def _compact_jsonl(path: Path, keep_last: int) -> int:
    if not path.exists():
        return 0
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    original = len(lines)
    kept = lines[-keep_last:] if keep_last > 0 else []
    path.write_text(('\n'.join(kept) + ('\n' if kept else '')), encoding='utf-8')
    return max(original - len(kept), 0)



def compact_execution_artifacts(base_dir: str | Path | None = None, keep_queue_last: int = 100, keep_log_last: int = 300, keep_processed_keys_last: int = 300, keep_retry_keys_last: int = 100) -> ExecutionQueueMaintenanceResult:
    queue_path = _execution_candidate_queue_path(base_dir)
    log_path = _execution_queue_log_path(base_dir)
    state_path = _execution_queue_state_path(base_dir)

    queue_compacted = _compact_jsonl(queue_path, keep_queue_last)
    log_compacted = _compact_jsonl(log_path, keep_log_last)

    state = load_execution_queue_state(base_dir=base_dir)
    processed_keys = list(state.get('processed_keys', []))[-keep_processed_keys_last:]
    retry_counts = dict(list(dict(state.get('retry_counts', {})).items())[-keep_retry_keys_last:])
    trimmed = (
        processed_keys != state.get('processed_keys', []) or
        retry_counts != state.get('retry_counts', {})
    )
    if trimmed or not state_path.exists():
        save_execution_queue_state(
            {
                'processed_keys': processed_keys,
                'retry_counts': retry_counts,
                'updated_at': utc_now_iso(),
            },
            base_dir=base_dir,
        )

    messages = [
        f'QUEUE_COMPACTED removed={queue_compacted} path={queue_path}',
        f'QUEUE_LOG_COMPACTED removed={log_compacted} path={log_path}',
        f'QUEUE_STATE_TRIMMED trimmed={trimmed} path={state_path}',
    ]
    return ExecutionQueueMaintenanceResult(
        queue_compacted=queue_compacted,
        log_compacted=log_compacted,
        state_trimmed=trimmed,
        messages=messages,
    )
