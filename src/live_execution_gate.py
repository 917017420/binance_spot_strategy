from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LiveExecutionGateDecision:
    can_enqueue_live: bool
    can_process_live: bool
    blocked_reason: str | None = None
    blocked_reasons: list[str] = field(default_factory=list)
    stale_count: int = 0
    cooldown_count: int = 0
    inflight_pending_count: int = 0



def derive_live_execution_gate(
    *,
    stale_count: int,
    cooldown_count: int,
    inflight_pending_count: int,
) -> LiveExecutionGateDecision:
    blocked_reasons: list[str] = []

    if stale_count > 0:
        blocked_reasons.append('stale_live_inflight_detected')
    if cooldown_count > 0:
        blocked_reasons.append('post_escalation_cooldown_active')
    if inflight_pending_count > 0:
        blocked_reasons.append('live_submit_inflight_pending')

    can_enqueue_live = stale_count == 0 and cooldown_count == 0
    can_process_live = stale_count == 0 and cooldown_count == 0 and inflight_pending_count == 0
    blocked_reason = blocked_reasons[0] if blocked_reasons else None

    return LiveExecutionGateDecision(
        can_enqueue_live=can_enqueue_live,
        can_process_live=can_process_live,
        blocked_reason=blocked_reason,
        blocked_reasons=blocked_reasons,
        stale_count=stale_count,
        cooldown_count=cooldown_count,
        inflight_pending_count=inflight_pending_count,
    )
