from __future__ import annotations

from pathlib import Path

from .live_execution_snapshot import build_live_execution_snapshot



def format_control_plane_brief(base_dir: str | Path | None = None) -> str:
    snapshot = build_live_execution_snapshot(base_dir=base_dir)
    summary = snapshot.summary
    current_state = summary.get('current_state') or {}
    current_blockers = summary.get('current_blockers') or {}
    historical_residue = summary.get('historical_residue') or {}
    inflight_residue_summary = historical_residue.get('inflight_residue_summary') or {}
    submit_state_summary = historical_residue.get('submit_state_summary') or {}
    recent_history = summary.get('recent_history') or {}
    queue_events = recent_history.get('queue_events') or {}
    last_live_submit = recent_history.get('last_live_submit') or {}
    order_lifecycle = recent_history.get('order_lifecycle') or {}
    lifecycle_events = order_lifecycle.get('events') or []
    runner_summary = ((summary.get('runner') or {}).get('summary')) or {}
    order_refresh = summary.get('order_refresh') or {}
    next_action_plan = summary.get('next_action_plan') or {}

    status = current_state.get('status') or 'unknown'
    active_symbol = current_state.get('active_symbol') or 'none'
    active_stage = current_state.get('active_stage') or 'none'
    primary_reason = current_state.get('primary_reason') or current_blockers.get('primary_reason') or 'none'
    can_push_live_now = current_state.get('can_push_live_now')
    needs_manual_intervention = current_state.get('needs_manual_intervention')
    active_position_under_management = current_state.get('active_position_under_management')

    blocker_lines = current_blockers.get('reasons') or []
    if not blocker_lines:
        blocker_lines = ['none']

    history_lines = [
        f"last_heartbeat_at={runner_summary.get('last_heartbeat_at')}",
        f"last_heartbeat_status={runner_summary.get('last_heartbeat_status')}",
        f"last_cycle_started_at={runner_summary.get('last_cycle_started_at')}",
        f"last_cycle_finished_at={runner_summary.get('last_cycle_finished_at')}",
        f"last_successful_cycle_at={runner_summary.get('last_successful_cycle_at')}",
        f"stale_cycle_recoveries={runner_summary.get('stale_cycle_recoveries', 0)}",
        f"monitor_failed={((runner_summary.get('monitor') or {}).get('failed'))}",
        f"archived_simulated_positions={runner_summary.get('archived_simulated_positions', 0)}",
        f"recent_dead_letter_detected={historical_residue.get('recent_dead_letter_detected')}",
        f"last_submit_symbol={historical_residue.get('last_submit_symbol')}",
        f"last_submit_status={historical_residue.get('last_submit_status')}",
        f"submit_side={submit_state_summary.get('submit_side')}",
        f"submit_state_classification={submit_state_summary.get('classification')}",
        f"submit_order_terminality={submit_state_summary.get('order_terminality')}",
        f"submit_flow_terminality={submit_state_summary.get('flow_terminality')}",
        f"submit_flow_reason={submit_state_summary.get('flow_reason')}",
        f"submit_state_should_archive={submit_state_summary.get('should_archive')}",
        f"released_count={historical_residue.get('released_count')}",
        f"inflight_residue_count={historical_residue.get('inflight_residue_count')}",
        f"inflight_residue_partial_fill_count={inflight_residue_summary.get('partial_fill_count')}",
        f"inflight_residue_orphan_symbols={inflight_residue_summary.get('orphan_symbols')}",
        f"escalated_count={historical_residue.get('escalated_count')}",
    ]

    queue_worker = runner_summary.get('queue_worker') or {}
    queue_lines = [
        f"skipped_inflight={queue_worker.get('skipped_inflight_count', 0)}",
        f"skipped_cooldown={queue_worker.get('skipped_cooldown_count', 0)}",
        f"skipped_single_active_lock={queue_worker.get('skipped_single_active_lock_count', 0)}",
        f"skipped_system_lock={queue_worker.get('skipped_system_lock_count', 0)}",
        f"skipped_symbol_conflict={queue_worker.get('skipped_symbol_conflict_count', 0)}",
        f"submit_failed={queue_worker.get('submit_failed_count', 0)}",
        f"retry_scheduled={queue_worker.get('retry_scheduled_count', 0)}",
        f"dead_lettered={queue_worker.get('dead_lettered_count', 0)}",
    ]

    next_action = next_action_plan.get('summary') or 'Observe current control-plane state and continue monitor/runner cycles.'

    sections = [
        'CURRENT',
        f"- status: {status}",
        f"- active_symbol: {active_symbol}",
        f"- active_stage: {active_stage}",
        f"- primary_reason: {primary_reason}",
        f"- active_position_under_management: {active_position_under_management}",
        f"- can_push_live_now: {can_push_live_now}",
        f"- needs_manual_intervention: {needs_manual_intervention}",
        '',
        'BLOCKERS',
        *[f"- {item}" for item in blocker_lines],
        '',
        'HISTORY',
        *[f"- {item}" for item in history_lines],
        '',
        'QUEUE / WORKER',
        *[f"- {item}" for item in queue_lines],
        '',
        'RECENT',
        f"- last_submit_symbol: {last_live_submit.get('last_symbol')}",
        f"- last_submit_status: {last_live_submit.get('last_submit_status')}",
        f"- recent_dead_letter_stage_present: {queue_events.get('dead_lettered') is not None}",
        f"- recent_release_stage_present: {queue_events.get('released_stale_inflight') is not None}",
        f"- last_order_refresh_ok: {order_refresh.get('last_ok')}",
        f"- last_order_refresh_found: {order_refresh.get('last_found')}",
        f"- last_order_refresh_status: {order_refresh.get('last_status')}",
        f"- last_order_refresh_stage: {order_refresh.get('last_stage')}",
        f"- last_order_refresh_error: {order_refresh.get('last_error')}",
        f"- last_order_refresh_ts: {order_refresh.get('last_ts')}",
        f"- last_order_refresh_attempt_ts: {order_refresh.get('last_attempt_ts')}",
        f"- next_order_refresh_after_ts: {order_refresh.get('next_after_ts')}",
        f"- last_order_refresh_target_count: {order_refresh.get('last_target_count')}",
        f"- last_order_refresh_refreshed_count: {order_refresh.get('last_refreshed_count')}",
        f"- recent_order_lifecycle_events: {len(lifecycle_events)}",
        f"- last_order_lifecycle_event: {lifecycle_events[-1].get('event') if lifecycle_events else None}",
        '',
        'NEXT ACTION',
        f"- {next_action}",
        f"- action_level: {next_action_plan.get('level')}",
        f"- action_code: {next_action_plan.get('code')}",
        f"- recommended_command: {next_action_plan.get('recommended_command')}",
    ]
    return '\n'.join(sections)
