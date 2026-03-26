from __future__ import annotations

from dataclasses import dataclass, field

from .execution_queue_state import load_execution_queue_state, save_execution_queue_state


@dataclass
class ExecutionQueueRecoveryCleanupResult:
    ok: bool
    trimmed_retry_keys: int = 0
    trimmed_processed_keys: int = 0
    messages: list[str] = field(default_factory=list)



def cleanup_execution_queue_after_recovery(clear_retry_counts: bool = True, clear_processed_keys: bool = False) -> ExecutionQueueRecoveryCleanupResult:
    state = load_execution_queue_state()
    retry_counts = state.get('retry_counts') or {}
    processed_keys = state.get('processed_keys') or []

    trimmed_retry_keys = len(retry_counts) if clear_retry_counts else 0
    trimmed_processed_keys = len(processed_keys) if clear_processed_keys else 0

    if clear_retry_counts:
        state['retry_counts'] = {}
    if clear_processed_keys:
        state['processed_keys'] = []

    path = save_execution_queue_state(state)
    messages: list[str] = []
    if clear_retry_counts:
        messages.append(f'RECOVERY_QUEUE_CLEANUP retry_counts_cleared={trimmed_retry_keys} path={path}')
    if clear_processed_keys:
        messages.append(f'RECOVERY_QUEUE_CLEANUP processed_keys_cleared={trimmed_processed_keys} path={path}')
    if not messages:
        messages.append(f'RECOVERY_QUEUE_CLEANUP noop path={path}')

    return ExecutionQueueRecoveryCleanupResult(
        ok=True,
        trimmed_retry_keys=trimmed_retry_keys,
        trimmed_processed_keys=trimmed_processed_keys,
        messages=messages,
    )
