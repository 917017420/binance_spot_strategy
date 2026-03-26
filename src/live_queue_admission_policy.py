from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .execution_candidate_queue import _execution_candidate_queue_path
from .execution_queue_state import load_execution_queue_state
from .live_execution_gate import LiveExecutionGateDecision, derive_live_execution_gate
from .single_active_trade_state import ActiveTradeLock, build_single_active_trade_state


@dataclass
class LiveQueueAdmissionDecision:
    allow_enqueue: bool
    allow_process: bool
    allow_latest_record: bool
    blocked_reason: str | None = None
    blocked_reasons: list[str] = field(default_factory=list)
    duplicate_reason: str | None = None
    superseded_reason: str | None = None
    latest_only_enabled: bool = True
    live_gate: LiveExecutionGateDecision | None = None
    active_trade_lock: ActiveTradeLock | None = None
    live_gate_blocked_reason: str | None = None
    live_gate_blocked_reasons: list[str] = field(default_factory=list)
    active_trade_blocked_reasons: list[str] = field(default_factory=list)
    queue_blocked_reasons: list[str] = field(default_factory=list)

    def to_contract(self) -> dict:
        live_gate = self.live_gate
        active_trade_lock = self.active_trade_lock
        return {
            'allow_enqueue': self.allow_enqueue,
            'allow_process': self.allow_process,
            'allow_latest_record': self.allow_latest_record,
            'enqueue_blocked': not self.allow_enqueue,
            'process_blocked': not self.allow_process,
            'primary_blocked_reason': self.blocked_reason,
            'blocked_reasons': list(self.blocked_reasons),
            'blocked_reasons_by_source': {
                'live_gate': list(self.live_gate_blocked_reasons),
                'single_active_trade': list(self.active_trade_blocked_reasons),
                'queue_record': list(self.queue_blocked_reasons),
            },
            'live_gate': {
                'can_enqueue_live': live_gate.can_enqueue_live if live_gate else True,
                'can_process_live': live_gate.can_process_live if live_gate else True,
                'blocked_reason': self.live_gate_blocked_reason,
                'blocked_reasons': list(self.live_gate_blocked_reasons),
                'stale_count': live_gate.stale_count if live_gate else 0,
                'cooldown_count': live_gate.cooldown_count if live_gate else 0,
                'inflight_pending_count': live_gate.inflight_pending_count if live_gate else 0,
            },
            'single_active_trade': {
                'blocking': bool(active_trade_lock.blocking) if active_trade_lock else False,
                'active_symbol': active_trade_lock.active_symbol if active_trade_lock else None,
                'lock_reason': active_trade_lock.lock_reason if active_trade_lock else None,
                'lock_owner': active_trade_lock.lock_owner if active_trade_lock else None,
                'blocked_reasons': list(self.active_trade_blocked_reasons),
                'source_details': active_trade_lock.source_details if active_trade_lock else {},
            },
            'queue_record': {
                'latest_only_enabled': self.latest_only_enabled,
                'duplicate_reason': self.duplicate_reason,
                'superseded_reason': self.superseded_reason,
                'blocked_reasons': list(self.queue_blocked_reasons),
            },
        }



def _load_queue_records(base_dir: str | Path | None = None) -> list[dict]:
    path = _execution_candidate_queue_path(base_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]



def _build_record_key(record: dict) -> str:
    return f"{record.get('symbol')}|{record.get('route')}|{record.get('route_status')}|{record.get('queued_at')}"



def _build_live_dedupe_key(symbol: str, route: str, route_status: str) -> str:
    return f"{symbol}|{route}|{route_status}"



