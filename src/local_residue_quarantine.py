from __future__ import annotations

from dataclasses import dataclass, field

from .live_inflight_state import extract_symbol_from_logical_key, load_live_inflight_state, save_live_inflight_state
from .positions_store import load_live_active_positions
from .utils import utc_now_iso


QUARANTINABLE_STATUSES = {'partial', 'partially_filled', 'partially-filled', 'open'}


@dataclass
class LocalResidueQuarantineResult:
    ok: bool
    apply_changes: bool
    symbol: str
    blocked_reason: str | None = None
    matched_keys: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)



def run_local_residue_quarantine(*, symbol: str, apply_changes: bool = False, base_dir=None) -> LocalResidueQuarantineResult:
    target_symbol = symbol.upper().strip()
    inflight_state = load_live_inflight_state(base_dir=base_dir)
    active_symbols = {position.symbol for position in load_live_active_positions(base_dir=base_dir)}

    if target_symbol in active_symbols:
        return LocalResidueQuarantineResult(
            ok=False,
            apply_changes=apply_changes,
            symbol=target_symbol,
            blocked_reason='symbol_is_currently_active',
            actions=[f'BLOCKED symbol={target_symbol} is currently active and cannot be quarantined'],
        )

    orders = dict(inflight_state.get('orders') or {})
    quarantined = dict(inflight_state.get('quarantined') or {})
    matched_keys: list[str] = []

    for logical_key, item in orders.items():
        status = str(item.get('status') or '').lower()
        order_symbol = extract_symbol_from_logical_key(logical_key)
        if order_symbol != target_symbol:
            continue
        if status not in QUARANTINABLE_STATUSES:
            continue
        matched_keys.append(logical_key)

    if not matched_keys:
        return LocalResidueQuarantineResult(
            ok=True,
            apply_changes=apply_changes,
            symbol=target_symbol,
            matched_keys=[],
            actions=[f'NO_MATCH symbol={target_symbol} quarantinable residue not found'],
        )

    actions = [f'MATCHED_RESIDUE symbol={target_symbol} keys={matched_keys}']
    if apply_changes:
        for logical_key in matched_keys:
            item = orders.pop(logical_key)
            quarantined[logical_key] = {
                **item,
                'quarantined_at': utc_now_iso(),
                'reason': 'operator_quarantine_orphan_partial_fill_residue',
            }
        next_state = {
            **inflight_state,
            'orders': orders,
            'quarantined': quarantined,
        }
        save_live_inflight_state(next_state, base_dir=base_dir)
        actions.append(f'APPLIED_QUARANTINE symbol={target_symbol} count={len(matched_keys)}')

    return LocalResidueQuarantineResult(
        ok=True,
        apply_changes=apply_changes,
        symbol=target_symbol,
        matched_keys=matched_keys,
        actions=actions,
    )



def format_local_residue_quarantine(*, symbol: str, apply_changes: bool = False, base_dir=None) -> str:
    result = run_local_residue_quarantine(symbol=symbol, apply_changes=apply_changes, base_dir=base_dir)
    lines = [
        'LOCAL RESIDUE QUARANTINE',
        f'- ok: {result.ok}',
        f'- apply_changes: {result.apply_changes}',
        f'- symbol: {result.symbol}',
        f'- blocked_reason: {result.blocked_reason}',
        f'- matched_keys: {result.matched_keys}',
        '',
        'ACTIONS',
    ]
    for item in result.actions:
        lines.append(f'- {item}')
    if not result.actions:
        lines.append('- none')
    return '\n'.join(lines)
