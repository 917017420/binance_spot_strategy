from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
ACTIVE_TRADE_RELEASE_LOG_FILE = DEFAULT_EXECUTION_DIR / 'active_trade_releases.jsonl'



def _active_trade_release_log_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / ACTIVE_TRADE_RELEASE_LOG_FILE.name



def append_active_trade_release(payload: dict, base_dir: str | Path | None = None) -> Path:
    path = _active_trade_release_log_path(base_dir=base_dir)
    record = {
        'released_at': utc_now_iso(),
        **payload,
    }
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path
