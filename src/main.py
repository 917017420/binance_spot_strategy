from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .auto_entry_gate import evaluate_auto_entry
from .auto_runner import run_auto_cycle, run_auto_loop
from .config import Settings, load_settings
from .exchange import create_exchange, fetch_ohlcv_dataframe
from .market_regime import evaluate_market_regime
from .models import PairAnalysis, ScanReport, SkippedSymbol
from .position_monitor import run_position_monitor, run_position_monitor_auto
from .ranker import split_priority_and_secondary
from .reporter import write_reports
from .route_execution_bridge import build_route_lifecycle_view
from .route_executor import execute_route_candidate
from .runner_recovery import reset_runner_fuse
from .runner_state import clear_runner_stop_signal, derive_runner_runtime_status, load_runner_state, load_runner_stop_signal, runner_stop_signal_path, save_runner_stop_signal
from .live_execution_snapshot import build_live_execution_snapshot
from .single_active_trade_debug import describe_single_active_trade_state
from .single_active_trade_repair import repair_single_active_trade_state
from .control_plane_reconcile import reconcile_control_plane_state
from .single_active_trade_scenarios import format_single_active_trade_scenarios
from .control_plane_brief import format_control_plane_brief
from .binance_readiness_check import format_binance_readiness_check
from .submit_preflight import run_submit_preflight
from .live_order_payload import LiveOrderPayload
from .exchange_state_reconcile import format_exchange_state_reconcile, run_exchange_state_reconcile
from .order_refresh_reconcile import format_order_refresh_reconcile, run_order_refresh_reconcile
from .scan_flow import apply_ranked_candidate_handoff, build_auto_entry_config, scan_symbol_analysis
from .semi_auto_flow import process_confirmation_to_dry_run
from .universe import build_symbol_universe
from .utils import setup_logging, utc_now_iso


LOGGER = logging.getLogger(__name__)


def _load_previous_scan(output_dir: str) -> dict:
    path = Path(output_dir) / "latest_scan.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Failed to load previous scan report %s: %s", path, exc)
        return {}


def _coerce_previous_priority(previous_scan: dict) -> list[dict]:
    priority_candidates = previous_scan.get("priority_candidates")
    if isinstance(priority_candidates, list):
        return priority_candidates
    candidates = previous_scan.get("candidates")
    return candidates if isinstance(candidates, list) else []


