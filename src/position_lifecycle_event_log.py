from __future__ import annotations

import json
from pathlib import Path

from .position_action_executor import load_position_action_results
from .position_lifecycle_bridge import build_lifecycle_view_from_action, build_lifecycle_view_from_event
from .positions_store import load_position_events
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
POSITION_LIFECYCLE_EVENTS_FILE = DEFAULT_EXECUTION_DIR / 'position_lifecycle_events.jsonl'
POSITION_LIFECYCLE_EVENTS_STATE_FILE = DEFAULT_EXECUTION_DIR / 'position_lifecycle_events_state.json'


def _position_lifecycle_events_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITION_LIFECYCLE_EVENTS_FILE.name


def _position_lifecycle_events_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITION_LIFECYCLE_EVENTS_STATE_FILE.name


def _load_snapshot_state(base_dir: str | Path | None = None) -> dict:
    path = _position_lifecycle_events_state_path(base_dir)
    if not path.exists():
        return {'seen_event_ids': [], 'seen_action_ids': []}
    return json.loads(path.read_text(encoding='utf-8'))


def _save_snapshot_state(state: dict, base_dir: str | Path | None = None) -> Path:
    path = _position_lifecycle_events_state_path(base_dir)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def _iter_snapshot_records(base_dir: str | Path | None = None) -> tuple[list[dict], dict]:
    records: list[dict] = []
    state = _load_snapshot_state(base_dir=base_dir)
    seen_event_ids = set(state.get('seen_event_ids', []))
    seen_action_ids = set(state.get('seen_action_ids', []))

    events = load_position_events(base_dir=base_dir)
    actions = load_position_action_results(base_dir=base_dir)
    for event in events[-20:]:
        if event.event_id in seen_event_ids:
            continue
        view = build_lifecycle_view_from_event(event)
        records.append({
            'captured_at': utc_now_iso(),
            'position_id': view.position_id,
            'symbol': view.symbol,
            'lifecycle_stage': view.lifecycle_stage,
            'source': view.source,
            'source_event_id': event.event_id,
            'source_event_type': view.source_event_type,
            'source_action': view.source_action,
            'notes': view.notes,
        })
        seen_event_ids.add(event.event_id)
    for action in actions[-20:]:
        if action.action_id in seen_action_ids:
            continue
        view = build_lifecycle_view_from_action(action)
        records.append({
            'captured_at': utc_now_iso(),
            'position_id': view.position_id,
            'symbol': view.symbol,
            'lifecycle_stage': view.lifecycle_stage,
            'source': view.source,
            'source_action_id': action.action_id,
            'source_event_type': view.source_event_type,
            'source_action': view.source_action,
            'notes': view.notes,
        })
        seen_action_ids.add(action.action_id)

    next_state = {
        'seen_event_ids': list(seen_event_ids)[-200:],
        'seen_action_ids': list(seen_action_ids)[-200:],
    }
    return records, next_state


def snapshot_position_lifecycle_events(base_dir: str | Path | None = None) -> Path:
    path = _position_lifecycle_events_path(base_dir)
    records, next_state = _iter_snapshot_records(base_dir=base_dir)
    with path.open('a', encoding='utf-8') as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    _save_snapshot_state(next_state, base_dir=base_dir)
    return path
