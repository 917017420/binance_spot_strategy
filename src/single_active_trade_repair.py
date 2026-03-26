from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .live_inflight_state import load_live_inflight_state, save_live_inflight_state
from .positions_store import load_active_positions, load_positions, save_positions
from .runner_state import load_runner_state, save_runner_state
from .single_active_trade_state import build_single_active_trade_state
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
REPAIR_LOG_FILE = DEFAULT_EXECUTION_DIR / 'single_active_trade_repairs.jsonl'


@dataclass
class SingleActiveTradeRepairResult:
    ok: bool
    actions: list[str] = field(default_factory=list)
    anomalies_before: list[str] = field(default_factory=list)
    anomalies_after: list[str] = field(default_factory=list)
    summary_before: dict = field(default_factory=dict)
    summary_after: dict = field(default_factory=dict)



def _repair_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / REPAIR_LOG_FILE.name



def _append_repair_log(payload: dict, base_dir: str | Path | None = None) -> Path:
    path = _repair_log_path(base_dir=base_dir)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return path



def _position_sort_key(position) -> tuple:
    active_rank = 0 if position.status == 'open' else 1
    return (
        active_rank,
        -(position.remaining_position_size_pct or 0.0),
        position.entry_time or '',
        position.position_id,
    )



def repair_single_active_trade_state(base_dir: str | Path | None = None, dry_run: bool = False) -> SingleActiveTradeRepairResult:
    before = build_single_active_trade_state(base_dir=base_dir)
    actions: list[str] = []

    positions = load_positions(base_dir=base_dir)
    active_positions = [
        position for position in positions
        if position.status in {'open', 'partially_reduced'} and position.remaining_position_size_pct > 0
    ]
    pending_inflight_state = load_live_inflight_state(base_dir=base_dir)
    orders = dict((pending_inflight_state.get('orders') or {}))

    kept_symbol = None
    kept_position_id = None
    if len(active_positions) > 1:
        kept_position = sorted(active_positions, key=_position_sort_key)[0]
        kept_symbol = kept_position.symbol
        kept_position_id = kept_position.position_id
        for position in active_positions:
            if position.position_id == kept_position_id:
                continue
            actions.append(
                f'POSITION_CANCELLED position_id={position.position_id} symbol={position.symbol} reason=single_active_repair_multiple_active_positions'
            )
            if not dry_run:
                position.status = 'cancelled'
                position.remaining_position_size_pct = 0.0
                if 'single_active_repair_cancelled' not in position.tags:
                    position.tags.append('single_active_repair_cancelled')
                position.notes.append(
                    f'{utc_now_iso()} single_active_trade_repair: cancelled because another active position was chosen as canonical'
                )

    if kept_symbol is None and active_positions:
        kept_position = sorted(active_positions, key=_position_sort_key)[0]
        kept_symbol = kept_position.symbol
        kept_position_id = kept_position.position_id

    if kept_symbol is not None:
        drop_keys = [logical_key for logical_key in orders.keys() if not logical_key.startswith(f'{kept_symbol}|')]
        for logical_key in drop_keys:
            actions.append(f'INFLIGHT_DROPPED logical_key={logical_key} reason=single_active_repair_symbol_conflict keep_symbol={kept_symbol}')
            if not dry_run:
                orders.pop(logical_key, None)

    if not dry_run and len(active_positions) > 1:
        save_positions(positions, base_dir=base_dir)
    if not dry_run and actions:
        save_live_inflight_state({**pending_inflight_state, 'orders': orders}, base_dir=base_dir)

    after = build_single_active_trade_state(base_dir=base_dir) if not dry_run else before
    runner_state = load_runner_state(base_dir=base_dir)
    if not dry_run:
        runner_state['last_single_active_repair_at'] = utc_now_iso()
        runner_state['last_single_active_repair_actions'] = actions
        runner_state['last_single_active_repair_anomalies_before'] = before.anomalies
        runner_state['last_single_active_repair_anomalies_after'] = after.anomalies
        save_runner_state(runner_state, base_dir=base_dir)

        log_path = _append_repair_log(
            {
                'repaired_at': utc_now_iso(),
                'dry_run': dry_run,
                'actions': actions,
                'anomalies_before': before.anomalies,
                'anomalies_after': after.anomalies,
                'summary_before': {
                    'status': before.status,
                    'active_symbol': before.lock.active_symbol,
                    'active_stage': before.lock.active_stage,
                    'lock_reason': before.lock.lock_reason,
                },
                'summary_after': {
                    'status': after.status,
                    'active_symbol': after.lock.active_symbol,
                    'active_stage': after.lock.active_stage,
                    'lock_reason': after.lock.lock_reason,
                },
            },
            base_dir=base_dir,
        )
        actions.append(f'REPAIR_LOG_WRITTEN path={log_path}')

    return SingleActiveTradeRepairResult(
        ok=True,
        actions=actions,
        anomalies_before=before.anomalies,
        anomalies_after=(after.anomalies if not dry_run else before.anomalies),
        summary_before={
            'status': before.status,
            'active_symbol': before.lock.active_symbol,
            'active_stage': before.lock.active_stage,
            'lock_reason': before.lock.lock_reason,
            'kept_position_id': kept_position_id,
        },
        summary_after={
            'status': after.status,
            'active_symbol': after.lock.active_symbol,
            'active_stage': after.lock.active_stage,
            'lock_reason': after.lock.lock_reason,
            'kept_position_id': kept_position_id,
        } if not dry_run else {
            'status': before.status,
            'active_symbol': before.lock.active_symbol,
            'active_stage': before.lock.active_stage,
            'lock_reason': before.lock.lock_reason,
            'kept_position_id': kept_position_id,
        },
    )