def _apply_scan_deltas(priority_candidates: list[PairAnalysis], previous_scan: dict) -> dict:
    previous_priority = _coerce_previous_priority(previous_scan)
    previous_by_symbol: dict[str, dict] = {}
    for index, item in enumerate(previous_priority, start=1):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        score_value = None
        scores = item.get("scores")
        if isinstance(scores, dict):
            score_value = scores.get("total_score")
        if score_value is None:
            score_value = item.get("previous_total_score")
        try:
            total_score = float(score_value) if score_value is not None else None
        except Exception:
            total_score = None
        previous_by_symbol[symbol] = {
            "rank": index,
            "total_score": total_score,
        }

    for index, candidate in enumerate(priority_candidates, start=1):
        previous = previous_by_symbol.get(candidate.symbol.upper())
        if previous is None:
            candidate.score_delta = 0.0
            candidate.rank_delta = 0
            candidate.previous_rank = None
            candidate.previous_total_score = None
            continue
        previous_total = previous.get("total_score")
        candidate.previous_rank = previous.get("rank")
        candidate.previous_total_score = previous_total
        candidate.score_delta = float(candidate.scores.total_score) - float(previous_total or 0.0)
        candidate.rank_delta = int(previous.get("rank") or 0) - index

    current_leader = priority_candidates[0].symbol if priority_candidates else None
    previous_leader = previous_priority[0].get("symbol") if previous_priority and isinstance(previous_priority[0], dict) else None
    return {
        "leader_changed": current_leader != previous_leader,
        "previous_leader": previous_leader,
        "current_leader": current_leader,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance Spot strategy scanner MVP")
    parser.add_argument("--config", default="config/strategy.example.yaml", help="Path to YAML config")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--log-level", default="INFO", help="Logging level")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan Binance Spot symbols")
    scan_parser.add_argument("--top", type=int, default=5, help="Number of results to show in reports")
    scan_parser.add_argument("--max-symbols", type=int, default=None, help="Override how many eligible symbols to scan")
    scan_parser.add_argument("--output-dir", default=None, help="Override output directory for reports")
    scan_parser.add_argument("--symbol", default=None, help="Scan a single symbol like BTC/USDT instead of the ranked universe")
    scan_parser.add_argument("--auto-entry-mode", default=None, choices=["dry_run", "paper", "live"], help="Optional auto-entry execution mode for passed auto-entry candidates")
    scan_parser.add_argument("--equity", type=float, default=1000.0, help="Total quote equity used when executing auto-entry candidates")

    dry_run_parser = subparsers.add_parser("confirm-dry-run", help="Process a confirmation command through the semi-auto dry-run flow")
    dry_run_parser.add_argument("--command-text", required=True, help="Confirmation text such as '确认买入 SOL 6%'")
    dry_run_parser.add_argument("--current-price", required=True, type=float, help="Current market price used for pre-submit checks and dry-run")
    dry_run_parser.add_argument("--market-state", required=True, help="Current market state, e.g. NEUTRAL_MIXED")
    dry_run_parser.add_argument("--equity", required=True, type=float, help="Total quote equity, e.g. 1000")

    monitor_parser = subparsers.add_parser("monitor-positions", help="Evaluate and persist open positions")
    monitor_parser.add_argument("--current-price", type=float, default=None, help="Current market price applied during this monitoring run")
    monitor_parser.add_argument("--market-state", default=None, help="Current market state, e.g. NEUTRAL_MIXED or RISK_OFF")
    monitor_parser.add_argument("--symbol", default="BTC/USDT", help="Symbol used to fetch current market price when --current-price is omitted")
    monitor_parser.add_argument("--action-mode", default="dry_run", choices=["dry_run", "paper", "live"], help="How executable position actions should be handled during monitoring")

    auto_runner_parser = subparsers.add_parser("auto-runner-once", help="Run one auto monitor cycle")
    auto_runner_parser.add_argument("--action-mode", default="dry_run", choices=["dry_run", "paper", "live"], help="How executable position actions should be handled during auto cycle")

    auto_loop_parser = subparsers.add_parser("auto-runner-loop", help="Run multiple auto cycles with optional sleep")
    auto_loop_parser.add_argument("--action-mode", default="dry_run", choices=["dry_run", "paper", "live"], help="How executable position actions should be handled during auto cycle")
    auto_loop_parser.add_argument("--cycles", type=int, default=1, help="How many cycles to run")
    auto_loop_parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep seconds between cycles")
    auto_loop_parser.add_argument("--forever", action="store_true", help="Keep running until a stop signal is requested or fuse health blocks more cycles")
    auto_loop_parser.add_argument("--sleep-heartbeat-seconds", type=float, default=5.0, help="Heartbeat cadence while sleeping between cycles")

    runtime_start_parser = subparsers.add_parser("runtime-start", help="Preferred resident runtime entrypoint; runs the auto loop in resident mode")
    runtime_start_parser.add_argument("--action-mode", default="dry_run", choices=["dry_run", "paper", "live"], help="How executable position actions should be handled during resident runtime")
    runtime_start_parser.add_argument("--sleep-seconds", type=float, default=60.0, help="Resident loop sleep seconds between cycles")
    runtime_start_parser.add_argument("--sleep-heartbeat-seconds", type=float, default=5.0, help="Heartbeat cadence while sleeping between resident cycles")
    runtime_start_parser.add_argument("--clear-stop-signal", action="store_true", help="Clear an existing stop request before starting resident runtime")

    runtime_stop_parser = subparsers.add_parser("runtime-stop", help="Preferred resident runtime stop command")
    runtime_stop_parser.add_argument("--reason", default="operator_stop", help="Reason recorded in the stop signal")
    runtime_stop_parser.add_argument("--wait", action="store_true", help="Wait for the resident runtime to become inactive after requesting stop")
    runtime_stop_parser.add_argument("--timeout-seconds", type=float, default=90.0, help="Maximum seconds to wait for graceful stop when --wait is used")
    runtime_stop_parser.add_argument("--poll-seconds", type=float, default=2.0, help="Polling cadence while waiting for graceful stop")

    subparsers.add_parser("runtime-status", aliases=["runtime-observe"], help="Show concise resident runtime status and operator commands")

    recovery_parser = subparsers.add_parser("reset-runner-fuse", help="Reset runner fuse/degraded state")
    recovery_parser.add_argument("--reason", default="manual_reset", help="Reason for reset")
    stop_parser = subparsers.add_parser("request-runner-stop", help="Request a graceful stop for a resident auto-runner loop")
    stop_parser.add_argument("--reason", default="operator_stop", help="Reason recorded in the stop signal")
    subparsers.add_parser("clear-runner-stop", help="Clear a previously requested runner stop signal")

    subparsers.add_parser("live-execution-snapshot", help="Show aggregated live execution control-plane snapshot")
    subparsers.add_parser("single-active-trade-state", help="Show current single-active-trade lock/state")
    repair_parser = subparsers.add_parser("repair-single-active-trade", help="Repair conflicting single-active-trade state")
    repair_parser.add_argument("--dry-run", action="store_true", help="Show intended repair actions without mutating state")
    subparsers.add_parser("reconcile-control-plane", help="Reconcile control-plane state after exits/unlocks")
    subparsers.add_parser("single-active-trade-scenarios", help="Run scenario checks for single-active-trade control plane")
    subparsers.add_parser("control-plane-brief", help="Show human-readable control-plane briefing")
    subparsers.add_parser("binance-readiness-check", help="Check Binance private-read and submit readiness")
    submit_preflight_parser = subparsers.add_parser("submit-preflight", help="Check live submit preflight without sending orders")
    submit_preflight_parser.add_argument("--symbol", required=True)
    submit_preflight_parser.add_argument("--quote-amount", required=True, type=float)
    submit_preflight_parser.add_argument("--side", default="buy")
    submit_preflight_parser.add_argument("--order-type", default="market")
    submit_preflight_parser.add_argument("--reference-price", default=0.0, type=float)
    subparsers.add_parser("exchange-state-reconcile", help="Compare local execution state with remote Binance account state")
    order_refresh_parser = subparsers.add_parser("order-refresh-reconcile", help="Refresh a Binance order fact and re-apply live fill reconcile")
    order_refresh_parser.add_argument("--symbol", required=False)
    order_refresh_parser.add_argument("--client-order-id", required=False)

    return parser.parse_args()


