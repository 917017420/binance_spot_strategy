from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
EXECUTION_QUEUE_LOG_FILE = DEFAULT_EXECUTION_DIR / 'execution_queue_log.jsonl'


def _execution_queue_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / EXECUTION_QUEUE_LOG_FILE.name


def append_execution_queue_log(record: dict, base_dir: str | Path | None = None) -> Path:
    path = _execution_queue_log_path(base_dir)
    payload = {
        'logged_at': utc_now_iso(),
        **record,
    }
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return path


def log_queue_transition(queue_key: str, symbol: str, route: str, stage: str, base_dir: str | Path | None = None, **extra) -> Path:
    return append_execution_queue_log(
        {
            'queue_key': queue_key,
            'symbol': symbol,
            'route': route,
            'stage': stage,
            **extra,
        },
        base_dir=base_dir,
    )