def _derive_active_trade_block_reasons(symbol: str | None, route: str | None, base_dir: str | Path | None = None) -> tuple[list[str], ActiveTradeLock | None]:
    if route != 'live':
        return [], None

    active_trade_state = build_single_active_trade_state(base_dir=base_dir)
    lock = active_trade_state.lock
    if not lock.blocking:
        return [], lock

    lock_reason = lock.lock_reason or 'single_active_trade_locked'
    if lock_reason in {
        'runner_fuse_open',
        'recovery_warm_restart_active',
        'multiple_active_positions_detected',
        'multiple_live_inflight_detected',
        'live_domain_symbol_conflict',
    }:
        return [lock_reason], lock

    if lock_reason == 'active_open_position_exists':
        if lock.active_symbol == symbol:
            return ['active_open_position_exists'], lock
        return ['single_active_trade_locked_by_other_symbol'], lock

    if lock_reason == 'live_submit_inflight_pending':
        if lock.active_symbol == symbol:
            return ['live_submit_inflight_pending'], lock
        return ['single_active_trade_locked_by_other_symbol'], lock

    if lock_reason == 'post_escalation_cooldown_active':
        return ['post_escalation_cooldown_active'], lock

    if lock.active_symbol not in {None, symbol}:
        return ['single_active_trade_locked_by_other_symbol'], lock

    return [lock_reason], lock



def derive_live_queue_admission_policy(
    *,
    stale_count: int,
    cooldown_count: int,
    inflight_pending_count: int,
    already_processed: bool = False,
    is_latest_record: bool = True,
    symbol: str | None = None,
    route: str | None = None,
    base_dir: str | Path | None = None,
) -> LiveQueueAdmissionDecision:
    live_gate = derive_live_execution_gate(
        stale_count=stale_count,
        cooldown_count=cooldown_count,
        inflight_pending_count=inflight_pending_count,
    )

    duplicate_reason = 'already_processed_queue_key' if already_processed else None
    superseded_reason = 'superseded_by_latest_live_record' if not is_latest_record else None
    allow_latest_record = is_latest_record

    active_trade_reasons, active_trade_lock = _derive_active_trade_block_reasons(symbol, route, base_dir=base_dir)
    queue_blocked_reasons: list[str] = []

    blocked_reasons = list(live_gate.blocked_reasons)
    blocked_reasons.extend(active_trade_reasons)
    if duplicate_reason:
        queue_blocked_reasons.append(duplicate_reason)
    if superseded_reason:
        queue_blocked_reasons.append(superseded_reason)
    blocked_reasons.extend(queue_blocked_reasons)

    blocked_reason = blocked_reasons[0] if blocked_reasons else None
    allow_enqueue = live_gate.can_enqueue_live and allow_latest_record and not already_processed and not active_trade_reasons
    allow_process = live_gate.can_process_live and allow_latest_record and not already_processed and not active_trade_reasons

    return LiveQueueAdmissionDecision(
        allow_enqueue=allow_enqueue,
        allow_process=allow_process,
        allow_latest_record=allow_latest_record,
        blocked_reason=blocked_reason,
        blocked_reasons=blocked_reasons,
        duplicate_reason=duplicate_reason,
        superseded_reason=superseded_reason,
        latest_only_enabled=True,
        live_gate=live_gate,
        active_trade_lock=active_trade_lock,
        live_gate_blocked_reason=live_gate.blocked_reason,
        live_gate_blocked_reasons=list(live_gate.blocked_reasons),
        active_trade_blocked_reasons=active_trade_reasons,
        queue_blocked_reasons=queue_blocked_reasons,
    )



def derive_enqueue_admission_precheck(
    *,
    symbol: str,
    route: str,
    route_status: str,
    stale_count: int,
    cooldown_count: int,
    inflight_pending_count: int,
    base_dir: str | Path | None = None,
) -> LiveQueueAdmissionDecision:
    is_latest_record = True
    already_processed = False

    if route == 'live':
        live_dedupe_key = _build_live_dedupe_key(symbol, route, route_status)
        queue_records = _load_queue_records(base_dir=base_dir)
        existing_live = [
            record for record in queue_records
            if record.get('route') == 'live'
            and _build_live_dedupe_key(record.get('symbol'), record.get('route'), record.get('route_status')) == live_dedupe_key
        ]
        if existing_live:
            is_latest_record = False

        queue_state = load_execution_queue_state(base_dir=base_dir)
        processed_keys = set(queue_state.get('processed_keys', []))
        for record in existing_live:
            record_key = _build_record_key(record)
            if record_key in processed_keys:
                already_processed = True
                break

    return derive_live_queue_admission_policy(
        symbol=symbol,
        route=route,
        stale_count=stale_count,
        cooldown_count=cooldown_count,
        inflight_pending_count=inflight_pending_count,
        already_processed=already_processed,
        is_latest_record=is_latest_record,
        base_dir=base_dir,
    )
