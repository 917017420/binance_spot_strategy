from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, load_settings
from .entry_executor import execute_entry_candidate
from .execution_candidate_queue import _execution_candidate_queue_path
from .execution_queue_log import log_queue_transition
from .execution_queue_state import load_execution_queue_state, save_execution_queue_state
from .live_queue_admission_policy import derive_live_queue_admission_policy
from .live_inflight_state import ACTIVE_LIVE_INFLIGHT_STATUSES, build_live_logical_key, detect_live_release_cooldown, load_live_inflight_state, save_live_inflight_state
from .live_submit_executor import build_live_submit_plan
from .models import IndicatorSnapshot, PairAnalysis, RiskPlan, ScoreBreakdown
from .utils import utc_now_iso


@dataclass
class ExecutionQueueProcessResult:
    processed: int
    skipped: int
    failed: int
    retried: int
    dead_lettered: int
    skipped_inflight: int = 0
    skipped_duplicate: int = 0
    skipped_superseded: int = 0
    skipped_cooldown: int = 0
    skipped_system_lock: int = 0
    skipped_symbol_conflict: int = 0
    skipped_single_active_lock: int = 0
    submit_failed: int = 0
    retry_scheduled: int = 0
    messages: list[str] = field(default_factory=list)



def _load_queue_records(base_dir: str | Path | None = None) -> list[dict]:
    path = _execution_candidate_queue_path(base_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]



def _build_record_key(record: dict) -> str:
    return f"{record.get('symbol')}|{record.get('route')}|{record.get('route_status')}|{record.get('queued_at')}"



def _build_live_dedupe_key(record: dict) -> str:
    return f"{record.get('symbol')}|{record.get('route')}|{record.get('route_status')}"



def _latest_only_queue(records: list[dict], base_dir: str | Path | None = None) -> tuple[list[dict], int]:
    latest_live: dict[str, dict] = {}
    ordered_records: list[dict] = []
    skipped_superseded = 0

    for record in records:
        route = record.get('route')
        if route == 'live':
            latest_live[_build_live_dedupe_key(record)] = record
        else:
            ordered_records.append(record)

    live_keys_seen: set[str] = set()
    for record in reversed(records):
        route = record.get('route')
        if route != 'live':
            continue
        live_key = _build_live_dedupe_key(record)
        if live_key in live_keys_seen:
            skipped_superseded += 1
            key = _build_record_key(record)
            log_queue_transition(
                key,
                record.get('symbol'),
                route,
                'skipped_superseded',
                base_dir=base_dir,
                worker_action='latest_only_dedupe',
                superseded_by=latest_live[live_key].get('queued_at'),
            )
            continue
        live_keys_seen.add(live_key)
        ordered_records.append(latest_live[live_key])

    non_live = [record for record in ordered_records if record.get('route') != 'live']
    live = [record for record in ordered_records if record.get('route') == 'live']
    return non_live + list(reversed(live)), skipped_superseded



def _build_stub_candidate(symbol: str) -> PairAnalysis:
    indicator = IndicatorSnapshot(
        close=100.0, ema20=98.0, ema50=96.0, ema200=94.0,
        high20=103.0, low20=88.0, atr14=2.0, atr14_pct=2.0,
        rsi14=60.0, volume=1000.0, avg_volume20=800.0,
        change_24h_pct=4.0, change_7d_pct=9.0, quote_volume_24h=30000000.0,
        body_pct=50.0, upper_wick_pct=10.0, lower_wick_pct=8.0,
        distance_to_ema20_pct=2.0,
    )
    score = ScoreBreakdown(
        trend_score=20, liquidity_score=18, strength_score=14, breakout_score=11,
        overextension_penalty=0, regime_score=6, total_score=69,
        passed_candidate_gate=True, strong_candidate=False, reasons=['queue worker stub'],
    )
    risk = RiskPlan(invalidation_level=95.0)
    return PairAnalysis(
        symbol=symbol,
        signal='BUY_READY_BREAKOUT',
        decision_action='BUY_APPROVED',
        execution_stage='IMMEDIATE_ATTENTION',
        attention_level='HIGH',
        decision_priority=140,
        position_size_pct=5.0,
        day_context_label='NEUTRAL_DAY_STRUCTURE',
        regime='neutral',
        indicators_1h=indicator,
        indicators_4h=indicator,
        scores=score,
        reasons=['queue worker stub'],
        risk=risk,
    )



