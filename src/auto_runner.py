from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta

from .auto_entry_gate import evaluate_auto_entry
from .auto_runner_execution_policy import build_execution_ready_decision
from .auto_runner_preview_samples import build_preview_sample_candidates
from .config import load_settings
from .control_plane_reconcile import reconcile_control_plane_state
from .exchange import create_exchange, fetch_ohlcv_dataframe
from .execution_candidate_queue import append_execution_candidate
from .execution_queue_archive import archive_execution_artifacts
from .execution_queue_maintenance import compact_execution_artifacts
from .execution_queue_recovery_cleanup import cleanup_execution_queue_after_recovery
from .execution_queue_worker import ExecutionQueueProcessResult, process_execution_queue
from .live_queue_admission_policy import derive_enqueue_admission_precheck, derive_live_queue_admission_policy
from .live_inflight_state import detect_live_release_cooldown, detect_stale_live_inflight, escalate_stale_live_inflight, load_live_inflight_state, release_escalated_live_inflight, save_live_inflight_state
from .live_submit_state import load_live_submit_state
from .order_refresh_reconcile import run_order_refresh_reconcile
from .single_active_trade_state import build_single_active_trade_state
from .market_regime import evaluate_market_regime
from .models import PairAnalysis
from .position_monitor import run_position_monitor_auto
from .ranker import split_priority_and_secondary
from .route_execution_bridge import build_route_lifecycle_view
from .route_executor import execute_route_candidate
from .runner_health import build_runner_health_summary
from .runner_recovery import reset_runner_fuse
from .runner_recovery_policy import build_recovery_policy
from .runner_state import describe_runner_state_file, load_runner_state, load_runner_stop_signal, mark_runner_cycle_started, save_runner_state
from .scan_flow import apply_ranked_candidate_handoff, build_auto_entry_config, scan_symbol_analysis
from .universe import build_symbol_universe
from .utils import parse_utc_iso, utc_now_iso


@dataclass
class AutoRunnerCycleResult:
    ok: bool
    cycle_started_at: str
    cycle_finished_at: str
    steps: list[str] = field(default_factory=list)
    state_path: str | None = None
    error: str | None = None


@dataclass
class AutoRunnerLoopResult:
    ok: bool
    loop_started_at: str
    loop_finished_at: str
    cycles: list[AutoRunnerCycleResult] = field(default_factory=list)
    stop_requested: bool = False
    stop_reason: str | None = None


def _update_runner_loop_state(**updates) -> dict:
    state = load_runner_state()
    state.update(updates)
    save_runner_state(state)
    return state


def _resolve_sleep_until_at(started_at: str, sleep_seconds: float) -> str | None:
    started = parse_utc_iso(started_at)
    if started is None:
        return None
    return (started + timedelta(seconds=max(float(sleep_seconds or 0.0), 0.0))).replace(microsecond=0).isoformat()


def _sleep_with_heartbeat(total_sleep_seconds: float, heartbeat_interval_seconds: float = 5.0) -> dict | None:
    remaining = max(float(total_sleep_seconds or 0.0), 0.0)
    if remaining <= 0:
        return None

    heartbeat_interval = max(float(heartbeat_interval_seconds or 5.0), 0.25)
    started_at = utc_now_iso()
    _update_runner_loop_state(
        last_loop_status='sleeping',
        last_loop_sleep_started_at=started_at,
        last_loop_sleep_until_at=_resolve_sleep_until_at(started_at, remaining),
        last_loop_sleep_seconds=remaining,
        last_loop_sleep_remaining_seconds=remaining,
        last_heartbeat_at=started_at,
        last_heartbeat_status='sleeping',
    )

    stop_signal = load_runner_stop_signal()
    if stop_signal is not None:
        detected_at = utc_now_iso()
        _update_runner_loop_state(
            last_loop_status='stop_requested',
            last_loop_exit_reason='stop_requested',
            last_stop_signal_at=stop_signal.get('requested_at') or detected_at,
            last_stop_signal_reason=stop_signal.get('reason') or 'manual_stop',
            last_loop_sleep_remaining_seconds=remaining,
            last_heartbeat_at=detected_at,
            last_heartbeat_status='stop_requested',
        )
        return stop_signal

    while remaining > 0:
        chunk = min(heartbeat_interval, remaining)
        time.sleep(chunk)
        remaining = max(remaining - chunk, 0.0)
        heartbeat_at = utc_now_iso()
        stop_signal = load_runner_stop_signal()
        if stop_signal is not None:
            _update_runner_loop_state(
                last_loop_status='stop_requested',
                last_loop_exit_reason='stop_requested',
                last_stop_signal_at=stop_signal.get('requested_at') or heartbeat_at,
                last_stop_signal_reason=stop_signal.get('reason') or 'manual_stop',
                last_loop_sleep_remaining_seconds=remaining,
                last_heartbeat_at=heartbeat_at,
                last_heartbeat_status='stop_requested',
            )
            return stop_signal
        if remaining > 0:
            _update_runner_loop_state(
                last_loop_status='sleeping',
                last_loop_sleep_remaining_seconds=remaining,
                last_heartbeat_at=heartbeat_at,
                last_heartbeat_status='sleeping',
            )

    return None