def _scan_symbol(exchange, symbol: str, quote_volume_24h: float, settings: Settings, regime: str) -> PairAnalysis:
    return scan_symbol_analysis(exchange, symbol, quote_volume_24h, settings, regime)


def run_scan(args: argparse.Namespace) -> int:
    settings = load_settings(args.config, args.env_file)
    if args.max_symbols:
        settings.universe.max_symbols = args.max_symbols
    if args.output_dir:
        settings.output.directory = args.output_dir

    previous_scan = _load_previous_scan(settings.output.directory)
    exchange = create_exchange(settings)
    try:
        symbols, skipped, quote_volume_by_symbol = build_symbol_universe(exchange, settings, top=None)
        if args.symbol:
            requested_symbol = args.symbol.upper()
            if requested_symbol not in quote_volume_by_symbol:
                quote_volume_by_symbol[requested_symbol] = 0.0
            symbols = [requested_symbol]
            skipped = []
        eligible_total = len(symbols)
        btc_quote_volume = quote_volume_by_symbol.get("BTC/USDT", 0.0)
        btc_1h = fetch_ohlcv_dataframe(exchange, "BTC/USDT", timeframe=settings.data.primary_timeframe, limit=settings.data.ohlcv_limit)
        btc_4h = fetch_ohlcv_dataframe(exchange, "BTC/USDT", timeframe=settings.data.context_timeframe, limit=settings.data.ohlcv_limit)
        regime_report = evaluate_market_regime("BTC/USDT", btc_1h, btc_4h, btc_quote_volume)
        settings.runtime_btc_indicators_1h = regime_report.indicators_1h

        analyses: list[PairAnalysis] = []
        for symbol in symbols[: settings.universe.max_symbols]:
            try:
                analyses.append(_scan_symbol(exchange, symbol, quote_volume_by_symbol.get(symbol, 0.0), settings, regime_report.regime))
            except Exception as exc:
                LOGGER.warning("Skipping %s due to failure: %s", symbol, exc)
                skipped.append(SkippedSymbol(symbol=symbol, reason=f"scan_failed:{exc}"))

        priority_candidates, secondary_candidates = split_priority_and_secondary(analyses, args.top)
        apply_ranked_candidate_handoff(priority_candidates, secondary_candidates)
        priority_limit = min(len(priority_candidates), max(args.top, 1))
        remaining_slots = max(args.top - priority_limit, 0)
        top_candidates = priority_candidates[:priority_limit] + secondary_candidates[:remaining_slots]

        auto_entry_cfg = build_auto_entry_config(settings)

        auto_entry_candidates: list[PairAnalysis] = []
        auto_entry_live_candidates: list[PairAnalysis] = []
        auto_entry_paper_candidates: list[PairAnalysis] = []
        auto_entry_shadow_candidates: list[PairAnalysis] = []
        auto_entry_denials: list[str] = []
        auto_entry_decisions: dict[str, dict] = {}
        auto_entry_execution_notes: list[str] = []

        for candidate in priority_candidates:
            decision = evaluate_auto_entry(candidate, market_state=regime_report.regime, config=auto_entry_cfg)
            route_exec = execute_route_candidate(candidate, route=decision.route, mode=args.auto_entry_mode, total_equity_quote=args.equity)
            lifecycle = build_route_lifecycle_view(candidate, route_exec)

            auto_entry_decisions[candidate.symbol] = {
                "route": decision.route,
                "severity": decision.severity,
                "score": decision.score,
                "checks": decision.checks,
                "reasons": decision.reasons,
                "impact": {
                    "day_context_label": candidate.day_context_label,
                    "day_context_config": settings.day_context.model_dump(),
                    "decision_priority": candidate.decision_priority,
                    "position_size_pct": candidate.position_size_pct,
                    "bucket_profile": decision.checks.get("bucket_profile"),
                    "bucket_soft_risk_weight": decision.checks.get("bucket_soft_risk_weight"),
                    "portfolio_soft_risk_points": decision.checks.get("portfolio_soft_risk_points"),
                    "shadow_soft_risk_points_threshold": decision.checks.get("shadow_soft_risk_points_threshold"),
                    "day_context_priority_delta": settings.day_context.trending_bonus if candidate.day_context_label == "TRENDING_HEALTHY" else (-settings.day_context.overheated_penalty if candidate.day_context_label == "OVERHEATED_BREAKOUT" else (-settings.day_context.weak_rebound_penalty if candidate.day_context_label == "WEAK_REBOUND" else 0)),
                    "route_soft_risk_triggered": decision.checks.get("portfolio_soft_risk_points", 0) >= decision.checks.get("shadow_soft_risk_points_threshold", 999),
                },
                "route_execution": {
                    "mode": route_exec.mode,
                    "status": route_exec.status,
                    "message": route_exec.message,
                    "details": route_exec.details,
                },
                "route_lifecycle": {
                    "execution_status": lifecycle.execution_status,
                    "execution_mode": lifecycle.execution_mode,
                    "position_init_status": lifecycle.position_init_status,
                    "position_path": lifecycle.position_path,
                    "position_event_path": lifecycle.position_event_path,
                    "entry_action_path": lifecycle.entry_action_path,
                    "execution_path": lifecycle.execution_path,
                    "notes": lifecycle.notes,
                },
            }

            auto_entry_execution_notes.append(
                f"AUTO_ENTRY_ROUTE {candidate.symbol}: route={decision.route} severity={decision.severity} score={decision.score} reasons={' | '.join(decision.reasons) if decision.reasons else 'none'}"
            )

            if decision.route == "live":
                auto_entry_candidates.append(candidate)
                auto_entry_live_candidates.append(candidate)
            elif decision.route == "paper":
                auto_entry_candidates.append(candidate)
                auto_entry_paper_candidates.append(candidate)
            elif decision.route == "shadow":
                auto_entry_shadow_candidates.append(candidate)
            else:
                auto_entry_denials.append(f"{candidate.symbol}: {'; '.join(decision.reasons)}")

            auto_entry_execution_notes.append(
                f"AUTO_ENTRY_ROUTE_EXEC {candidate.symbol}: route={route_exec.route} mode={route_exec.mode} status={route_exec.status} message={route_exec.message}"
            )
            if route_exec.status == "executed" and route_exec.details.get("entry_action_path"):
                auto_entry_execution_notes.append(
                    f"AUTO_ENTRY_EXECUTED {candidate.symbol}: route={route_exec.route} mode={route_exec.mode} entry_action_path={route_exec.details['entry_action_path']}"
                )
            elif route_exec.status == "tracked":
                auto_entry_execution_notes.append(f"AUTO_ENTRY_SHADOW {candidate.symbol}: {'; '.join(decision.reasons)}")

        scan_deltas = _apply_scan_deltas(priority_candidates, previous_scan)
        execution_ready_candidates = [c for c in priority_candidates if c.decision_action == 'BUY_APPROVED']
        watch_quality_candidates = [c for c in priority_candidates if c.decision_action != 'BUY_APPROVED']
        live_leader = auto_entry_live_candidates[0] if auto_entry_live_candidates else (execution_ready_candidates[0] if execution_ready_candidates else None)

        report = ScanReport(
            generated_at=utc_now_iso(),
            scan_mode="public_data" if not settings.api.enable_private else "api_enabled",
            scanned_symbols=len(analyses),
            eligible_symbols=eligible_total,
            skipped_symbols=skipped,
            market_regime=regime_report,
            config_snapshot={
                "day_context": settings.day_context.model_dump(),
                "auto_entry": settings.auto_entry.model_dump(),
            },
            candidates=top_candidates,
            priority_candidates=priority_candidates,
            execution_ready_candidates=execution_ready_candidates,
            watch_quality_candidates=watch_quality_candidates,
            live_leader=live_leader,
            secondary_candidates=secondary_candidates,
            auto_entry_candidates=auto_entry_candidates,
            auto_entry_live_candidates=auto_entry_live_candidates,
            auto_entry_paper_candidates=auto_entry_paper_candidates,
            auto_entry_shadow_candidates=auto_entry_shadow_candidates,
            auto_entry_allow_count=len(auto_entry_candidates),
            auto_entry_deny_count=len(auto_entry_denials),
            auto_entry_denials=auto_entry_denials,
            auto_entry_decisions=auto_entry_decisions,
            scan_deltas=scan_deltas,
            notes=[
                f"CONFIG_SNAPSHOT day_context={settings.day_context.model_dump()} auto_entry={settings.auto_entry.model_dump()}",
                "Scanner is observation-only and does not submit live spot orders.",
                "Signals are heuristic and intended for shortlist generation, not autonomous execution.",
                *auto_entry_execution_notes,
            ],
        )

        json_path, text_path = write_reports(report, settings.output.directory)
        print(f"Wrote JSON report: {json_path}")
        print(f"Wrote text report: {text_path}")
        print(f"BTC regime: {regime_report.regime} | candidates: {len(top_candidates)} | scanned: {len(analyses)}")
        return 0
    finally:
        close_fn = getattr(exchange, "close", None)
        if callable(close_fn):
            close_fn()


