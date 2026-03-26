from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import Position, PositionEvent, PositionState
from .utils import ensure_directory, seconds_since_iso, utc_now_iso


STRONG_EVENT_TYPES = {"TP1_HIT", "TP2_HIT", "TRAILING_EXIT", "STOP_EXIT", "RISK_OFF_EXIT", "POSITION_OPENED"}
ACTIVE_POSITION_STATUSES = {"open", "partially_reduced"}
SIMULATED_POSITION_TAGS = {"dry_run", "paper", "truth_domain_simulated"}
LIVE_POSITION_TAGS = {"live", "live_fill_reconciled", "truth_domain_live"}


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / "data" / "execution"
POSITIONS_FILE = DEFAULT_EXECUTION_DIR / "positions.json"
POSITION_EVENTS_FILE = DEFAULT_EXECUTION_DIR / "position_events.jsonl"
DEFAULT_ARCHIVE_DIR = DEFAULT_EXECUTION_DIR / 'archive'
ARCHIVED_POSITIONS_FILE = DEFAULT_ARCHIVE_DIR / 'positions_archive.jsonl'
DEFAULT_SIMULATED_POSITION_ARCHIVE_AGE_SECONDS = 24 * 60 * 60


@dataclass
class PositionArchiveCleanupResult:
    archived_count: int = 0
    kept_count: int = 0
    archive_path: str | None = None
    archived_position_ids: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _positions_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITIONS_FILE.name


def _position_events_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / POSITION_EVENTS_FILE.name


def _archive_dir(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    archive_dir = root / DEFAULT_ARCHIVE_DIR.name
    ensure_directory(archive_dir)
    return archive_dir


def _archived_positions_path(base_dir: str | Path | None = None) -> Path:
    return _archive_dir(base_dir) / ARCHIVED_POSITIONS_FILE.name


def load_positions(base_dir: str | Path | None = None) -> list[Position]:
    path = _positions_path(base_dir)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Position.model_validate(item) for item in data]


def classify_position_truth_domain(position: Position) -> str:
    tags = {
        str(tag).strip().lower()
        for tag in (position.tags or [])
        if str(tag).strip()
    }
    if tags & SIMULATED_POSITION_TAGS:
        return 'simulation'
    if (
        tags & LIVE_POSITION_TAGS
        or str(position.entry_execution_stage or '').lower() == 'live_fill_reconciled'
        or str(position.position_id or '').startswith('pos:live:')
    ):
        return 'live'
    return 'unknown'


def load_active_positions(base_dir: str | Path | None = None) -> list[Position]:
    return [
        position
        for position in load_positions(base_dir)
        if position.status in ACTIVE_POSITION_STATUSES
        and position.remaining_position_size_pct > 0
    ]


def load_live_active_positions(base_dir: str | Path | None = None) -> list[Position]:
    return [
        position
        for position in load_active_positions(base_dir)
        if classify_position_truth_domain(position) != 'simulation'
    ]


def load_monitor_positions(action_mode: str = 'dry_run', base_dir: str | Path | None = None) -> list[Position]:
    mode = str(action_mode or '').strip().lower()
    if mode == 'live':
        return load_live_active_positions(base_dir=base_dir)
    return load_active_positions(base_dir=base_dir)