def _scan_symbol(exchange, symbol: str, quote_volume_24h: float, settings, regime: str) -> PairAnalysis:
    return scan_symbol_analysis(exchange, symbol, quote_volume_24h, settings, regime)



def _build_auto_entry_cfg(settings):
    return build_auto_entry_config(settings)



def _build_preview_for_candidates(settings, regime: str, candidates: list[PairAnalysis], cycle_policy: str, action_mode: str = 'dry_run', debug_live_submit_failure_contract: dict | None = None, live_gate=None, stale_count: int = 0, cooldown_count: int = 0, inflight_pending_count: int = 0) -> tuple[list[str], list[dict], list[dict], list[dict], list[dict]]:
    shortlist_symbols = [candidate.symbol for candidate in candidates]
    route_summaries: list[dict] = []
    execution_previews: list[dict] = []
    readiness_summaries: list[dict] = []
    queued_candidates: list[dict] = []
    auto_entry_cfg = _build_auto_entry_cfg(settings)
    preview_total_equity_quote = float(settings.auto_entry.scan_reference_equity_quote)
    configured_live_order_quote_amount = float(settings.auto_entry.live_order_quote_amount)
    for candidate in candidates:
        decision = evaluate_auto_entry(candidate, market_state=regime, config=auto_entry_cfg)
        route_preview = {
            'symbol': candidate.symbol,
            'allow': decision.allow,
            'route': decision.route,
            'severity': decision.severity,
            'score': decision.score,
            'reasons': list(decision.reasons),
        }
        route_summaries.append(route_preview)
        route_exec = execute_route_candidate(
            candidate,
            route=decision.route,
            mode=None,
            total_equity_quote=preview_total_equity_quote,
            settings=settings,
        )
        lifecycle = build_route_lifecycle_view(candidate, route_exec)
        execution_preview = {
            'symbol': candidate.symbol,
            'route': route_exec.route,
            'mode': route_exec.mode,
            'execution_status': route_exec.status,
            'position_init_status': lifecycle.position_init_status,
            'preview_total_equity_quote': preview_total_equity_quote,
            'configured_live_order_quote_amount': configured_live_order_quote_amount if decision.route == 'live' else None,
        }
        execution_previews.append(execution_preview)
        ready = build_execution_ready_decision(
            symbol=candidate.symbol,
            route=decision.route,
            route_status=route_exec.status,
            cycle_policy=cycle_policy,
            position_init_status=lifecycle.position_init_status,
        )
        enqueue_admission = None
        admission_precheck = None
        can_progress_to_live_execution = ready.execution_ready
        primary_blocked_reason = None
        blocked_reasons: list[str] = []
        if decision.route == 'live':
            enqueue_admission = derive_enqueue_admission_precheck(
                symbol=candidate.symbol,
                route=decision.route,
                route_status=route_exec.status,
                stale_count=stale_count,
                cooldown_count=cooldown_count,
                inflight_pending_count=inflight_pending_count,
            )
            admission_precheck = enqueue_admission.to_contract()
            can_progress_to_live_execution = ready.execution_ready and enqueue_admission.allow_enqueue
            if not enqueue_admission.allow_enqueue:
                primary_blocked_reason = enqueue_admission.blocked_reason
                blocked_reasons = list(enqueue_admission.blocked_reasons)

        readiness_preview = {
            'symbol': ready.symbol,
            'route': decision.route,
            'route_status': route_exec.status,
            'execution_ready': ready.execution_ready,
            'can_progress_to_live_execution': can_progress_to_live_execution,
            'blocked_by_policy': ready.blocked_by_policy,
            'reason': ready.reason,
            'notes': list(ready.notes),
            'primary_blocked_reason': primary_blocked_reason,
            'blocked_reasons': blocked_reasons,
        }
        if admission_precheck is not None:
            readiness_preview['admission_precheck'] = admission_precheck
        readiness_summaries.append(readiness_preview)
        if ready.execution_ready:
            if action_mode == 'live' and decision.route != 'live':
                continue
            if action_mode in {'dry_run', 'paper'} and decision.route == 'live':
                continue

            queue_record = {
                'symbol': candidate.symbol,
                'route': decision.route,
                'route_status': route_exec.status,
                'cycle_policy': cycle_policy,
                'action_mode': action_mode,
                'position_init_status': lifecycle.position_init_status,
                'decision_reason': ready.reason,
                'route_decision': route_preview,
                'execution_preview': execution_preview,
                'candidate_snapshot': candidate.model_dump(mode='json'),
                'preview_total_equity_quote': preview_total_equity_quote,
                'configured_live_order_quote_amount': configured_live_order_quote_amount if decision.route == 'live' else None,
            }
            if candidate.symbol == 'TRX/USDT' and isinstance(debug_live_submit_failure_contract, dict) and debug_live_submit_failure_contract:
                queue_record['debug'] = {
                    'live_submit': debug_live_submit_failure_contract,
                }
            if queue_record['route'] == 'live' and admission_precheck is not None:
                queue_record['admission_precheck'] = admission_precheck
                if not enqueue_admission.allow_enqueue:
                    continue
            queued_candidates.append(queue_record)
    return shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates



