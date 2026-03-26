from __future__ import annotations

from dataclasses import dataclass, field

from .execution_queue_recovery_cleanup import cleanup_execution_queue_after_recovery
from .live_inflight_state import save_live_inflight_state
from .runner_state import describe_runner_state_file, load_runner_state, save_runner_state
from .utils import utc_now_iso


@dataclass
class RunnerRecoveryResult:
    ok: bool
    state_path: str
    messages: list[str] = field(default_factory=list)
    state_meta: dict | None = None



def reset_runner_fuse(reason: str = 'manual_reset') -> RunnerRecoveryResult:
    cleanup = cleanup_execution_queue_after_recovery(clear_retry_counts=True, clear_processed_keys=False)
    inflight_path = save_live_inflight_state({'orders': {}}, base_dir=None)
    state = load_runner_state()
    next_state = {
        **state,
        'fuse_open': False,
        'consecutive_failures': 0,
        'last_cycle_status': 'reset',
        'last_cycle_error': None,
        'last_recovery_reason': reason,
        'last_recovery_at': utc_now_iso(),
        'warm_restart_cycles_remaining': 2,
        'last_recovery_cleanup': {
            'trimmed_retry_keys': cleanup.trimmed_retry_keys,
            'trimmed_processed_keys': cleanup.trimmed_processed_keys,
            'cleared_live_inflight': True,
        },
        'recovery_single_active_snapshot': {
            'status': active_trade_state.status,
            'active_symbol': active_trade_state.lock.active_symbol,
            'active_stage': active_trade_state.lock.active_stage,
            'lock_reason': active_trade_state.lock.lock_reason,
            'anomalies': active_trade_state.anomalies,
        },
    }
    path = save_runner_state(next_state)
    meta = describe_runner_state_file()
    return RunnerRecoveryResult(
        ok=True,
        state_path=str(path),
        state_meta=meta,
        messages=[
            *cleanup.messages,
            f'RECOVERY_LIVE_INFLIGHT_RESET path={inflight_path}',
            f'RUNNER_FUSE_RESET reason={reason}',
            f'RUNNER_STATE_RESET path={path}',
            f'RUNNER_STATE_RESET_META {meta}',
        ],
    )