def run_confirm_dry_run(args: argparse.Namespace) -> int:
    result = process_confirmation_to_dry_run(
        command_text=args.command_text,
        current_price=args.current_price,
        market_state=args.market_state,
        total_equity_quote=args.equity,
    )
    print(result)
    return 0 if result.ok else 2


def run_monitor_positions(args: argparse.Namespace) -> int:
    if args.current_price is None or args.market_state is None:
        result = run_position_monitor_auto(args.config, args.env_file, action_mode=args.action_mode)
    else:
        result = run_position_monitor(args.current_price, args.market_state, action_mode=args.action_mode)
    print(result)
    for message in result.messages:
        print(message)
    return 0


def run_auto_runner_once(args: argparse.Namespace) -> int:
    result = run_auto_cycle(config_path=args.config, env_file=args.env_file, action_mode=args.action_mode)
    print(result)
    for step in result.steps:
        print(step)
    return 0 if result.ok else 2


def run_auto_runner_loop(args: argparse.Namespace) -> int:
    result = run_auto_loop(
        config_path=args.config,
        env_file=args.env_file,
        action_mode=args.action_mode,
        cycles=args.cycles,
        sleep_seconds=args.sleep_seconds,
        run_forever=args.forever,
        sleep_heartbeat_seconds=args.sleep_heartbeat_seconds,
    )
    print(result)
    for idx, cycle in enumerate(result.cycles, start=1):
        print(f'--- cycle {idx} ---')
        print(cycle)
        for step in cycle.steps:
            print(step)
    return 0 if result.ok else 2