def archive_stale_simulated_positions(
    *,
    base_dir: str | Path | None = None,
    older_than_seconds: float = DEFAULT_SIMULATED_POSITION_ARCHIVE_AGE_SECONDS,
) -> PositionArchiveCleanupResult:
    positions = load_positions(base_dir=base_dir)
    if not positions:
        return PositionArchiveCleanupResult(kept_count=0)

    kept: list[Position] = []
    archived: list[tuple[Position, float | None]] = []
    for position in positions:
        age_seconds = seconds_since_iso(position.entry_time)
        should_archive = (
            classify_position_truth_domain(position) == 'simulation'
            and position.status in ACTIVE_POSITION_STATUSES
            and position.remaining_position_size_pct > 0
            and age_seconds is not None
            and age_seconds >= older_than_seconds
        )
        if should_archive:
            archived.append((position, age_seconds))
            continue
        kept.append(position)

    result = PositionArchiveCleanupResult(
        archived_count=len(archived),
        kept_count=len(kept),
        archived_position_ids=[position.position_id for position, _ in archived],
    )
    if not archived:
        return result

    path = _archived_positions_path(base_dir=base_dir)
    with path.open('a', encoding='utf-8') as f:
        for position, age_seconds in archived:
            f.write(
                json.dumps(
                    {
                        'archived_at': utc_now_iso(),
                        'archive_reason': 'stale_simulated_active_position',
                        'truth_domain': classify_position_truth_domain(position),
                        'age_seconds': age_seconds,
                        'position': position.model_dump(mode='json'),
                    },
                    ensure_ascii=False,
                )
                + '\n'
            )
            result.messages.append(
                f'SIMULATED_POSITION_ARCHIVED position_id={position.position_id} symbol={position.symbol} age_seconds={age_seconds:.0f}'
            )

    save_positions(kept, base_dir=base_dir)
    result.archive_path = str(path)
    result.messages.append(
        f'SIMULATED_POSITION_ARCHIVE_SUMMARY archived={result.archived_count} kept={result.kept_count} path={path}'
    )
    return result


def save_positions(positions: list[Position], base_dir: str | Path | None = None) -> Path:
    path = _positions_path(base_dir)
    payload = [position.model_dump(mode="json") for position in positions]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upsert_position(position: Position, base_dir: str | Path | None = None) -> Path:
    positions = load_positions(base_dir)
    updated = False
    for idx, existing in enumerate(positions):
        if existing.position_id == position.position_id:
            positions[idx] = position
            updated = True
            break
    if not updated:
        positions.append(position)
    return save_positions(positions, base_dir)


def load_position_events(base_dir: str | Path | None = None) -> list[PositionEvent]:
    path = _position_events_path(base_dir)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [PositionEvent.model_validate(json.loads(line)) for line in lines]


def should_append_position_event(event: PositionEvent, base_dir: str | Path | None = None) -> bool:
    existing_events = load_position_events(base_dir)
    if not existing_events:
        return True
    for existing in reversed(existing_events[-20:]):
        if existing.position_id != event.position_id:
            continue
        if existing.event_type == event.event_type and event.event_type in STRONG_EVENT_TYPES:
            return False
        if existing.event_type == "POSITION_UPDATED" and event.event_type == "POSITION_UPDATED":
            if existing.details == event.details and existing.position_status == event.position_status and existing.suggested_action == event.suggested_action:
                return False
            return True
    return True


def append_position_event(event: PositionEvent, base_dir: str | Path | None = None) -> Path:
    path = _position_events_path(base_dir)
    if not should_append_position_event(event, base_dir=base_dir):
        return path
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return path


def build_position_event(position: Position, state: PositionState) -> PositionEvent:
    event_type = "POSITION_UPDATED"
    if state.suggested_action == "SELL_REDUCE" and position.tp1_hit and not position.tp2_hit:
        event_type = "TP1_HIT"
    elif state.suggested_action == "ENABLE_TRAILING_STOP" and position.tp2_hit:
        event_type = "TP2_HIT"
    elif state.suggested_action == "SELL_EXIT" and position.status == "stopped":
        event_type = "STOP_EXIT"
    elif state.suggested_action == "SELL_EXIT" and any("RISK_OFF" in reason for reason in state.reasons):
        event_type = "RISK_OFF_EXIT"
    elif state.suggested_action == "SELL_EXIT" and position.trailing_enabled and position.status == "closed":
        event_type = "TRAILING_EXIT"

    created_at = utc_now_iso()
    return PositionEvent(
        event_id=f"{position.position_id}:{created_at}:{event_type}",
        position_id=position.position_id,
        symbol=position.symbol,
        event_type=event_type,
        created_at=created_at,
        position_status=position.status,
        suggested_action=state.suggested_action,
        reasons=state.reasons,
        details={
            "last_price": position.last_price,
            "remaining_position_size_pct": position.remaining_position_size_pct,
            "active_stop_price": position.active_stop_price,
            "tp1_hit": position.tp1_hit,
            "tp2_hit": position.tp2_hit,
            "trailing_enabled": position.trailing_enabled,
        },
    )
