from __future__ import annotations

from collections import Counter
from pathlib import Path

from .asset_buckets import classify_asset_bucket
from .models import PairAnalysis, ScanReport
from .utils import ensure_directory


def _format_gate_explainability(report: ScanReport, symbol: str) -> list[str]:
    decision = report.auto_entry_decisions.get(symbol, {})
    if not decision:
        return ["  gate: no structured decision recorded"]
    checks = decision.get("checks", {})
    reasons = decision.get("reasons", [])
    route_exec = decision.get('route_execution', {})
    return [
        f"  gate: route={decision.get('route', 'unknown')} severity={decision.get('severity', 'unknown')} score={decision.get('score', 0)}",
        f"  gate checks: {'; '.join(f'{k}={v}' for k, v in checks.items()) or 'none'}",
        f"  gate soft-risk: bucket_profile={checks.get('bucket_profile', 'n/a')} bucket_weight={checks.get('bucket_soft_risk_weight', 'n/a')} points={checks.get('portfolio_soft_risk_points', 'n/a')} shadow_threshold={checks.get('shadow_soft_risk_points_threshold', 'n/a')}",
        f"  gate reasons: {'; '.join(reasons) or 'none'}",
        f"  route execution: mode={route_exec.get('mode', 'n/a')} status={route_exec.get('status', 'n/a')} policy={route_exec.get('details', {}).get('execution_policy', 'n/a')} message={route_exec.get('message', 'n/a')}",
        f"  route lifecycle: position_init_status={decision.get('route_lifecycle', {}).get('position_init_status', 'n/a')} entry_action_path={decision.get('route_lifecycle', {}).get('entry_action_path', 'n/a')} position_path={decision.get('route_lifecycle', {}).get('position_path', 'n/a')}",
    ]


def _format_candidate(candidate: PairAnalysis, report: ScanReport) -> str:
    one_h = candidate.indicators_1h
    decision = report.auto_entry_decisions.get(candidate.symbol, {})
    route_exec = decision.get('route_execution', {})
    lines = [
        f"- {candidate.symbol} | {candidate.signal} | total={candidate.scores.total_score}",
        f"  secondary={candidate.secondary_signal or 'NONE'} decision={candidate.decision_action or 'NONE'} priority={candidate.decision_priority}",
        f"  delta: score_delta={candidate.score_delta:+.1f} rank_delta={candidate.rank_delta:+d} prev_rank={candidate.previous_rank} prev_total={candidate.previous_total_score}",
        f"  execution_stage={candidate.execution_stage or 'NONE'} attention_level={candidate.attention_level or 'NONE'}",
        f"  route execution summary: route={decision.get('route', 'n/a')} status={route_exec.get('status', 'n/a')} mode={route_exec.get('mode', 'n/a')} message={route_exec.get('message', 'n/a')}",
        f"  regime={candidate.regime} liquidity={candidate.scores.liquidity_score} trend={candidate.scores.trend_score} strength={candidate.scores.strength_score} breakout={candidate.scores.breakout_score} runway={candidate.scores.runway_score} runway_penalty={candidate.scores.runway_penalty} mtf={candidate.scores.mtf_alignment_score} structure={candidate.scores.structure_quality_score} exec={candidate.scores.execution_quality_score} overext={candidate.scores.overextension_penalty}",
        f"  close={one_h.close:.4f} ema20={one_h.ema20:.4f} ema50={one_h.ema50:.4f} ema200={one_h.ema200:.4f}",
        f"  high20={one_h.high20:.4f} low20={one_h.low20:.4f} atr%={one_h.atr14_pct:.2f} vol={one_h.volume:.2f} avg20={one_h.avg_volume20:.2f}",
        f"  change24h={one_h.change_24h_pct:.2f}% change7d={one_h.change_7d_pct:.2f}% upper_wick={one_h.upper_wick_pct:.1f}% body={one_h.body_pct:.1f}%",
        f"  relative: vs BTC 24h={'stronger' if any('24h performance is stronger than BTC' in r for r in candidate.scores.reasons) else 'not stronger'} | vs BTC 7d={'stronger' if any('7d performance is stronger than BTC' in r for r in candidate.scores.reasons) else 'not stronger'}",
        f"  penalty flags: {'; '.join([r for r in candidate.scores.reasons if 'stretched' in r or 'extended' in r or 'upper wick' in r or 'Volatility is unusually high' in r][:4]) or 'none'}",
        f"  reasons: {'; '.join(candidate.reasons + candidate.scores.reasons[:4])}",
        f"  decision reasons: {'; '.join(candidate.decision_reasons) or 'none'}",
        f"  day context: label={candidate.day_context_label or 'NONE'} change24h={candidate.symbol_change_24h_pct:.2f}% range24h={candidate.symbol_range_24h_pct:.2f}% close_pos={candidate.close_position_in_24h_range:.2f} pullback={candidate.pullback_from_24h_high_pct:.2f}% vs_btc_delta={candidate.vs_btc_24h_delta:.2f}%",
        f"  competition bucket={classify_asset_bucket(candidate.symbol)}",
        f"  positive reasons: {'; '.join(candidate.positive_reasons[:3]) or 'none'}",
        f"  blocking reasons: {'; '.join(candidate.blocking_reasons[:3]) or 'none'}",
        f"  penalty reasons: {'; '.join(candidate.penalty_reasons[:3]) or 'none'}",
        f"  execution-fit: est_quote={candidate.execution_estimated_quote_amount:.2f} est_base={candidate.execution_estimated_base_amount:.6f} min_notional_ok={candidate.execution_min_notional_ok} min_amount_ok={candidate.execution_min_amount_ok} dust_risk={candidate.execution_dust_risk or 'none'} tiny_live_sensitivity={'elevated' if candidate.execution_dust_risk else 'normal'}",
        f"  tiny-live-fit: quote={candidate.execution_tiny_live_quote_amount:.2f} base={candidate.execution_tiny_live_base_amount:.6f} min_amount_ok={candidate.execution_tiny_live_min_amount_ok} market_min_amount={candidate.execution_market_min_amount:.6f} amount_step={candidate.execution_market_amount_step:.8f}",
        f"  runway/risk: upside={candidate.runway_upside_pct:.2f}% near_high={candidate.near_local_high} distance_to_high={candidate.distance_to_local_high_pct:.2f}% rr={candidate.reward_risk_ratio:.2f} upside_plan={candidate.expected_upside_pct:.2f}% downside_plan={candidate.expected_downside_pct:.2f}%",
        f"  exit plan: stop={(candidate.planned_initial_stop_price or 0.0):.4f} tp1={(candidate.planned_tp1_price or 0.0):.4f} tp2={(candidate.planned_tp2_price or 0.0):.4f}",
        f"  risk: invalidation={(candidate.risk.invalidation_level or 0.0):.4f} atr_buffer={(candidate.risk.atr_based_buffer or 0.0):.4f}",
        f"  risk notes: {'; '.join(candidate.risk.notes[:2]) or 'none'}",
    ]
    lines.extend(_format_gate_explainability(report, candidate.symbol))
    return "\n".join(lines)