def run_runtime_start(args: argparse.Namespace) -> int:
    if args.clear_stop_signal:
        clear_runner_stop_signal()

    stop_signal = load_runner_stop_signal()
    if stop_signal is not None:
        runtime = derive_runner_runtime_status(load_runner_state(), stop_signal=stop_signal)
        print(
            {
                'ok': False,
                'message': 'Resident runtime start blocked by pending stop signal; clear it first or pass --clear-stop-signal.',
                'signal': stop_signal,
                'runtime': runtime,
                'recommended_command': runtime.get('commands', {}).get('clear_stop') or 'python3 -m src.main clear-runner-stop',
            }
        )
        return 2

    loop_args = argparse.Namespace(
        config=args.config,
        env_file=args.env_file,
        action_mode=args.action_mode,
        cycles=0,
        sleep_seconds=args.sleep_seconds,
        forever=True,
        sleep_heartbeat_seconds=args.sleep_heartbeat_seconds,
    )
    return run_auto_runner_loop(loop_args)


def run_runtime_stop(args: argparse.Namespace) -> int:
    path = save_runner_stop_signal(reason=args.reason)
    runtime = derive_runner_runtime_status(load_runner_state(), stop_signal=load_runner_stop_signal())
    payload = {
        'ok': True,
        'path': str(path),
        'signal': load_runner_stop_signal(),
        'runtime': runtime,
        'recommended_command': runtime.get('commands', {}).get('stop_and_wait') if not args.wait else runtime.get('commands', {}).get('observe'),
    }
    if not args.wait:
        print(payload)
        return 0

    timeout_seconds = max(float(args.timeout_seconds or 0.0), 0.0)
    poll_seconds = max(float(args.poll_seconds or 0.0), 0.25)
    deadline = time.monotonic() + timeout_seconds
    while True:
        runtime = derive_runner_runtime_status(load_runner_state(), stop_signal=load_runner_stop_signal())
        if not runtime.get('loop_active'):
            payload.update(
                {
                    'ok': True,
                    'stopped': True,
                    'timed_out': False,
                    'runtime': runtime,
                    'message': 'Resident runtime is no longer active after stop request.',
                }
            )
            print(payload)
            return 0
        if runtime.get('heartbeat_stale'):
            payload.update(
                {
                    'ok': False,
                    'stopped': False,
                    'timed_out': False,
                    'runtime': runtime,
                    'message': 'Stop requested, but resident runtime heartbeat is stale; inspect supervisor/process state manually.',
                }
            )
            print(payload)
            return 2
        if time.monotonic() >= deadline:
            payload.update(
                {
                    'ok': False,
                    'stopped': False,
                    'timed_out': True,
                    'runtime': runtime,
                    'message': 'Timed out waiting for resident runtime to stop gracefully.',
                }
            )
            print(payload)
            return 2
        time.sleep(poll_seconds)


