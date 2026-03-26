from __future__ import annotations

import json
from pathlib import Path

from .utils import ensure_directory


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
EXECUTION_QUEUE_STATE_FILE = DEFAULT_EXECUTION_DIR / 'execution_queue_state.json'


def _execution_queue_state_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / EXECUTION_QUEUE_STATE_FILE.name


def load_execution_queue_state(base_dir: str | Path | None = None) -> dict:
    path = _execution_queue_state_path(base_dir)
    if not path.exists():
        return {'processed_keys': []}
    return json.loads(path.read_text(encoding='utf-8'))


def save_execution_queue_state(state: dict, base_dir: str | Path | None = None) -> Path:
    path = _execution_queue_state_path(base_dir)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return path
