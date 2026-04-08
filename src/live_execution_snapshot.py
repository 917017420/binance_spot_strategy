from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .control_plane_status import derive_control_plane_status
from .live_execution_gate import derive_live_execution_gate
from .execution_queue_log import _execution_queue_log_path
from .live_inflight_state import (
    detect_live_release_cooldown,
    detect_stale_live_inflight,
    load_live_inflight_state,
    load_live_order_residue,
    summarize_live_order_residue,
)
from .live_submit_state import load_live_submit_state, summarize_live_submit_state
from .runner_state import derive_runner_runtime_status, load_runner_state, load_runner_stop_signal
from .single_active_trade_state import build_single_active_trade_state
from .active_trade_release_log import _active_trade_release_log_path
from .order_lifecycle_log import _order_lifecycle_log_path, tail_order_lifecycle_events


@dataclass
class LiveExecutionSnapshot:
    status: str
    summary: dict = field(default_factory=dict)



def _tail_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    items: list[dict] = []
    for line in lines[-limit:]:
        if line.strip():
            items.append(json.loads(line))
    return items



def _archive_dir(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent / 'data' / 'execution'
    return root / 'archive'



def _latest_archived_queue_log(base_dir: str | Path | None = None) -> Path | None:
    archive_dir = _archive_dir(base_dir)
    if not archive_dir.exists():
        return None
    candidates = sorted(archive_dir.glob('*-execution_queue_log.jsonl'))
    if not candidates:
        return None
    return candidates[-1]



def _aggregate_recent_live_event_counts(live_records: list[dict]) -> dict:
    counters = {
        'submit_requested': 0,
        'submit_failed': 0,
        'retry_scheduled': 0,
        'dead_lettered': 0,
        'skipped_inflight': 0,
        'skipped_cooldown': 0,
        'skipped_duplicate': 0,
        'skipped_superseded': 0,
        'pending_real_submit': 0,
        'released_stale_inflight': 0,
    }
    for item in live_records:
        stage = item.get('stage')
        if stage in counters:
            counters[stage] += 1
    counters['window_size'] = len(live_records)
    return counters



def _pick_recent_live_events(live_records: list[dict]) -> dict:
    recent_submit = None
    recent_retry = None
    recent_dead_letter = None
    recent_pending = None
    recent_adapter_stage = None
    recent_inflight_skip = None
    recent_cooldown_skip = None
    recent_escalation_release = None

    for item in reversed(live_records):
        stage = item.get('stage')
        if recent_submit is None and stage == 'submit_requested':
            recent_submit = item
        if recent_retry is None and stage == 'retry_scheduled':
            recent_retry = item
        if recent_dead_letter is None and stage == 'dead_lettered':
            recent_dead_letter = item
        if recent_pending is None and stage == 'pending_real_submit':
            recent_pending = item
        if recent_adapter_stage is None and stage in {'adapter_stubbed', 'adapter_call', 'submit_failed'}:
            recent_adapter_stage = item
        if recent_inflight_skip is None and stage == 'skipped_inflight':
            recent_inflight_skip = item
        if recent_cooldown_skip is None and stage == 'skipped_cooldown':
            recent_cooldown_skip = item
        if recent_escalation_release is None and stage == 'released_stale_inflight':
            recent_escalation_release = item

    return {
        'submit_requested': recent_submit,
        'adapter_stage': recent_adapter_stage,
        'pending_real_submit': recent_pending,
        'retry_scheduled': recent_retry,
        'dead_lettered': recent_dead_letter,
        'skipped_inflight': recent_inflight_skip,
        'skipped_cooldown': recent_cooldown_skip,
        'released_stale_inflight': recent_escalation_release,
    }



def _tail_active_trade_releases(base_dir: str | Path | None = None, limit: int = 20) -> list[dict]:
    path = _active_trade_release_log_path(base_dir=base_dir)
    return _tail_jsonl(path, limit=limit)



def _derive_next_action_plan(*, control, runtime: dict, active_trade_state, inflight_residue_summary: dict, order_refresh: dict, submit_state_summary: dict) -> dict:
    active_symbol = active_trade_state.lock.active_symbol
    if runtime.get('start_blocked_by_stop_signal'):
        return {
            'level': 'runtime_blocked',
            'code': 'clear_stop_signal_before_restart',
            'summary': 'Resident runtime start is blocked by a pending stop signal; clear it before the next supervised launch.',
            'recommended_command': runtime.get('commands', {}).get('clear_stop') or 'python3 -m src.main clear-runner-stop',
        }
    if runtime.get('heartbeat_stale'):
        return {
            'level': 'runtime_attention',
            'code': 'inspect_stale_runtime',
            'summary': 'Resident runtime heartbeat is stale; inspect runtime and control-plane state before restarting.',
            'recommended_command': runtime.get('commands', {}).get('control_plane') or 'python3 -m src.main control-plane-brief',
        }
    if runtime.get('loop_active'):
        return {
            'level': 'observe_runtime',
            'code': 'resident_runtime_already_active',
            'summary': 'Resident runtime is already active; observe its heartbeat and control-plane ownership instead of launching a parallel cycle.',
            'recommended_command': runtime.get('commands', {}).get('observe') or 'python3 -m src.main runtime-status',
        }
    if inflight_residue_summary.get('needs_manual_attention'):
        orphan_symbols = inflight_residue_summary.get('orphan_symbols') or []
        return {
            'level': 'manual_attention',
            'code': 'inspect_orphan_partial_fill_residue',
            'summary': f"Inspect orphan partial-fill residue for {orphan_symbols} before trusting live-domain state.",
            'recommended_command': f"python3 -m src.main quarantine-local-residue --symbol {orphan_symbols[0]}" if orphan_symbols else 'python3 -m src.main local-residue-audit',
        }
    if control.status == 'anomalous':
        return {
            'level': 'repair',
            'code': 'repair_single_active_trade',
            'summary': 'Repair conflicting single-active-trade state before resuming live admission.',
            'recommended_command': 'python3 -m src.main repair-single-active-trade --dry-run',
        }
    if control.primary_reason == 'active_open_position_exists':
        return {
            'level': 'observe',
            'code': 'wait_for_active_position_lifecycle',
            'summary': f'Wait for {active_symbol} position lifecycle to progress toward exit before admitting a new live symbol.',
            'recommended_command': 'python3 -m src.main control-plane-brief',
        }
    if order_refresh.get('last_stage') == 'auth_skipped':
        return {
            'level': 'config_pending',
            'code': 'enable_private_exchange_refresh_later',
            'summary': 'Remote order refresh is intentionally skipped until private Binance credentials are enabled.',
            'recommended_command': 'python3 -m src.main binance-readiness-check',
        }
    if submit_state_summary.get('should_archive'):
        return {
            'level': 'cleanup',
            'code': 'archive_stale_submit_state',
            'summary': 'Current submit state looks archivable and should be cleared from the active control-plane view.',
            'recommended_command': 'python3 -m src.main reconcile-control-plane',
        }
    if control.can_push_live_now:
        return {
            'level': 'ready',
            'code': 'accept_next_live_candidate',
            'summary': 'Live admission is available; the system can accept the next eligible live candidate.',
            'recommended_command': 'python3 -m src.main auto-runner-once --action-mode dry_run',
        }
    if control.needs_manual_intervention:
        return {
            'level': 'manual_attention',
            'code': 'manual_review_required',
            'summary': 'Manual intervention is recommended before continuing live execution.',
            'recommended_command': 'python3 -m src.main live-execution-snapshot',
        }
    return {
        'level': 'observe',
        'code': 'continue_monitoring',
        'summary': 'Observe current control-plane state and continue monitor/runner cycles.',
        'recommended_command': 'python3 -m src.main control-plane-brief',
    }



def build_live_execution_snapshot(base_dir: str | Path | None = None, stale_after_seconds: float = 900.0, cooldown_seconds: float = 900.0) -> LiveExecutionSnapshot:
    live_submit_state = load_live_submit_state(base_dir=base_dir)
    live_inflight_state = load_live_inflight_state(base_dir=base_dir)
    stale_inflight = detect_stale_live_inflight(live_inflight_state, stale_after_seconds=stale_after_seconds)
    live_release_cooldown = detect_live_release_cooldown(live_inflight_state, cooldown_seconds=cooldown_seconds)
    runner_state = load_runner_state(base_dir=base_dir)
    runner_stop_signal = load_runner_stop_signal(base_dir=base_dir)
    runtime = derive_runner_runtime_status(runner_state, stop_signal=runner_stop_signal)
    active_trade_state = build_single_active_trade_state(base_dir=base_dir)

    queue_log_path = _execution_queue_log_path(base_dir=base_dir)
    queue_records = _tail_jsonl(queue_log_path, limit=200)
    archive_log_path = _latest_archived_queue_log(base_dir=base_dir)
    archived_queue_records = _tail_jsonl(archive_log_path, limit=200) if archive_log_path else []
    all_queue_records = queue_records + archived_queue_records
    live_records = [item for item in all_queue_records if item.get('route') == 'live']
    recent_events = _pick_recent_live_events(live_records)
    recent_event_counts = _aggregate_recent_live_event_counts(live_records)
    recent_active_trade_releases = _tail_active_trade_releases(base_dir=base_dir, limit=20)
    recent_order_lifecycle_events = tail_order_lifecycle_events(base_dir=base_dir, limit=20)
    order_lifecycle_log_path = _order_lifecycle_log_path(base_dir=base_dir)

    inflight_orders = live_inflight_state.get('orders') or {}
    released_orders = live_inflight_state.get('released') or {}
    inflight_pending = {
        k: v for k, v in inflight_orders.items()
        if v.get('status') in {'pending_real_submit', 'adapter_stubbed'}
    }
    inflight_residue = load_live_order_residue(live_inflight_state)
    inflight_residue_summary = summarize_live_order_residue(
        live_inflight_state,
        active_symbol=active_trade_state.lock.active_symbol,
        stale_after_seconds=1800.0,
    )

    stale_count = stale_inflight.get('count', 0) or 0
    cooldown_count = live_release_cooldown.get('count', 0) or 0
    escalated_count = int(runner_state.get('last_escalated_inflight_count', 0) or 0)
    last_submit_status = live_submit_state.get('last_submit_status')
    last_response = live_submit_state.get('last_response') or {}
    last_error = live_submit_state.get('last_error')
    active_live_symbols = {
        str(item.get('symbol'))
        for item in (active_trade_state.observed_positions or [])
        if item.get('symbol') and item.get('participates_in_live_control_plane')
    }
    inflight_symbols = {
        key.split('|')[0]
        for key in inflight_orders.keys()
        if key and '|' in key
    }
    submit_state_summary = summarize_live_submit_state(
        live_submit_state,
        active_symbols=active_live_symbols,
        inflight_symbols=inflight_symbols,
        stale_after_seconds=1800.0,
    )
    recent_reconcile_actions = list(runner_state.get('last_reconcile_actions') or [])
    recent_recovery_actions = [
        item for item in recent_reconcile_actions
        if str(item).startswith('LIVE_INFLIGHT_RECOVERY_') or str(item).startswith('LIVE_SUBMIT_STATE_ARCHIVED_LOCAL_PREVIEW')
    ]

    live_gate = derive_live_execution_gate(
        stale_count=stale_count,
        cooldown_count=cooldown_count,
        inflight_pending_count=len(inflight_pending),
    )

    control = derive_control_plane_status(
        runner_state=runner_state,
        stale_count=stale_count,
        cooldown_count=cooldown_count,
        escalated_count=escalated_count,
        inflight_pending_count=len(inflight_pending),
        recent_dead_lettered=recent_events['dead_lettered'] is not None,
        submit_failed=(last_response.get('status') == 'submit_failed'),
        active_trade_lock=active_trade_state.lock,
        inflight_residue_summary=inflight_residue_summary,
    )

    current_blockers = {
        'primary_reason': control.primary_reason,
        'live_enqueue_blocked': control.live_enqueue_blocked,
        'reasons': [
            reason for reason in control.reasons
            if reason in {
                'active_open_position_exists',
                'live_submit_pending',
                'live_submit_inflight_pending',
                'runner_fuse_open',
                'recovery_warm_restart_active',
                'post_escalation_cooldown_active',
                'stale_live_inflight_detected',
                'multiple_active_positions_detected',
                'multiple_live_inflight_detected',
                'live_domain_symbol_conflict',
                'orphan_partial_fill_residue_detected',
            }
        ],
        'active_lock_reason': active_trade_state.lock.lock_reason,
        'gate_blocked_reason': live_gate.blocked_reason,
    }

    historical_residue = {
        'recent_dead_letter_detected': recent_events['dead_lettered'] is not None,
        'recent_submit_failed': last_response.get('status') == 'submit_failed',
        'last_submit_symbol': live_submit_state.get('last_symbol'),
        'last_submit_status': last_submit_status,
        'submit_state_summary': submit_state_summary,
        'released_count': len(released_orders),
        'released_orders': released_orders,
        'inflight_residue_count': len(inflight_residue),
        'inflight_residue_orders': inflight_residue,
        'inflight_residue_summary': inflight_residue_summary,
        'escalated_count': escalated_count,
        'recent_queue_event_counts': recent_event_counts,
        'archive_log_path': str(archive_log_path) if archive_log_path else None,
        'recent_reconcile_actions': recent_reconcile_actions,
        'recent_recovery_actions': recent_recovery_actions,
    }

    recent_history = {
        'queue_events': {
            **recent_events,
            'counts': recent_event_counts,
            'current_log_path': str(queue_log_path),
            'archive_log_path': str(archive_log_path) if archive_log_path else None,
        },
        'active_trade_releases': recent_active_trade_releases,
        'order_lifecycle': {
            'count': len(recent_order_lifecycle_events),
            'events': recent_order_lifecycle_events,
            'log_path': str(order_lifecycle_log_path),
        },
        'last_live_submit': {
            'last_client_order_id': live_submit_state.get('last_client_order_id'),
            'last_submit_status': last_submit_status,
            'last_symbol': live_submit_state.get('last_symbol'),
            'last_response_status': last_response.get('status'),
            'last_error': last_error,
            'summary': submit_state_summary,
        },
    }

    next_action_plan = _derive_next_action_plan(
        control=control,
        runtime=runtime,
        active_trade_state=active_trade_state,
        inflight_residue_summary=inflight_residue_summary,
        order_refresh={
            'last_stage': runner_state.get('last_order_refresh_stage'),
        },
        submit_state_summary=submit_state_summary,
    )

    operational_observed_positions = [
        item
        for item in (active_trade_state.observed_positions or [])
        if item.get('participates_in_live_control_plane')
    ]
    excluded_simulation_positions_count = sum(
        1
        for item in (active_trade_state.observed_positions or [])
        if item.get('truth_domain') == 'simulation'
    )

    summary = {
        'current_state': {
            'status': control.status,
            'active_symbol': active_trade_state.lock.active_symbol,
            'active_stage': active_trade_state.lock.active_stage,
            'active_position_under_management': control.primary_reason == 'active_open_position_exists',
            'primary_reason': control.primary_reason,
            'can_push_live_now': control.can_push_live_now,
            'needs_manual_intervention': control.needs_manual_intervention,
            'live_domain_owner_symbol': active_trade_state.lock.active_symbol,
            'live_domain_stage': active_trade_state.lock.active_stage,
        },
        'current_blockers': current_blockers,
        'historical_residue': historical_residue,
        'recent_history': recent_history,
        'order_refresh': {
            'last_ok': runner_state.get('last_order_refresh_ok'),
            'last_found': runner_state.get('last_order_refresh_found'),
            'last_status': runner_state.get('last_order_refresh_status'),
            'last_stage': runner_state.get('last_order_refresh_stage'),
            'last_error': runner_state.get('last_order_refresh_error'),
            'last_ts': runner_state.get('last_order_refresh_ts'),
            'last_attempt_ts': runner_state.get('last_order_refresh_attempt_ts'),
            'next_after_ts': runner_state.get('next_order_refresh_after_ts'),
            'last_target_count': runner_state.get('last_order_refresh_target_count'),
            'last_refreshed_count': runner_state.get('last_order_refresh_refreshed_count'),
            'last_actions': runner_state.get('last_order_refresh_actions', []),
        },
        'next_action_plan': next_action_plan,
        'counts': {
            'stale': stale_count,
            'escalated': escalated_count,
            'cooldown': cooldown_count,
            'inflight': len(inflight_orders),
            'inflight_pending': len(inflight_pending),
            'inflight_residue': len(inflight_residue),
            'released': len(released_orders),
        },
        'blocked': {
            'deprecated': True,
            'preferred_replacement': 'current_blockers',
        },
        'runner': {
            'health_status': control.status,
            'fuse_open': runner_state.get('fuse_open'),
            'next_sleep_seconds': runner_state.get('next_sleep_seconds'),
            'runtime': runtime,
            'last_loop_mode': runner_state.get('last_loop_mode'),
            'last_loop_status': runner_state.get('last_loop_status'),
            'last_loop_action_mode': runner_state.get('last_loop_action_mode'),
            'last_loop_started_at': runner_state.get('last_loop_started_at'),
            'last_loop_finished_at': runner_state.get('last_loop_finished_at'),
            'last_loop_exit_reason': runner_state.get('last_loop_exit_reason'),
            'last_loop_cycle_target': runner_state.get('last_loop_cycle_target'),
            'last_loop_cycle_count': runner_state.get('last_loop_cycle_count', 0),
            'last_loop_heartbeat_interval_seconds': runner_state.get('last_loop_heartbeat_interval_seconds', 5.0),
            'last_loop_sleep_started_at': runner_state.get('last_loop_sleep_started_at'),
            'last_loop_sleep_until_at': runner_state.get('last_loop_sleep_until_at'),
            'last_loop_sleep_seconds': runner_state.get('last_loop_sleep_seconds', 0.0),
            'last_loop_sleep_remaining_seconds': runner_state.get('last_loop_sleep_remaining_seconds', 0.0),
            'last_stop_signal_at': runner_state.get('last_stop_signal_at'),
            'last_stop_signal_reason': runner_state.get('last_stop_signal_reason'),
            'last_cycle_stage': runner_state.get('last_cycle_stage'),
            'last_cycle_started_at': runner_state.get('last_cycle_started_at'),
            'last_cycle_finished_at': runner_state.get('last_cycle_finished_at'),
            'last_successful_cycle_at': runner_state.get('last_successful_cycle_at'),
            'last_heartbeat_at': runner_state.get('last_heartbeat_at'),
            'last_heartbeat_status': runner_state.get('last_heartbeat_status'),
            'warm_restart_cycles_remaining': runner_state.get('warm_restart_cycles_remaining'),
            'last_queue_failed': runner_state.get('last_queue_failed'),
            'last_queue_retried': runner_state.get('last_queue_retried'),
            'last_queue_dead_lettered': runner_state.get('last_queue_dead_lettered'),
            'last_queue_skipped_inflight': runner_state.get('last_queue_skipped_inflight', 0),
            'last_queue_skipped_duplicate': runner_state.get('last_queue_skipped_duplicate', 0),
            'last_queue_skipped_superseded': runner_state.get('last_queue_skipped_superseded', 0),
            'last_queue_skipped_cooldown': runner_state.get('last_queue_skipped_cooldown', 0),
            'last_queue_submit_failed': runner_state.get('last_queue_submit_failed', 0),
            'last_queue_retry_scheduled': runner_state.get('last_queue_retry_scheduled', 0),
            'last_stale_inflight_count': runner_state.get('last_stale_inflight_count'),
            'last_escalated_inflight_count': escalated_count,
            'last_live_release_cooldown_count': runner_state.get('last_live_release_cooldown_count', 0),
            'preferred_snapshot_fields': ['current_state', 'current_blockers', 'historical_residue'],
            'summary': {
                'status': control.status,
                'stale_count': stale_count,
                'escalated_count': escalated_count,
                'cooldown_count': cooldown_count,
                'can_push_live_now': control.can_push_live_now,
                'needs_manual_intervention': control.needs_manual_intervention,
                'runtime': runtime,
                'last_loop_mode': runner_state.get('last_loop_mode'),
                'last_loop_status': runner_state.get('last_loop_status'),
                'last_loop_action_mode': runner_state.get('last_loop_action_mode'),
                'last_loop_started_at': runner_state.get('last_loop_started_at'),
                'last_loop_finished_at': runner_state.get('last_loop_finished_at'),
                'last_loop_exit_reason': runner_state.get('last_loop_exit_reason'),
                'last_loop_cycle_target': runner_state.get('last_loop_cycle_target'),
                'last_loop_cycle_count': runner_state.get('last_loop_cycle_count', 0),
                'last_loop_heartbeat_interval_seconds': runner_state.get('last_loop_heartbeat_interval_seconds', 5.0),
                'last_loop_sleep_started_at': runner_state.get('last_loop_sleep_started_at'),
                'last_loop_sleep_until_at': runner_state.get('last_loop_sleep_until_at'),
                'last_loop_sleep_seconds': runner_state.get('last_loop_sleep_seconds', 0.0),
                'last_loop_sleep_remaining_seconds': runner_state.get('last_loop_sleep_remaining_seconds', 0.0),
                'last_stop_signal_at': runner_state.get('last_stop_signal_at'),
                'last_stop_signal_reason': runner_state.get('last_stop_signal_reason'),
                'last_heartbeat_at': runner_state.get('last_heartbeat_at'),
                'last_heartbeat_status': runner_state.get('last_heartbeat_status'),
                'last_cycle_started_at': runner_state.get('last_cycle_started_at'),
                'last_cycle_finished_at': runner_state.get('last_cycle_finished_at'),
                'last_successful_cycle_at': runner_state.get('last_successful_cycle_at'),
                'stale_cycle_recoveries': runner_state.get('stale_cycle_recoveries', 0),
                'monitor': runner_state.get('last_monitor_summary') or {},
                'archived_simulated_positions': runner_state.get('last_archived_simulated_positions', 0),
                'queue_worker': {
                    'skipped_inflight_count': runner_state.get('last_queue_skipped_inflight', 0),
                    'skipped_duplicate_count': runner_state.get('last_queue_skipped_duplicate', 0),
                    'skipped_superseded_count': runner_state.get('last_queue_skipped_superseded', 0),
                    'skipped_cooldown_count': runner_state.get('last_queue_skipped_cooldown', 0),
                    'skipped_single_active_lock_count': runner_state.get('last_queue_skipped_single_active_lock', 0),
                    'skipped_system_lock_count': runner_state.get('last_queue_skipped_system_lock', 0),
                    'skipped_symbol_conflict_count': runner_state.get('last_queue_skipped_symbol_conflict', 0),
                    'submit_failed_count': runner_state.get('last_queue_submit_failed', 0),
                    'retry_scheduled_count': runner_state.get('last_queue_retry_scheduled', 0),
                    'dead_lettered_count': runner_state.get('last_queue_dead_lettered', 0),
                },
                'deprecated_explanation_fields': True,
                'preferred_snapshot_fields': ['current_state', 'current_blockers', 'historical_residue'],
            },
        },
        'live_submit_state': {
            'last_client_order_id': live_submit_state.get('last_client_order_id'),
            'last_submit_status': last_submit_status,
            'last_symbol': live_submit_state.get('last_symbol'),
            'last_response_status': last_response.get('status'),
            'last_error': last_error,
            'summary': submit_state_summary,
            'archived_last_submit': live_submit_state.get('archived_last_submit'),
        },
        'runtime': runtime,
        'live_inflight': {
            'count': len(inflight_orders),
            'pending_count': len(inflight_pending),
            'residue_count': len(inflight_residue),
            'orders': inflight_orders,
            'residue_orders': inflight_residue,
            'stale': stale_inflight,
            'released_count': len(released_orders),
            'released': released_orders,
            'release_cooldown': live_release_cooldown,
        },
        'single_active_trade': {
            'status': active_trade_state.status,
            'active_symbol': active_trade_state.lock.active_symbol,
            'active_stage': active_trade_state.lock.active_stage,
            'lock_reason': active_trade_state.lock.lock_reason,
            'lock_owner': active_trade_state.lock.lock_owner,
            'current_position_id': active_trade_state.lock.current_position_id,
            'live_logical_key': active_trade_state.lock.live_logical_key,
            'blocking': active_trade_state.lock.blocking,
            'can_admit_new_live_symbol': active_trade_state.lock.can_admit_new_live_symbol,
            'needs_manual_intervention': active_trade_state.lock.needs_manual_intervention,
            'source_details': active_trade_state.lock.source_details,
            'observed_positions': operational_observed_positions,
            'excluded_simulation_positions_count': excluded_simulation_positions_count,
            'observed_inflight': active_trade_state.observed_inflight,
            'anomalies': active_trade_state.anomalies,
        },
        'control_plane': {
            'deprecated': True,
            'preferred_replacement': ['current_state', 'current_blockers'],
        },
        'recent_queue_events': {
            'deprecated': True,
            'preferred_replacement': 'recent_history.queue_events',
        },
        'recent_active_trade_releases': recent_active_trade_releases,
    }
    return LiveExecutionSnapshot(status=control.status, summary=summary)