def run_runtime_status(args: argparse.Namespace) -> int:
    snapshot = build_live_execution_snapshot()
    summary = snapshot.summary
    runtime = summary.get('runtime') or derive_runner_runtime_status(load_runner_state(), stop_signal=load_runner_stop_signal())
    current_state = summary.get('current_state') or {}
    next_action = summary.get('next_action_plan') or {}

    lines = [
        'RUNTIME',
        f"- status: {runtime.get('status')}",
        f"- mode: {runtime.get('mode')}",
        f"- action_mode: {runtime.get('last_loop_action_mode')}",
        f"- loop_active: {runtime.get('loop_active')}",
        f"- last_loop_started_at: {runtime.get('last_loop_started_at')}",
        f"- last_loop_finished_at: {runtime.get('last_loop_finished_at')}",
        f"- last_loop_exit_reason: {runtime.get('last_loop_exit_reason')}",
        f"- last_loop_cycle_count: {runtime.get('last_loop_cycle_count')}",
        f"- heartbeat_stale: {runtime.get('heartbeat_stale')}",
        f"- last_heartbeat_at: {runtime.get('last_heartbeat_at')}",
        f"- last_heartbeat_status: {runtime.get('last_heartbeat_status')}",
        f"- heartbeat_age_seconds: {runtime.get('heartbeat_age_seconds')}",
        f"- heartbeat_timeout_seconds: {runtime.get('heartbeat_timeout_seconds')}",
        f"- last_successful_cycle_at: {runtime.get('last_successful_cycle_at')}",
        f"- sleep_until_at: {runtime.get('last_loop_sleep_until_at')}",
        f"- sleep_remaining_seconds: {runtime.get('last_loop_sleep_remaining_seconds')}",
        f"- stop_signal_present: {runtime.get('stop_signal_present')}",
        f"- stop_signal_reason: {runtime.get('stop_signal_reason')}",
        f"- stop_signal_requested_at: {runtime.get('stop_signal_requested_at')}",
        f"- stop_signal_age_seconds: {runtime.get('stop_signal_age_seconds')}",
        f"- start_blocked_by_stop_signal: {runtime.get('start_blocked_by_stop_signal')}",
        f"- summary: {runtime.get('summary')}",
        f"- operator_hint: {runtime.get('operator_hint')}",
        '',
        'CONTROL',
        f"- status: {current_state.get('status')}",
        f"- active_symbol: {current_state.get('active_symbol')}",
        f"- active_stage: {current_state.get('active_stage')}",
        f"- active_position_under_management: {current_state.get('active_position_under_management')}",
        f"- can_push_live_now: {current_state.get('can_push_live_now')}",
        f"- needs_manual_intervention: {current_state.get('needs_manual_intervention')}",
        '',
        'COMMANDS',
        f"- observe: {runtime.get('commands', {}).get('observe')}",
        f"- control_plane: {runtime.get('commands', {}).get('control_plane')}",
        f"- start: {runtime.get('commands', {}).get('start')}",
        f"- stop: {runtime.get('commands', {}).get('stop')}",
        f"- stop_and_wait: {runtime.get('commands', {}).get('stop_and_wait')}",
        f"- clear_stop: {runtime.get('commands', {}).get('clear_stop')}",
        f"- recommended_command: {next_action.get('recommended_command') or runtime.get('recommended_command')}",
    ]
    print('\n'.join(lines))
    return 0