def _run_scan_step(config_path: str, env_file: str, shortlist_top: int = 3, max_scan_symbols: int | None = None, regime_override: str | None = None, action_mode: str = 'dry_run', use_sample_candidates: bool = False, debug_live_submit_failure_contract: dict | None = None, live_gate=None, stale_count: int = 0, cooldown_count: int = 0, inflight_pending_count: int = 0) -> tuple[str, str, int, str, list[str], list[dict], list[dict], list[dict], list[dict]]:
    settings = load_settings(config_path, env_file)
    regime = regime_override or 'neutral'
    cycle_policy = 'monitor_only' if regime == 'risk_off' else 'scan_plus_monitor'
    scan_limit = max_scan_symbols if max_scan_symbols is not None else int(settings.universe.max_symbols)

    if use_sample_candidates:
        candidates = build_preview_sample_candidates(regime=regime, debug_contract=debug_live_submit_failure_contract)
        shortlist = candidates[:shortlist_top] if regime != 'risk_off' else []
        shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates = _build_preview_for_candidates(settings, regime, shortlist, cycle_policy, action_mode=action_mode, debug_live_submit_failure_contract=debug_live_submit_failure_contract, live_gate=live_gate, stale_count=stale_count, cooldown_count=cooldown_count, inflight_pending_count=inflight_pending_count)
        return settings.exchange.name, settings.output.directory, len(candidates), regime, shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates

    exchange = create_exchange(settings)
    try:
        symbols, skipped, quote_volume_by_symbol = build_symbol_universe(exchange, settings, top=None)
        btc_quote_volume = quote_volume_by_symbol.get('BTC/USDT', 0.0)
        btc_1h = fetch_ohlcv_dataframe(exchange, 'BTC/USDT', timeframe=settings.data.primary_timeframe, limit=settings.data.ohlcv_limit)
        btc_4h = fetch_ohlcv_dataframe(exchange, 'BTC/USDT', timeframe=settings.data.context_timeframe, limit=settings.data.ohlcv_limit)
        regime_report = evaluate_market_regime('BTC/USDT', btc_1h, btc_4h, btc_quote_volume)
        settings.runtime_btc_indicators_1h = regime_report.indicators_1h
        regime = regime_override or regime_report.regime
        cycle_policy = 'monitor_only' if regime == 'risk_off' else 'scan_plus_monitor'

        analyses: list[PairAnalysis] = []
        for symbol in symbols[:scan_limit]:
            try:
                analyses.append(_scan_symbol(exchange, symbol, quote_volume_by_symbol.get(symbol, 0.0), settings, regime))
            except Exception:
                continue
        priority_candidates, secondary_candidates = split_priority_and_secondary(analyses, shortlist_top)
        apply_ranked_candidate_handoff(priority_candidates, secondary_candidates)
        shortlist = priority_candidates[:shortlist_top] if regime != 'risk_off' else []
        shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates = _build_preview_for_candidates(settings, regime, shortlist, cycle_policy, action_mode=action_mode, debug_live_submit_failure_contract=debug_live_submit_failure_contract, live_gate=live_gate, stale_count=stale_count, cooldown_count=cooldown_count, inflight_pending_count=inflight_pending_count)
        return settings.exchange.name, settings.output.directory, len(symbols), regime, shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()



