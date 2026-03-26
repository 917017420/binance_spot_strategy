from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
LIVE_SUBMIT_PLAN_FILE = DEFAULT_EXECUTION_DIR / 'live_submit_plans.jsonl'



def _live_submit_plan_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / LIVE_SUBMIT_PLAN_FILE.name



def append_live_submit_plan(payload: dict, base_dir: str | Path | None = None) -> Path:
    path = _live_submit_plan_path(base_dir)
    record = {
        'created_at': utc_now_iso(),
        **payload,
    }
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path