def run_request_runner_stop(args: argparse.Namespace) -> int:
    path = save_runner_stop_signal(reason=args.reason)
    print(
        {
            'ok': True,
            'path': str(path),
            'signal': load_runner_stop_signal(),
        }
    )
    return 0


def run_clear_runner_stop(args: argparse.Namespace) -> int:
    print(
        {
            'ok': True,
            'cleared': clear_runner_stop_signal(),
            'path': str(runner_stop_signal_path()),
        }
    )
    return 0


def run_reset_runner_fuse(args: argparse.Namespace) -> int:
    result = reset_runner_fuse(reason=args.reason)
    print(result)
    for message in result.messages:
        print(message)
    return 0 if result.ok else 2


def run_live_execution_snapshot(args: argparse.Namespace) -> int:
    snapshot = build_live_execution_snapshot()
    summary = snapshot.summary
    primary_view = {
        'current_state': summary.get('current_state'),
        'current_blockers': summary.get('current_blockers'),
        'historical_residue': summary.get('historical_residue'),
        'recent_history': summary.get('recent_history'),
    }
    print(snapshot)
    print(primary_view)
    return 0


def run_single_active_trade_state(args: argparse.Namespace) -> int:
    print(describe_single_active_trade_state())
    return 0


def run_repair_single_active_trade(args: argparse.Namespace) -> int:
    result = repair_single_active_trade_state(dry_run=args.dry_run)
    print(result)
    for action in result.actions:
        print(action)
    return 0