def run_auto_cycle(config_path: str, env_file: str, action_mode: str = 'dry_run', regime_override: str | None = None, use_sample_candidates: bool = False, debug_live_submit_failure_contract: dict | None = None) -> AutoRunnerCycleResult:
    started_at = utc_now_iso()
    settings = load_settings(config_path, env_file)
    state = load_runner_state()
    steps: list[str] = field(default_factory=list).default_factory()
    state, stale_cycle_recovered = mark_runner_cycle_started(state, started_at=started_at)
    save_runner_state(state)
    if stale_cycle_recovered:
        steps.append('STALE_LOOP_RECOVERY previous_running_cycle_detected')

    try:
        startup_reconcile = reconcile_control_plane_state()
        if startup_reconcile.actions:
            steps.append(
                'STARTUP_RECOVERY '
                f'before={startup_reconcile.before_status} after={startup_reconcile.after_status} '
                f'actions={len(startup_reconcile.actions)}'
            )
            steps.extend([f'STARTUP_RECOVERY_ACTION {item}' for item in startup_reconcile.actions])
            state = load_runner_state()

        recovery_policy = build_recovery_policy(state)
        steps.append(f'RECOVERY_POLICY warm_restart_active={recovery_policy.warm_restart_active} allow_queue_enqueue={recovery_policy.allow_queue_enqueue} allow_queue_worker={recovery_policy.allow_queue_worker} clear_processed_keys_on_recovery={recovery_policy.clear_processed_keys_on_recovery} reason={recovery_policy.reason}')
        steps.extend([f'RECOVERY_NOTE {note}' for note in recovery_policy.notes])

        if recovery_policy.clear_processed_keys_on_recovery:
            cleanup = cleanup_execution_queue_after_recovery(clear_retry_counts=False, clear_processed_keys=True)
            steps.extend(cleanup.messages)
            state = load_runner_state()
            last_recovery_cleanup = state.get('last_recovery_cleanup') or {}
            state['last_recovery_cleanup'] = {
                **last_recovery_cleanup,
                'trimmed_processed_keys': cleanup.trimmed_processed_keys,
            }
            save_runner_state(state)
            state = load_runner_state()
            recovery_policy = build_recovery_policy(state)

        if state.get('fuse_open') and state.get('last_recovery_reason'):
            recovery = reset_runner_fuse(reason='auto_cycle_resume_after_manual_reset')
            steps.extend(recovery.messages)
            state = load_runner_state()
            recovery_policy = build_recovery_policy(state)

        inflight_state = load_live_inflight_state()
        stale_inflight = detect_stale_live_inflight(inflight_state, stale_after_seconds=900.0)
        escalated_inflight = escalate_stale_live_inflight(inflight_state, stale_after_seconds=900.0, escalate_after_seconds=1800.0)
        stale_inflight_count = stale_inflight.get('count', 0)
        if stale_inflight_count > 0:
            steps.append(f'STALE_LIVE_INFLIGHT count={stale_inflight_count} orders={stale_inflight.get("orders")}')
        if escalated_inflight.get('count', 0) > 0:
            next_inflight_state, released = release_escalated_live_inflight(inflight_state, escalated_inflight)
            save_live_inflight_state(next_inflight_state)
            inflight_state = next_inflight_state
            steps.append(f'STALE_LIVE_INFLIGHT_ESCALATED count={escalated_inflight.get("count")} released={released}')
            stale_inflight_count = 0
        live_release_cooldown = detect_live_release_cooldown(inflight_state, cooldown_seconds=900.0)
        if live_release_cooldown.get('count', 0) > 0:
            steps.append(f'LIVE_RELEASE_COOLDOWN count={live_release_cooldown.get("count")} orders={live_release_cooldown.get("orders")}')

        admission = derive_live_queue_admission_policy(
            stale_count=stale_inflight_count,
            cooldown_count=live_release_cooldown.get('count', 0),
            inflight_pending_count=0,
            already_processed=False,
            is_latest_record=True,
        )
        live_gate = admission.live_gate
        steps.append(
            f'LIVE_EXECUTION_GATE can_enqueue_live={live_gate.can_enqueue_live} can_process_live={live_gate.can_process_live} '
            f'blocked_reason={live_gate.blocked_reason or "none"} blocked_reasons={live_gate.blocked_reasons}'
        )
        steps.append(
            f'LIVE_QUEUE_ADMISSION allow_enqueue={admission.allow_enqueue} allow_process={admission.allow_process} '
            f'blocked_reason={admission.blocked_reason or "none"} blocked_reasons={admission.blocked_reasons}'
        )

        exchange_name, output_dir, eligible_count, market_regime, shortlist_symbols, route_summaries, execution_previews, readiness_summaries, queued_candidates = _run_scan_step(
            config_path=config_path,
            env_file=env_file,
            regime_override=regime_override,
            action_mode=action_mode,
            use_sample_candidates=use_sample_candidates,
            debug_live_submit_failure_contract=debug_live_submit_failure_contract,
            live_gate=live_gate,
            stale_count=stale_inflight_count,
            cooldown_count=live_release_cooldown.get('count', 0),
            inflight_pending_count=0,
        )
        scan_at = utc_now_iso()
        cycle_policy = 'monitor_only' if market_regime == 'risk_off' else 'scan_plus_monitor'
        steps.append(
            f'SCAN_STEP exchange={exchange_name} output_dir={output_dir} eligible_symbols={eligible_count} market_regime={market_regime} shortlist={shortlist_symbols} sample_mode={use_sample_candidates}'
        )
        if route_summaries:
            steps.append(f'ROUTE_PREVIEW {route_summaries}')
        if execution_previews:
            steps.append(f'ROUTE_EXECUTION_PREVIEW {execution_previews}')
        if readiness_summaries:
            steps.append(f'EXECUTION_READY {readiness_summaries}')

        queue_paths: list[str] = []
        if recovery_policy.allow_queue_enqueue:
            for record in queued_candidates:
                path = append_execution_candidate(record)
                queue_paths.append(str(path))
            if queued_candidates:
                steps.append(f'EXECUTION_QUEUE queued={queued_candidates} paths={queue_paths}')
        else:
            steps.append('EXECUTION_QUEUE enqueue_skipped_due_to_warm_restart')

        if recovery_policy.allow_queue_worker:
            queue_result = process_execution_queue(settings=settings, config_path=config_path, env_file=env_file)
            steps.append(
                f'QUEUE_WORKER processed={queue_result.processed} skipped={queue_result.skipped} failed={queue_result.failed} retried={queue_result.retried} dead_lettered={queue_result.dead_lettered}'
            )
            steps.append(
                'QUEUE_WORKER_SUMMARY '
                f"skipped_inflight={queue_result.skipped_inflight} skipped_duplicate={queue_result.skipped_duplicate} "
                f"skipped_superseded={queue_result.skipped_superseded} skipped_cooldown={queue_result.skipped_cooldown} "
                f"submit_failed={queue_result.submit_failed} retry_scheduled={queue_result.retry_scheduled}"
            )
            steps.extend(queue_result.messages)

            submit_state = load_live_submit_state()
            inflight_state = load_live_inflight_state()
            last_client_order_id = submit_state.get('last_client_order_id')
            last_submit_status = str(submit_state.get('last_submit_status') or '').lower()
            inflight_orders = inflight_state.get('orders') or {}
            attempt_ts = None
            next_after_ts = None
            refresh_needed = (bool(last_client_order_id) and last_submit_status not in {'filled', 'closed', 'canceled', 'cancelled', 'rejected'}) or bool(inflight_orders)
            if refresh_needed:
                attempt_ts = utc_now_iso()
                try:
                    refresh_result = run_order_refresh_reconcile()
                    steps.extend(refresh_result.actions)
                    steps.append(
                        f"ORDER_REFRESH ok={refresh_result.ok} order_found={refresh_result.order_found} order_status={refresh_result.order_status or 'none'} stage={refresh_result.stage or 'none'}"
                    )
                except Exception as refresh_error:
                    refresh_result = None
                    steps.append(f"ORDER_REFRESH_ERROR {type(refresh_error).__name__}: {refresh_error}")
            else:
                refresh_result = None
                steps.append('ORDER_REFRESH skipped_due_to_terminal_submit_and_empty_inflight')
        else:
            refresh_result = None
            queue_result = ExecutionQueueProcessResult(processed=0, skipped=0, failed=0, retried=0, dead_lettered=0, messages=[])
            steps.append('QUEUE_WORKER skipped_due_to_recovery_policy')

        if market_regime == 'risk_off':
            steps.append('CYCLE_POLICY risk_off => monitor_only')
        else:
            steps.append('CYCLE_POLICY allow_scan_plus_monitor')

        monitor_result = run_position_monitor_auto(config_path=config_path, env_file=env_file, action_mode=action_mode)
        monitor_at = utc_now_iso()
        archived_simulated_positions = sum(
            1 for action in (monitor_result.reconcile_actions or []) if str(action).startswith('SIMULATED_POSITION_ARCHIVED ')
        )
        steps.append(
            f'POSITION_MONITOR scanned={monitor_result.scanned} updated={monitor_result.updated} failed={monitor_result.failed} archived_simulated={archived_simulated_positions}'
        )
        steps.extend(monitor_result.messages)

        execution_pressure = queue_result.failed + queue_result.dead_lettered
        next_sleep_seconds = 30.0 if market_regime == 'risk_off' else 10.0
        if recovery_policy.next_sleep_seconds is not None:
            next_sleep_seconds = max(next_sleep_seconds, recovery_policy.next_sleep_seconds)
        if execution_pressure > 0:
            next_sleep_seconds = max(next_sleep_seconds, 60.0)
        if stale_inflight_count > 0:
            next_sleep_seconds = max(next_sleep_seconds, 120.0)
        if live_release_cooldown.get('count', 0) > 0:
            next_sleep_seconds = max(next_sleep_seconds, 90.0)

        queue_fuse_open = queue_result.dead_lettered > 0
        consecutive_failures = 0 if not queue_fuse_open else int(state.get('consecutive_failures', 0) or 0) + 1
        warm_restart_cycles_remaining = int(state.get('warm_restart_cycles_remaining', 0) or 0)
        warm_restart_active = warm_restart_cycles_remaining > 0 and bool(state.get('last_recovery_reason'))
        if warm_restart_cycles_remaining > 0:
            warm_restart_cycles_remaining -= 1

        health = build_runner_health_summary(
            fuse_open=queue_fuse_open,
            warm_restart_active=warm_restart_active,
            queue_dead_lettered=queue_result.dead_lettered,
            queue_failed=queue_result.failed,
            stale_inflight_count=stale_inflight_count,
            live_release_cooldown_count=live_release_cooldown.get('count', 0),
            escalated_inflight_count=escalated_inflight.get('count', 0),
            monitor_failed_count=monitor_result.failed,
            stale_cycle_recovered=stale_cycle_recovered,
        )
        steps.append(f'RUNNER_HEALTH status={health.status} reason={health.reason}')
        steps.append(
            'LIVE_EXECUTION_SUMMARY '
            f"stale_count={stale_inflight_count} escalated_count={escalated_inflight.get('count', 0)} "
            f"cooldown_count={live_release_cooldown.get('count', 0)} "
            f"blocked_reason={'stale_live_inflight_detected' if stale_inflight_count > 0 else 'post_escalation_cooldown_active' if live_release_cooldown.get('count', 0) > 0 else 'none'}"
        )

        active_trade_state = build_single_active_trade_state()
        finished_at = utc_now_iso()
        next_state = {
            **state,
            'last_scan_at': scan_at,
            'last_monitor_at': monitor_at,
            'last_cycle_status': 'ok' if not queue_fuse_open else 'degraded',
            'last_cycle_error': None if not queue_fuse_open else 'queue dead-letter detected',
            'last_cycle_stage': 'idle',
            'last_cycle_finished_at': finished_at,
            'last_successful_cycle_at': (finished_at if not queue_fuse_open else state.get('last_successful_cycle_at')),
            'last_heartbeat_at': finished_at,
            'last_heartbeat_status': 'ok' if not queue_fuse_open else 'degraded',
            'last_market_regime': market_regime,
            'last_eligible_symbols': eligible_count,
            'last_shortlist_symbols': shortlist_symbols,
            'last_route_preview': route_summaries,
            'last_route_execution_preview': execution_previews,
            'last_execution_ready': readiness_summaries,
            'last_queued_candidates': queued_candidates,
            'last_queue_worker_messages': queue_result.messages,
            'last_cycle_policy': cycle_policy,
            'next_sleep_seconds': next_sleep_seconds,
            'consecutive_failures': consecutive_failures,
            'fuse_open': queue_fuse_open,
            'last_queue_failed': queue_result.failed,
            'last_queue_retried': queue_result.retried,
            'last_queue_dead_lettered': queue_result.dead_lettered,
            'last_queue_skipped_inflight': queue_result.skipped_inflight,
            'last_queue_skipped_duplicate': queue_result.skipped_duplicate,
            'last_queue_skipped_superseded': queue_result.skipped_superseded,
            'last_queue_skipped_cooldown': queue_result.skipped_cooldown,
            'last_queue_skipped_single_active_lock': queue_result.skipped_single_active_lock,
            'last_queue_skipped_system_lock': queue_result.skipped_system_lock,
            'last_queue_skipped_symbol_conflict': queue_result.skipped_symbol_conflict,
            'last_queue_submit_failed': queue_result.submit_failed,
            'last_queue_retry_scheduled': queue_result.retry_scheduled,
            'last_order_refresh_ok': (refresh_result.ok if refresh_result is not None else None),
            'last_order_refresh_found': (refresh_result.order_found if refresh_result is not None else None),
            'last_order_refresh_status': (refresh_result.order_status if refresh_result is not None else None),
            'last_order_refresh_stage': (refresh_result.stage if refresh_result is not None else None),
            'last_order_refresh_error': (refresh_result.error if refresh_result is not None else None),
            'last_order_refresh_ts': (refresh_result.refreshed_at if refresh_result is not None else None),
            'last_order_refresh_attempt_ts': attempt_ts,
            'next_order_refresh_after_ts': next_after_ts,
            'last_order_refresh_target_count': (refresh_result.target_count if refresh_result is not None else 0),
            'last_order_refresh_refreshed_count': (refresh_result.refreshed_count if refresh_result is not None else 0),
            'last_order_refresh_actions': (refresh_result.actions if refresh_result is not None else []),
            'last_active_trade_status': active_trade_state.status,
            'last_active_trade_symbol': active_trade_state.lock.active_symbol,
            'last_active_trade_stage': active_trade_state.lock.active_stage,
            'last_active_trade_lock_reason': active_trade_state.lock.lock_reason,
            'warm_restart_cycles_remaining': warm_restart_cycles_remaining,
            'last_health_status': health.status,
            'last_health_reason': health.reason,
            'last_stale_inflight_count': stale_inflight_count,
            'last_escalated_inflight_count': escalated_inflight.get('count', 0),
            'last_live_release_cooldown_count': live_release_cooldown.get('count', 0),
            'last_monitor_failed': monitor_result.failed,
            'last_monitor_messages': monitor_result.messages,
            'last_monitor_summary': {
                'scanned': monitor_result.scanned,
                'updated': monitor_result.updated,
                'failed': monitor_result.failed,
            },
            'last_reconcile_actions': monitor_result.reconcile_actions,
            'last_archived_simulated_positions': archived_simulated_positions,
            'last_cycle_summary': {
                'market_regime': market_regime,
                'shortlist_symbols': shortlist_symbols,
                'queued_candidates': len(queued_candidates),
                'queue_processed': queue_result.processed,
                'queue_dead_lettered': queue_result.dead_lettered,
                'monitor_scanned': monitor_result.scanned,
                'monitor_updated': monitor_result.updated,
                'monitor_failed': monitor_result.failed,
                'archived_simulated_positions': archived_simulated_positions,
                'health_status': health.status,
                'health_reason': health.reason,
            },
        }
        maintenance = compact_execution_artifacts(
            keep_queue_last=120,
            keep_log_last=400,
            keep_processed_keys_last=400,
            keep_retry_keys_last=120,
        )
        steps.extend(maintenance.messages)
        next_state['last_maintenance_messages'] = maintenance.messages

        state_path = save_runner_state(next_state)
        state_meta = describe_runner_state_file()
        steps.append(f'RUNNER_STATE_WRITTEN meta={state_meta}')
        if queue_fuse_open:
            steps.append('RUNNER_FUSE queue_dead_letter_detected')

        if queue_fuse_open or queue_result.skipped > 100:
            archive = archive_execution_artifacts()
            steps.extend(archive.messages)

        return AutoRunnerCycleResult(
            ok=not queue_fuse_open,
            cycle_started_at=started_at,
            cycle_finished_at=finished_at,
            steps=steps,
            state_path=str(state_path),
            error='queue dead-letter detected' if queue_fuse_open else None,
        )
    except Exception as error:
        consecutive_failures = int(state.get('consecutive_failures', 0) or 0) + 1
        fuse_open = consecutive_failures >= 3
        backoff_seconds = 60.0 if not fuse_open else 300.0
        finished_at = utc_now_iso()
        next_state = {
            **state,
            'last_cycle_status': 'failed',
            'last_cycle_error': f'{type(error).__name__}: {error}',
            'last_cycle_stage': 'idle',
            'last_cycle_finished_at': finished_at,
            'last_heartbeat_at': finished_at,
            'last_heartbeat_status': 'failed',
            'next_sleep_seconds': backoff_seconds,
            'consecutive_failures': consecutive_failures,
            'fuse_open': fuse_open,
            'last_health_status': 'fused' if fuse_open else 'degraded',
            'last_health_reason': f'{type(error).__name__}: {error}',
        }
        state_path = save_runner_state(next_state)
        state_meta = describe_runner_state_file()
        steps.append(f'RUNNER_STATE_FAILED meta={state_meta}')
        steps.append(f'BACKOFF applied_seconds={backoff_seconds}')
        steps.append(f'RUNNER_HEALTH status={next_state["last_health_status"]} reason={next_state["last_health_reason"]}')
        if fuse_open:
            steps.append('FUSE opened_after_consecutive_failures')
        return AutoRunnerCycleResult(
            ok=False,
            cycle_started_at=started_at,
            cycle_finished_at=finished_at,
            steps=steps,
            state_path=str(state_path),
            error=f'{type(error).__name__}: {error}',
        )



