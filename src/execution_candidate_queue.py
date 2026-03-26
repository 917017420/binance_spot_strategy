from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
EXECUTION_CANDIDATE_QUEUE_FILE = DEFAULT_EXECUTION_DIR / 'execution_candidate_queue.jsonl'


def _execution_candidate_queue_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / EXECUTION_CANDIDATE_QUEUE_FILE.name


def append_execution_candidate(record: dict, base_dir: str | Path | None = None) -> Path:
    path = _execution_candidate_queue_path(base_dir)
    payload = {
        'queued_at': utc_now_iso(),
        **record,
    }
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return path