def run_reconcile_control_plane(args: argparse.Namespace) -> int:
    result = reconcile_control_plane_state()
    print(result)
    for action in result.actions:
        print(action)
    return 0


def run_single_active_trade_scenarios(args: argparse.Namespace) -> int:
    print(format_single_active_trade_scenarios())
    return 0


def run_control_plane_brief(args: argparse.Namespace) -> int:
    print(format_control_plane_brief())
    return 0


def run_binance_readiness_check(args: argparse.Namespace) -> int:
    print(format_binance_readiness_check(config_path=args.config, env_path=args.env_file))
    return 0


def run_submit_preflight_cmd(args: argparse.Namespace) -> int:
    settings = load_settings(config_path=args.config, env_path=args.env_file)
    exchange = create_exchange(settings)
    try:
        markets = exchange.load_markets()
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()
    payload = LiveOrderPayload(
        symbol=args.symbol,
        side=args.side,
        order_type=args.order_type,
        quote_amount=args.quote_amount,
        client_order_id='preflight-check',
        reference_price=args.reference_price,
        requested_position_size_pct=0.0,
        metadata={'source': 'submit_preflight_cmd'},
    )
    result = run_submit_preflight(settings, payload, markets=markets)
    print(result)
    return 0 if result.ok else 2


def run_exchange_state_reconcile_cmd(args: argparse.Namespace) -> int:
    result = run_exchange_state_reconcile(config_path=args.config, env_path=args.env_file)
    print(format_exchange_state_reconcile(result=result))
    return 0 if result.ok else 2


def run_order_refresh_reconcile_cmd(args: argparse.Namespace) -> int:
    result = run_order_refresh_reconcile(symbol=args.symbol, client_order_id=args.client_order_id, config_path=args.config, env_path=args.env_file)
    print(format_order_refresh_reconcile(result=result))
    return 0 if result.ok else 2


def main() -> int:
    args = _parse_args()
    setup_logging(args.log_level)
    if args.command == "scan":
        return run_scan(args)
    if args.command == "confirm-dry-run":
        return run_confirm_dry_run(args)
    if args.command == "monitor-positions":
        return run_monitor_positions(args)
    if args.command == "auto-runner-once":
        return run_auto_runner_once(args)
    if args.command == "auto-runner-loop":
        return run_auto_runner_loop(args)
    if args.command == "runtime-start":
        return run_runtime_start(args)
    if args.command == "runtime-stop":
        return run_runtime_stop(args)
    if args.command in {"runtime-status", "runtime-observe"}:
        return run_runtime_status(args)
    if args.command == "reset-runner-fuse":
        return run_reset_runner_fuse(args)
    if args.command == "request-runner-stop":
        return run_request_runner_stop(args)
    if args.command == "clear-runner-stop":
        return run_clear_runner_stop(args)
    if args.command == "live-execution-snapshot":
        return run_live_execution_snapshot(args)
    if args.command == "single-active-trade-state":
        return run_single_active_trade_state(args)
    if args.command == "repair-single-active-trade":
        return run_repair_single_active_trade(args)
    if args.command == "reconcile-control-plane":
        return run_reconcile_control_plane(args)
    if args.command == "single-active-trade-scenarios":
        return run_single_active_trade_scenarios(args)
    if args.command == "control-plane-brief":
        return run_control_plane_brief(args)
    if args.command == "binance-readiness-check":
        return run_binance_readiness_check(args)
    if args.command == "submit-preflight":
        return run_submit_preflight_cmd(args)
    if args.command == "exchange-state-reconcile":
        return run_exchange_state_reconcile_cmd(args)
    if args.command == "order-refresh-reconcile":
        return run_order_refresh_reconcile_cmd(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
