from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
ORDER_LIFECYCLE_LOG_FILE = DEFAULT_EXECUTION_DIR / 'order_lifecycle_events.jsonl'


def _order_lifecycle_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / ORDER_LIFECYCLE_LOG_FILE.name


def tail_order_lifecycle_events(base_dir: str | Path | None = None, limit: int = 50) -> list[dict]:
    path = _order_lifecycle_log_path(base_dir)
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').splitlines()
    events: list[dict] = []
    for line in lines[-limit:]:
        if line.strip():
            events.append(json.loads(line))
    return events


def has_recent_order_lifecycle_event(*, event: str, symbol: str, client_order_id: str, status: str | None = None, base_dir: str | Path | None = None, limit: int = 100) -> bool:
    recent = tail_order_lifecycle_events(base_dir=base_dir, limit=limit)
    for item in reversed(recent):
        if item.get('event') != event:
            continue
        if item.get('symbol') != symbol:
            continue
        if item.get('client_order_id') != client_order_id:
            continue
        if status is not None and item.get('status') != status:
            continue
        return True
    return False


def append_order_lifecycle_event(payload: dict, base_dir: str | Path | None = None) -> Path:
    path = _order_lifecycle_log_path(base_dir)
    record = {
        'ts': utc_now_iso(),
        **payload,
    }
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path