def run_auto_loop(
    config_path: str,
    env_file: str,
    action_mode: str = 'dry_run',
    cycles: int = 1,
    sleep_seconds: float = 0.0,
    run_forever: bool = False,
    sleep_heartbeat_seconds: float = 5.0,
) -> AutoRunnerLoopResult:
    started_at = utc_now_iso()
    results: list[AutoRunnerCycleResult] = []
    target_cycles = None if run_forever or cycles <= 0 else max(cycles, 1)
    loop_mode = 'resident' if target_cycles is None else 'bounded'
    stop_requested = False
    stop_reason: str | None = None
    exit_reason: str | None = None

    _update_runner_loop_state(
        last_loop_mode=loop_mode,
        last_loop_status='running',
        last_loop_action_mode=action_mode,
        last_loop_started_at=started_at,
        last_loop_finished_at=None,
        last_loop_exit_reason=None,
        last_loop_cycle_target=target_cycles,
        last_loop_cycle_count=0,
        last_loop_heartbeat_interval_seconds=max(float(sleep_heartbeat_seconds or 5.0), 0.25),
        last_loop_sleep_started_at=None,
        last_loop_sleep_until_at=None,
        last_loop_sleep_seconds=0.0,
        last_loop_sleep_remaining_seconds=0.0,
    )

    index = 0
    while target_cycles is None or index < target_cycles:
        stop_signal = load_runner_stop_signal()
        if stop_signal is not None:
            stop_requested = True
            stop_reason = stop_signal.get('reason') or 'manual_stop'
            exit_reason = 'stop_requested'
            detected_at = utc_now_iso()
            _update_runner_loop_state(
                last_loop_status='stop_requested',
                last_loop_exit_reason=exit_reason,
                last_stop_signal_at=stop_signal.get('requested_at') or detected_at,
                last_stop_signal_reason=stop_reason,
                last_heartbeat_at=detected_at,
                last_heartbeat_status='stop_requested',
            )
            break

        state = load_runner_state()
        if state.get('fuse_open') and not state.get('last_recovery_reason'):
            exit_reason = 'fuse_open'
            break

        health_status = state.get('last_health_status')
        if health_status == 'fused' and not state.get('last_recovery_reason'):
            exit_reason = 'fused_health'
            break

        result = run_auto_cycle(config_path=config_path, env_file=env_file, action_mode=action_mode)
        results.append(result)
        index += 1
        _update_runner_loop_state(
            last_loop_status='running',
            last_loop_cycle_count=len(results),
            last_loop_sleep_started_at=None,
            last_loop_sleep_until_at=None,
            last_loop_sleep_seconds=0.0,
            last_loop_sleep_remaining_seconds=0.0,
        )

        if target_cycles is not None and index >= target_cycles:
            exit_reason = 'cycle_limit_reached'
            break

        if target_cycles is None or index < target_cycles:
            state = load_runner_state()
            adaptive_sleep = float(state.get('next_sleep_seconds', sleep_seconds) or sleep_seconds)
            health_status = state.get('last_health_status')
            if health_status == 'warm_restart':
                adaptive_sleep = max(adaptive_sleep, 30.0)
            elif health_status == 'degraded':
                adaptive_sleep = max(adaptive_sleep, 60.0)
            elif health_status == 'fused':
                exit_reason = 'fused_health'
                break
            actual_sleep = adaptive_sleep if adaptive_sleep > 0 else sleep_seconds
            if actual_sleep > 0:
                stop_signal = _sleep_with_heartbeat(actual_sleep, sleep_heartbeat_seconds)
                if stop_signal is not None:
                    stop_requested = True
                    stop_reason = stop_signal.get('reason') or 'manual_stop'
                    exit_reason = 'stop_requested'
                    break
    finished_at = utc_now_iso()

    final_state = load_runner_state()
    final_state.update(
        {
            'last_loop_mode': loop_mode,
            'last_loop_status': ('stopped' if stop_requested else 'idle'),
            'last_loop_action_mode': action_mode,
            'last_loop_started_at': started_at,
            'last_loop_finished_at': finished_at,
            'last_loop_exit_reason': exit_reason or ('stop_requested' if stop_requested else 'completed'),
            'last_loop_cycle_target': target_cycles,
            'last_loop_cycle_count': len(results),
            'last_loop_heartbeat_interval_seconds': max(float(sleep_heartbeat_seconds or 5.0), 0.25),
            'last_loop_sleep_remaining_seconds': 0.0,
            'last_stop_signal_reason': stop_reason or final_state.get('last_stop_signal_reason'),
        }
    )
    if stop_requested:
        final_state['last_heartbeat_at'] = finished_at
        final_state['last_heartbeat_status'] = 'stopped'
    save_runner_state(final_state)

    return AutoRunnerLoopResult(
        ok=all(item.ok for item in results),
        loop_started_at=started_at,
        loop_finished_at=finished_at,
        cycles=results,
        stop_requested=stop_requested,
        stop_reason=stop_reason,
    )