def _load_candidate_from_record(record: dict) -> PairAnalysis:
    candidate_snapshot = record.get('candidate_snapshot')
    if isinstance(candidate_snapshot, dict) and candidate_snapshot:
        return PairAnalysis.model_validate(candidate_snapshot)
    return _build_stub_candidate(str(record.get('symbol')))



def process_execution_queue(base_dir: str | Path | None = None, settings: Settings | None = None, config_path: str = 'config/strategy.example.yaml', env_file: str = '.env') -> ExecutionQueueProcessResult:
    settings = settings or load_settings(config_path=config_path, env_path=env_file)
    raw_records = _load_queue_records(base_dir=base_dir)
    records, skipped_superseded = _latest_only_queue(raw_records, base_dir=base_dir)
    state = load_execution_queue_state(base_dir=base_dir)
    inflight_state = load_live_inflight_state(base_dir=base_dir)
    inflight_orders = inflight_state.get('orders', {})
    cooldown_state = detect_live_release_cooldown(inflight_state, cooldown_seconds=900.0)
    cooldown_orders = cooldown_state.get('orders', {})
    stale_count = 0
    processed_keys = set(state.get('processed_keys', []))
    retry_counts = dict(state.get('retry_counts', {}))
    processed = 0
    skipped = skipped_superseded
    failed = 0
    retried = 0
    dead_lettered = 0
    skipped_inflight = 0
    skipped_duplicate = 0
    skipped_cooldown = 0
    skipped_system_lock = 0
    skipped_symbol_conflict = 0
    skipped_single_active_lock = 0
    submit_failed = 0
    retry_scheduled = 0
    messages: list[str] = field(default_factory=list).default_factory()
    preview_total_equity_quote = float(settings.auto_entry.scan_reference_equity_quote)

    for record in records:
        key = _build_record_key(record)
        route = record.get('route')
        symbol = record.get('symbol')
        route_status = record.get('route_status')
        force_fail = bool(record.get('force_fail', False))
        log_queue_transition(key, symbol, route, 'queued_seen', base_dir=base_dir, route_status=route_status)

        logical_key = build_live_logical_key(symbol, route, route_status) if route == 'live' else None
        inflight_entry = inflight_orders.get(logical_key) if logical_key else None
        inflight_status = inflight_entry.get('status') if inflight_entry else None
        admission = derive_live_queue_admission_policy(
            symbol=symbol,
            route=route,
            stale_count=stale_count,
            cooldown_count=1 if route == 'live' and logical_key in cooldown_orders else 0,
            inflight_pending_count=1 if route == 'live' and str(inflight_status or '').lower() in ACTIVE_LIVE_INFLIGHT_STATUSES else 0,
            already_processed=(key in processed_keys),
            is_latest_record=True,
            base_dir=base_dir,
        )
        if route == 'live' and not admission.allow_process and admission.blocked_reason == 'post_escalation_cooldown_active':
            skipped += 1
            skipped_cooldown += 1
            messages.append(f"QUEUE_SKIP_COOLDOWN {logical_key}")
            log_queue_transition(
                key,
                symbol,
                route,
                'skipped_cooldown',
                base_dir=base_dir,
                worker_action='cooldown_guard',
                logical_key=logical_key,
                cooldown_entry=cooldown_orders.get(logical_key),
            )
            continue

        if route == 'live' and not admission.allow_process and admission.blocked_reason == 'live_submit_inflight_pending':
            skipped += 1
            skipped_inflight += 1
            messages.append(f"QUEUE_SKIP_INFLIGHT {logical_key} status={inflight_status}")
            log_queue_transition(
                key,
                symbol,
                route,
                'skipped_inflight',
                base_dir=base_dir,
                worker_action='inflight_guard',
                inflight_status=inflight_status,
                logical_key=logical_key,
            )
            continue

        if route == 'live' and not admission.allow_process and admission.blocked_reason in {'runner_fuse_open', 'recovery_warm_restart_active'}:
            skipped += 1
            skipped_system_lock += 1
            messages.append(f"QUEUE_SKIP_SYSTEM_LOCK {symbol} reason={admission.blocked_reason}")
            log_queue_transition(
                key,
                symbol,
                route,
                'skipped_system_lock',
                base_dir=base_dir,
                worker_action='single_active_system_lock',
                blocked_reason=admission.blocked_reason,
                active_trade_lock=admission.active_trade_lock.source_details if admission.active_trade_lock else None,
            )
            continue

        if route == 'live' and not admission.allow_process and admission.blocked_reason in {'multiple_active_positions_detected', 'multiple_live_inflight_detected', 'live_domain_symbol_conflict'}:
            skipped += 1
            skipped_symbol_conflict += 1
            messages.append(f"QUEUE_SKIP_SYMBOL_CONFLICT {symbol} reason={admission.blocked_reason}")
            log_queue_transition(
                key,
                symbol,
                route,
                'skipped_symbol_conflict',
                base_dir=base_dir,
                worker_action='single_active_symbol_conflict',
                blocked_reason=admission.blocked_reason,
                active_trade_lock=admission.active_trade_lock.source_details if admission.active_trade_lock else None,
                anomalies=admission.blocked_reasons,
            )
            continue

        if route == 'live' and not admission.allow_process and admission.blocked_reason in {'single_active_trade_locked_by_other_symbol', 'active_open_position_exists'}:
            skipped += 1
            skipped_single_active_lock += 1
            messages.append(f"QUEUE_SKIP_SINGLE_ACTIVE {symbol} reason={admission.blocked_reason}")
            log_queue_transition(
                key,
                symbol,
                route,
                'skipped_single_active_lock',
                base_dir=base_dir,
                worker_action='single_active_lock_guard',
                blocked_reason=admission.blocked_reason,
                active_trade_lock=admission.active_trade_lock.source_details if admission.active_trade_lock else None,
                active_symbol=admission.active_trade_lock.active_symbol if admission.active_trade_lock else None,
            )
            continue

        if key in processed_keys:
            skipped += 1
            skipped_duplicate += 1
            messages.append(f"QUEUE_SKIP_DUPLICATE {key}")
            log_queue_transition(key, symbol, route, 'skipped_duplicate', base_dir=base_dir, route_status=route_status)
            continue

        log_queue_transition(key, symbol, route, 'processing', base_dir=base_dir, route_status=route_status)

        try:
            if force_fail:
                raise RuntimeError('forced queue worker failure for retry path')

            if route == 'paper':
                candidate = _load_candidate_from_record(record)
                paper_mode = str(record.get('action_mode') or 'dry_run')
                if paper_mode not in {'dry_run', 'paper'}:
                    paper_mode = 'dry_run'
                result = execute_entry_candidate(
                    candidate,
                    mode=paper_mode,
                    total_equity_quote=preview_total_equity_quote,
                )
                entry_action_path = result.get('entry_action_path')
                execution_path = result.get('execution_path')
                position_path = result.get('position_path')
                position_event_path = result.get('position_event_path')
                messages.append(
                    f"QUEUE_PROCESS {symbol} route=paper status={route_status} action=auto_paper_executed mode={paper_mode} entry_action_path={entry_action_path}"
                )
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    'executed',
                    base_dir=base_dir,
                    worker_action='auto_paper_executed',
                    entry_action_path=str(entry_action_path),
                    execution_path=str(execution_path),
                    position_path=str(position_path),
                    position_event_path=str(position_event_path),
                )
            elif route == 'live':
                candidate = _load_candidate_from_record(record)
                debug_contract = None
                if isinstance(record.get('debug'), dict):
                    live_submit_debug = record.get('debug', {}).get('live_submit')
                    if isinstance(live_submit_debug, dict):
                        debug_contract = live_submit_debug
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    'submit_requested',
                    base_dir=base_dir,
                    worker_action='submit_requested',
                )
                plan = build_live_submit_plan(
                    candidate,
                    total_equity_quote=preview_total_equity_quote,
                    settings=settings,
                    debug_contract=debug_contract,
                )
                adapter_details = plan.details.get('adapter_details') or {}
                submit_response = plan.details.get('exchange_submit_response') or {}
                submit_error = plan.details.get('exchange_submit_error')
                contract = plan.details.get('exchange_submit_contract') or {}
                debug_contract = plan.details.get('exchange_submit_debug_contract')
                messages.append(
                    f"QUEUE_PROCESS {symbol} route=live status={route_status} action=submit_requested plan_path={plan.details.get('plan_path')}"
                )
                adapter_stage = contract.get('adapter_call_stage') or plan.details.get('exchange_adapter_status') or 'adapter_stubbed'
                inflight_orders[logical_key] = {
                    'status': submit_response.get('status') or adapter_stage,
                    'last_queue_key': key,
                    'client_order_id': plan.details.get('client_order_id'),
                    'updated_at': utc_now_iso(),
                }
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    adapter_stage,
                    base_dir=base_dir,
                    worker_action=adapter_stage,
                    plan_path=plan.details.get('plan_path'),
                    adapter_details=adapter_details,
                    submit_contract=contract,
                    debug_contract=debug_contract,
                )
                terminal_status = contract.get('terminal_submit_status')
                if terminal_status == 'submit_failed' or submit_response.get('status') == 'submit_failed':
                    failed += 1
                    submit_failed += 1
                    retry_count = int(retry_counts.get(key, 0)) + 1
                    retry_counts[key] = retry_count
                    inflight_orders[logical_key]['status'] = 'submit_failed'
                    messages.append(f"QUEUE_SUBMIT_FAILED {symbol} route={route} retry_count={retry_count}")
                    log_queue_transition(
                        key,
                        symbol,
                        route,
                        'submit_failed',
                        base_dir=base_dir,
                        worker_action='submit_failed',
                        submit_error=submit_error,
                        submit_contract=contract,
                        debug_contract=debug_contract,
                        retry_count=retry_count,
                    )
                    if retry_count <= 2:
                        retried += 1
                        retry_scheduled += 1
                        inflight_orders.pop(logical_key, None)
                        log_queue_transition(
                            key,
                            symbol,
                            route,
                            'retry_scheduled',
                            base_dir=base_dir,
                            retry_count=retry_count,
                        )
                        continue
                    dead_lettered += 1
                    processed_keys.add(key)
                    inflight_orders.pop(logical_key, None)
                    log_queue_transition(
                        key,
                        symbol,
                        route,
                        'dead_lettered',
                        base_dir=base_dir,
                        retry_count=retry_count,
                    )
                    continue
                pending_stage = submit_response.get('status') or 'pending_real_submit'
                inflight_orders[logical_key]['status'] = pending_stage
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    pending_stage,
                    base_dir=base_dir,
                    worker_action=pending_stage,
                    submit_contract=contract,
                    debug_contract=debug_contract,
                )
            else:
                messages.append(f"QUEUE_PROCESS {symbol} route={route} status={route_status} action=noop")
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    'noop',
                    base_dir=base_dir,
                )

            processed_keys.add(key)
            retry_counts.pop(key, None)
            processed += 1
        except Exception as error:
            failed += 1
            retry_count = int(retry_counts.get(key, 0) or 0) + 1
            retry_counts[key] = retry_count
            if logical_key:
                inflight_orders.pop(logical_key, None)
            log_queue_transition(
                key,
                symbol,
                route,
                'failed',
                base_dir=base_dir,
                error_type=type(error).__name__,
                error_message=str(error),
                retry_count=retry_count,
            )
            if retry_count <= 2:
                retried += 1
                retry_scheduled += 1
                messages.append(f"QUEUE_RETRY {symbol} route={route} retry_count={retry_count}")
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    'retry_scheduled',
                    base_dir=base_dir,
                    retry_count=retry_count,
                )
            else:
                dead_lettered += 1
                messages.append(f"QUEUE_FAILED {symbol} route={route} retry_count={retry_count}")
                processed_keys.add(key)
                log_queue_transition(
                    key,
                    symbol,
                    route,
                    'dead_lettered',
                    base_dir=base_dir,
                    retry_count=retry_count,
                )

    next_state = {
        'processed_keys': list(processed_keys)[-500:],
        'retry_counts': retry_counts,
        'updated_at': utc_now_iso(),
    }
    save_execution_queue_state(next_state, base_dir=base_dir)
    save_live_inflight_state({'orders': inflight_orders}, base_dir=base_dir)
    return ExecutionQueueProcessResult(
        processed=processed,
        skipped=skipped,
        failed=failed,
        retried=retried,
        dead_lettered=dead_lettered,
        skipped_inflight=skipped_inflight,
        skipped_duplicate=skipped_duplicate,
        skipped_superseded=skipped_superseded,
        skipped_cooldown=skipped_cooldown,
        skipped_system_lock=skipped_system_lock,
        skipped_symbol_conflict=skipped_symbol_conflict,
        skipped_single_active_lock=skipped_single_active_lock,
        submit_failed=submit_failed,
        retry_scheduled=retry_scheduled,
        messages=messages,
    )
