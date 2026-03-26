from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControlPlaneStatus:
    status: str
    primary_reason: str
    reasons: list[str] = field(default_factory=list)
    live_enqueue_blocked: bool = False
    needs_manual_intervention: bool = False
    can_push_live_now: bool = True
    counts: dict = field(default_factory=dict)



def derive_control_plane_status(
    *,
    runner_state: dict,
    stale_count: int,
    cooldown_count: int,
    escalated_count: int,
    inflight_pending_count: int,
    recent_dead_lettered: bool,
    submit_failed: bool,
    active_trade_lock=None,
    inflight_residue_summary: dict | None = None,
) -> ControlPlaneStatus:
    reasons: list[str] = []
    inflight_residue_summary = inflight_residue_summary or {}
    fuse_open = bool(runner_state.get('fuse_open'))
    warm_restart_active = runner_state.get('last_health_status') == 'warm_restart'

    if fuse_open:
        reasons.append('runner_fuse_open')
    if stale_count > 0:
        reasons.append('stale_live_inflight_detected')
    if cooldown_count > 0:
        reasons.append('post_escalation_cooldown_active')
    if warm_restart_active:
        reasons.append('recovery_warm_restart_active')
    if active_trade_lock and active_trade_lock.lock_reason and active_trade_lock.lock_reason not in reasons:
        reasons.append(active_trade_lock.lock_reason)
    if recent_dead_lettered:
        reasons.append('recent_dead_letter_detected')
    if submit_failed:
        reasons.append('recent_live_submit_failed')
    if escalated_count > 0 and stale_count == 0 and cooldown_count == 0:
        reasons.append('stale_live_inflight_escalation_handled_this_cycle')
    if inflight_pending_count > 0 and stale_count == 0:
        reasons.append('live_submit_pending')
    if inflight_residue_summary.get('needs_manual_attention'):
        reasons.append('orphan_partial_fill_residue_detected')

    if fuse_open:
        status = 'fused'
        primary_reason = 'runner_fuse_open'
    elif stale_count > 0:
        status = 'degraded'
        primary_reason = 'stale_live_inflight_detected'
    elif cooldown_count > 0:
        status = 'cooldown'
        primary_reason = 'post_escalation_cooldown_active'
    elif warm_restart_active:
        status = 'warm_restart'
        primary_reason = 'recovery_warm_restart_active'
    elif active_trade_lock and active_trade_lock.lock_reason in {'multiple_active_positions_detected', 'multiple_live_inflight_detected', 'live_domain_symbol_conflict'}:
        status = 'anomalous'
        primary_reason = active_trade_lock.lock_reason
    elif active_trade_lock and active_trade_lock.lock_reason == 'active_open_position_exists':
        status = 'locked'
        primary_reason = 'active_open_position_exists'
    elif recent_dead_lettered or submit_failed:
        status = 'degraded'
        primary_reason = 'recent_dead_letter_detected' if recent_dead_lettered else 'recent_live_submit_failed'
    elif inflight_pending_count > 0:
        status = 'pending'
        primary_reason = 'live_submit_pending'
    elif escalated_count > 0:
        status = 'degraded'
        primary_reason = 'stale_live_inflight_escalation_handled_this_cycle'
    else:
        status = 'healthy'
        primary_reason = 'cycle_completed_without_live_blockers'

    live_enqueue_blocked = primary_reason in {
        'stale_live_inflight_detected',
        'post_escalation_cooldown_active',
        'active_open_position_exists',
        'multiple_active_positions_detected',
        'multiple_live_inflight_detected',
        'live_domain_symbol_conflict',
        'runner_fuse_open',
        'recovery_warm_restart_active',
    }
    needs_manual_intervention = fuse_open or stale_count > 0 or primary_reason in {
        'multiple_active_positions_detected',
        'multiple_live_inflight_detected',
        'live_domain_symbol_conflict',
    } or inflight_residue_summary.get('needs_manual_attention', False)
    can_push_live_now = not live_enqueue_blocked and not fuse_open and not warm_restart_active and not inflight_residue_summary.get('needs_manual_attention', False)

    return ControlPlaneStatus(
        status=status,
        primary_reason=primary_reason,
        reasons=reasons,
        live_enqueue_blocked=live_enqueue_blocked,
        needs_manual_intervention=needs_manual_intervention,
        can_push_live_now=can_push_live_now,
        counts={
            'stale': stale_count,
            'cooldown': cooldown_count,
            'escalated': escalated_count,
            'inflight_pending': inflight_pending_count,
        },
    )