def _cmp_line(name: str, leader_value, runner_value, *, prefer_higher: bool = True) -> str | None:
    if leader_value == runner_value:
        return None
    if prefer_higher:
        winner = 'leader' if leader_value > runner_value else 'next'
    else:
        winner = 'leader' if leader_value < runner_value else 'next'
    return f"{name}: leader={leader_value} next={runner_value} winner={winner}"


def _render_priority_comparison(report: ScanReport) -> list[str]:
    priority = report.priority_candidates or []
    if len(priority) < 2:
        return []
    leader = priority[0]
    runner_up = priority[1]
    comparisons = [
        _cmp_line('total_score', leader.scores.total_score, runner_up.scores.total_score),
        _cmp_line('mtf', leader.scores.mtf_alignment_score, runner_up.scores.mtf_alignment_score),
        _cmp_line('structure', leader.scores.structure_quality_score, runner_up.scores.structure_quality_score),
        _cmp_line('exec', leader.scores.execution_quality_score, runner_up.scores.execution_quality_score),
        _cmp_line('decision_priority', leader.decision_priority, runner_up.decision_priority),
    ]
    comparisons = [item for item in comparisons if item]
    if not comparisons:
        comparisons.append('leader edges are marginal; review qualitative reasons')
    return [
        'Priority comparison:',
        f"- leader={leader.symbol} vs next={runner_up.symbol}",
        *[f"- {item}" for item in comparisons],
        f"- leader_signal={leader.signal} next_signal={runner_up.signal}",
    ]


