from __future__ import annotations

import json
from pathlib import Path

from .models import Position
from .position_lifecycle import build_position_lifecycle
from .positions_store import load_positions
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution'
POSITION_LIFECYCLE_FILE = DEFAULT_EXECUTION_DIR / 'position_lifecycle.jsonl'


def _position_lifecycle_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITION_LIFECYCLE_FILE.name


def _write_position_lifecycle_records(positions: list[Position], base_dir: str | Path | None = None) -> Path:
    path = _position_lifecycle_path(base_dir)
    with path.open('a', encoding='utf-8') as f:
        for position in positions:
            lifecycle = build_position_lifecycle(position)
            record = {
                'captured_at': utc_now_iso(),
                'position_id': lifecycle.position_id,
                'symbol': lifecycle.symbol,
                'lifecycle_stage': lifecycle.lifecycle_stage,
                'status': lifecycle.status,
                'exit_action': lifecycle.exit_action,
                'notes': lifecycle.notes,
            }
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path


def snapshot_position_lifecycles(base_dir: str | Path | None = None) -> Path:
    positions = load_positions(base_dir=base_dir)
    return _write_position_lifecycle_records(positions, base_dir=base_dir)


def snapshot_given_positions(positions: list[Position], base_dir: str | Path | None = None) -> Path:
    return _write_position_lifecycle_records(positions, base_dir=base_dir)
