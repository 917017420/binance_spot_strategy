from __future__ import annotations

from dataclasses import dataclass, field

from .live_inflight_state import (
    load_live_inflight_state,
    prune_clearable_live_order_residue,
    save_live_inflight_state,
    summarize_live_order_residue,
)
from .live_submit_state import (
    load_live_submit_state,
    prune_clearable_live_submit_state,
    save_live_submit_state,
    summarize_live_submit_state,
)
from .positions_store import load_live_active_positions


@dataclass
class LocalResidueAuditResult:
    ok: bool
    apply_changes: bool
    active_symbols: list[str] = field(default_factory=list)
    inflight_symbols: list[str] = field(default_factory=list)
    inflight_residue_summary: dict = field(default_factory=dict)
    submit_state_summary: dict = field(default_factory=dict)
    clearable_residue_keys: list[str] = field(default_factory=list)
    archived_submit: dict | None = None
    actions: list[str] = field(default_factory=list)
    recommended_action: str | None = None



def run_local_residue_audit(*, apply_changes: bool = False, older_than_seconds: float = 1800.0, base_dir=None) -> LocalResidueAuditResult:
    active_positions = load_live_active_positions(base_dir=base_dir)
    active_symbols = sorted({position.symbol for position in active_positions})

    inflight_state = load_live_inflight_state(base_dir=base_dir)
    inflight_symbols = sorted({
        key.split('|')[0]
        for key in (inflight_state.get('orders') or {}).keys()
        if key and '|' in key
    })
    submit_state = load_live_submit_state(base_dir=base_dir)

    inflight_residue_summary = summarize_live_order_residue(
        inflight_state,
        active_symbol=active_symbols[0] if len(active_symbols) == 1 else None,
        stale_after_seconds=older_than_seconds,
    )
    submit_state_summary = summarize_live_submit_state(
        submit_state,
        active_symbols=set(active_symbols),
        inflight_symbols=set(inflight_symbols),
        stale_after_seconds=older_than_seconds,
    )

    next_inflight_state, clearable_residue_keys = prune_clearable_live_order_residue(
        inflight_state,
        active_symbols=set(active_symbols),
        older_than_seconds=older_than_seconds,
    )
    next_submit_state, archived_submit = prune_clearable_live_submit_state(
        submit_state,
        active_symbols=set(active_symbols),
        inflight_symbols=set(inflight_symbols),
        older_than_seconds=older_than_seconds,
    )

    actions: list[str] = []
    if clearable_residue_keys:
        actions.append(f"CLEARABLE_LIVE_ORDER_RESIDUE keys={clearable_residue_keys}")
    if archived_submit is not None:
        actions.append(
            f"CLEARABLE_LIVE_SUBMIT_STATE status={archived_submit.get('last_submit_status')} symbol={archived_submit.get('last_symbol')}"
        )

    if apply_changes:
        if clearable_residue_keys:
            save_live_inflight_state(next_inflight_state, base_dir=base_dir)
            actions.append(f"APPLIED_LIVE_ORDER_RESIDUE_PRUNE removed={len(clearable_residue_keys)}")
        if archived_submit is not None:
            save_live_submit_state(next_submit_state, base_dir=base_dir)
            actions.append("APPLIED_LIVE_SUBMIT_STATE_ARCHIVE")

    recommended_action = None
    if inflight_residue_summary.get('needs_manual_attention'):
        recommended_action = 'manual_review_orphan_partial_fill_residue'
    elif clearable_residue_keys or archived_submit is not None:
        recommended_action = 'safe_local_cleanup_available'
    else:
        recommended_action = 'no_local_cleanup_needed'

    return LocalResidueAuditResult(
        ok=True,
        apply_changes=apply_changes,
        active_symbols=active_symbols,
        inflight_symbols=inflight_symbols,
        inflight_residue_summary=inflight_residue_summary,
        submit_state_summary=submit_state_summary,
        clearable_residue_keys=clearable_residue_keys,
        archived_submit=archived_submit,
        actions=actions,
        recommended_action=recommended_action,
    )



def format_local_residue_audit(*, apply_changes: bool = False, older_than_seconds: float = 1800.0, base_dir=None) -> str:
    result = run_local_residue_audit(
        apply_changes=apply_changes,
        older_than_seconds=older_than_seconds,
        base_dir=base_dir,
    )
    lines = [
        'LOCAL RESIDUE AUDIT',
        f'- ok: {result.ok}',
        f'- apply_changes: {result.apply_changes}',
        f'- recommended_action: {result.recommended_action}',
        f'- active_symbols: {result.active_symbols}',
        f'- inflight_symbols: {result.inflight_symbols}',
        '',
        'INFLIGHT RESIDUE',
        f"- count: {result.inflight_residue_summary.get('count')}",
        f"- partial_fill_count: {result.inflight_residue_summary.get('partial_fill_count')}",
        f"- terminal_count: {result.inflight_residue_summary.get('terminal_count')}",
        f"- stale_count: {result.inflight_residue_summary.get('stale_count')}",
        f"- orphan_symbols: {result.inflight_residue_summary.get('orphan_symbols')}",
        f"- needs_manual_attention: {result.inflight_residue_summary.get('needs_manual_attention')}",
        '',
        'SUBMIT STATE',
        f"- classification: {result.submit_state_summary.get('classification')}",
        f"- status: {result.submit_state_summary.get('status')}",
        f"- symbol: {result.submit_state_summary.get('symbol')}",
        f"- should_archive: {result.submit_state_summary.get('should_archive')}",
        '',
        'CLEARABLE',
        f'- live_order_residue_keys: {result.clearable_residue_keys}',
        f'- archived_submit_candidate: {result.archived_submit}',
        '',
        'ACTIONS',
    ]
    for item in result.actions:
        lines.append(f'- {item}')
    if not result.actions:
        lines.append('- none')
    return '\n'.join(lines)
