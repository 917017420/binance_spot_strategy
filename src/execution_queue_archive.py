from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .execution_candidate_queue import _execution_candidate_queue_path
from .execution_queue_log import _execution_queue_log_path
from .utils import ensure_directory, utc_now_iso


DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / 'data' / 'execution' / 'archive'


@dataclass
class ExecutionQueueArchiveResult:
    archived_files: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)



def _archive_target(name: str) -> Path:
    ensure_directory(DEFAULT_ARCHIVE_DIR)
    timestamp = utc_now_iso().replace(':', '-').replace('+00:00', 'Z')
    return DEFAULT_ARCHIVE_DIR / f'{timestamp}-{name}'



def archive_execution_artifacts(base_dir: str | Path | None = None) -> ExecutionQueueArchiveResult:
    result = ExecutionQueueArchiveResult()
    for src in [_execution_candidate_queue_path(base_dir), _execution_queue_log_path(base_dir)]:
        if not src.exists() or src.stat().st_size == 0:
            result.messages.append(f'ARCHIVE_SKIP path={src}')
            continue
        dst = _archive_target(src.name)
        src.replace(dst)
        src.write_text('', encoding='utf-8')
        result.archived_files.append(str(dst))
        result.messages.append(f'ARCHIVE_OK src={src} dst={dst}')
    return result
