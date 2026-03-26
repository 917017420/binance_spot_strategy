from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunnerHealthSummary:
    status: str
    reason: str



def build_runner_health_summary(*, fuse_open: bool, warm_restart_active: bool, queue_dead_lettered: int, queue_failed: int, stale_inflight_count: int = 0, live_release_cooldown_count: int = 0, escalated_inflight_count: int = 0, monitor_failed_count: int = 0, stale_cycle_recovered: bool = False) -> RunnerHealthSummary:
    if fuse_open:
        return RunnerHealthSummary(status='fused', reason='queue dead-letter detected')
    if stale_cycle_recovered:
        return RunnerHealthSummary(status='degraded', reason='stale runner cycle recovered this cycle')
    if monitor_failed_count > 0:
        return RunnerHealthSummary(status='degraded', reason='position monitor errors detected')
    if stale_inflight_count > 0:
        return RunnerHealthSummary(status='degraded', reason='stale live inflight detected')
    if live_release_cooldown_count > 0:
        return RunnerHealthSummary(status='degraded', reason='post-escalation live cooldown active')
    if escalated_inflight_count > 0:
        return RunnerHealthSummary(status='degraded', reason='stale live inflight escalation handled this cycle')
    if warm_restart_active:
        return RunnerHealthSummary(status='warm_restart', reason='recovery warm restart window active')
    if queue_dead_lettered > 0 or queue_failed > 0:
        return RunnerHealthSummary(status='degraded', reason='queue worker pressure detected')
    return RunnerHealthSummary(status='healthy', reason='cycle completed without queue pressure')
