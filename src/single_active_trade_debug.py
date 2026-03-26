from __future__ import annotations

from pathlib import Path
from pprint import pformat

from .single_active_trade_state import build_single_active_trade_state



def describe_single_active_trade_state(base_dir: str | Path | None = None) -> str:
    snapshot = build_single_active_trade_state(base_dir=base_dir)
    payload = {
        'status': snapshot.status,
        'lock': {
            'active_symbol': snapshot.lock.active_symbol,
            'active_stage': snapshot.lock.active_stage,
            'lock_reason': snapshot.lock.lock_reason,
            'lock_owner': snapshot.lock.lock_owner,
            'current_position_id': snapshot.lock.current_position_id,
            'live_logical_key': snapshot.lock.live_logical_key,
            'blocking': snapshot.lock.blocking,
            'can_admit_new_live_symbol': snapshot.lock.can_admit_new_live_symbol,
            'needs_manual_intervention': snapshot.lock.needs_manual_intervention,
            'source_details': snapshot.lock.source_details,
        },
        'observed_positions': snapshot.observed_positions,
        'observed_inflight': snapshot.observed_inflight,
        'anomalies': snapshot.anomalies,
    }
    return pformat(payload, sort_dicts=False)
