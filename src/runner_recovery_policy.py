from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecoveryPolicyDecision:
    warm_restart_active: bool
    allow_queue_enqueue: bool
    allow_queue_worker: bool
    clear_processed_keys_on_recovery: bool
    next_sleep_seconds: float | None = None
    reason: str = ''
    notes: list[str] = field(default_factory=list)



def build_recovery_policy(state: dict) -> RecoveryPolicyDecision:
    last_recovery_reason = state.get('last_recovery_reason')
    warm_restart_cycles_remaining = int(state.get('warm_restart_cycles_remaining', 0) or 0)
    last_recovery_cleanup = state.get('last_recovery_cleanup') or {}
    trimmed_processed_keys = int(last_recovery_cleanup.get('trimmed_processed_keys', 0) or 0)
    recovery_single_active_snapshot = state.get('recovery_single_active_snapshot') or {}
    active_stage = recovery_single_active_snapshot.get('active_stage')
    active_symbol = recovery_single_active_snapshot.get('active_symbol')

    if last_recovery_reason and warm_restart_cycles_remaining > 0:
        return RecoveryPolicyDecision(
            warm_restart_active=True,
            allow_queue_enqueue=False,
            allow_queue_worker=True,
            clear_processed_keys_on_recovery=trimmed_processed_keys == 0 and warm_restart_cycles_remaining <= 1,
            next_sleep_seconds=30.0,
            reason='warm restart window active after recovery',
            notes=[
                f'last_recovery_reason={last_recovery_reason}',
                f'warm_restart_cycles_remaining={warm_restart_cycles_remaining}',
                f'trimmed_processed_keys={trimmed_processed_keys}',
                f'recovery_active_symbol={active_symbol}',
                f'recovery_active_stage={active_stage}',
            ],
        )

    return RecoveryPolicyDecision(
        warm_restart_active=False,
        allow_queue_enqueue=True,
        allow_queue_worker=True,
        clear_processed_keys_on_recovery=False,
        next_sleep_seconds=None,
        reason='normal cycle policy',
        notes=[],
    )