def render_text_report(report: ScanReport) -> str:
    skipped_counts = Counter(item.reason for item in report.skipped_symbols)
    day_cfg = report.config_snapshot.get("day_context", {})
    auto_cfg = report.config_snapshot.get("auto_entry", {})
    if (not day_cfg or not auto_cfg) and report.notes:
        for note in report.notes:
            if note.startswith("CONFIG_SNAPSHOT "):
                snapshot_text = note.removeprefix("CONFIG_SNAPSHOT ")
                day_cfg = day_cfg or snapshot_text
                auto_cfg = auto_cfg or snapshot_text
                break
    lines = [
        "Binance Spot Strategy Scanner MVP",
        f"Generated at: {report.generated_at}",
        f"Mode: {report.scan_mode}",
        f"BTC regime: {report.market_regime.regime} (score={report.market_regime.score})",
        f"BTC regime reasons: {'; '.join(report.market_regime.reasons)}",
        f"Eligible symbols: {report.eligible_symbols}",
        f"Scanned symbols: {report.scanned_symbols}",
        f"Candidates found: {len(report.candidates)}",
        f"Priority list count: {len(report.priority_candidates)}",
        f"Execution-ready count: {len(report.execution_ready_candidates)}",
        f"Watch-quality count: {len(report.watch_quality_candidates)}",
        f"Secondary watchlist count: {len(report.secondary_candidates)}",
        f"Config snapshot: day_context={day_cfg or 'none'} auto_entry={auto_cfg or 'none'}",
        "",
    ]
    if report.live_leader is not None:
        delta = report.scan_deltas or {}
        lines.extend([
            'Live leader:',
            f"- {report.live_leader.symbol} | action={report.live_leader.decision_action} stage={report.live_leader.execution_stage} priority={report.live_leader.decision_priority} total={report.live_leader.scores.total_score}",
            f"- leader_changed={delta.get('leader_changed')} previous_leader={delta.get('previous_leader')} current_leader={delta.get('current_leader')}",
            '',
        ])

    lines.append('Execution-ready priority:')
    if report.execution_ready_candidates:
        for candidate in report.execution_ready_candidates:
            lines.append(_format_candidate(candidate, report))
            lines.append("")
    else:
        lines.append("- No execution-ready priority candidates met the current criteria.")
        lines.append("")

    comparison_lines = _render_priority_comparison(report)
    if comparison_lines:
        lines.append("")
        lines.extend(comparison_lines)
        lines.append("")

    lines.append('Watch-quality priority:')
    if report.watch_quality_candidates:
        for candidate in report.watch_quality_candidates:
            lines.append(_format_candidate(candidate, report))
            lines.append('')
    else:
        lines.append('- No watch-quality priority candidates.')
        lines.append('')

    lines.append("Secondary watchlist:")
    if report.secondary_candidates:
        for candidate in report.secondary_candidates:
            lines.append(_format_candidate(candidate, report))
            lines.append("")
    else:
        lines.append("- No secondary watchlist candidates.")
        lines.append("")

    lines.append("Auto-entry gate summary:")
    if report.auto_entry_live_candidates:
        for candidate in report.auto_entry_live_candidates:
            lines.append(f"- ROUTE_LIVE {candidate.symbol}")
    if report.auto_entry_paper_candidates:
        for candidate in report.auto_entry_paper_candidates:
            lines.append(f"- ROUTE_PAPER {candidate.symbol}")
    if report.auto_entry_shadow_candidates:
        for candidate in report.auto_entry_shadow_candidates:
            lines.append(f"- ROUTE_SHADOW {candidate.symbol}")
    if not (report.auto_entry_live_candidates or report.auto_entry_paper_candidates or report.auto_entry_shadow_candidates):
        lines.append("- No auto-entry candidates passed the current gate.")
    if report.auto_entry_denials:
        for denial in report.auto_entry_denials:
            lines.append(f"- DENY {denial}")
    else:
        lines.append("- No auto-entry denials recorded.")
    lines.append("")

    lines.append("Skipped symbols summary:")
    if skipped_counts:
        for reason, count in skipped_counts.most_common(12):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- None")

    route_exec_notes = [note for note in report.notes if note.startswith("AUTO_ENTRY_ROUTE_EXEC")]
    if route_exec_notes:
        lines.append("")
        lines.append("Auto-entry route execution notes:")
        for note in route_exec_notes:
            lines.append(f"- {note}")

    route_notes = [note for note in report.notes if note.startswith("AUTO_ENTRY_ROUTE") and not note.startswith("AUTO_ENTRY_ROUTE_EXEC")]
    if route_notes:
        lines.append("")
        lines.append("Auto-entry route notes:")
        for note in route_notes:
            lines.append(f"- {note}")

    if report.notes:
        lines.append("")
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"- {note}")

    return "\n".join(lines).strip() + "\n"


def write_reports(report: ScanReport, output_dir: str) -> tuple[Path, Path]:
    directory = ensure_directory(output_dir)
    json_path = directory / "latest_scan.json"
    text_path = directory / "latest_scan.txt"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    text_path.write_text(render_text_report(report), encoding="utf-8")
    return json_path, text_path
