from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import PairAnalysis, PendingConfirmation
from .utils import ensure_directory, utc_now_iso


DEFAULT_EXECUTION_DIR = Path(__file__).resolve().parent.parent / "data" / "execution"
PENDING_CONFIRMATIONS_FILE = DEFAULT_EXECUTION_DIR / "pending_confirmations.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _storage_path(base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else DEFAULT_EXECUTION_DIR
    ensure_directory(root)
    return root / PENDING_CONFIRMATIONS_FILE.name


def load_pending_confirmations(base_dir: str | Path | None = None) -> list[PendingConfirmation]:
    path = _storage_path(base_dir)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PendingConfirmation.model_validate(item) for item in data]


def save_pending_confirmations(confirmations: list[PendingConfirmation], base_dir: str | Path | None = None) -> Path:
    path = _storage_path(base_dir)
    path.write_text(
        json.dumps([item.model_dump(mode="json") for item in confirmations], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def build_pending_confirmation(
    analysis: PairAnalysis,
    requested_position_size_pct: float | None = None,
    ttl_minutes: int = 15,
    trigger_source: str = "priority_list",
) -> PendingConfirmation:
    now = _utc_now()
    expires_at = now + timedelta(minutes=ttl_minutes)
    position_size_pct = requested_position_size_pct if requested_position_size_pct is not None else max(analysis.position_size_pct, 5.0)
    return PendingConfirmation(
        confirmation_id=str(uuid.uuid4()),
        created_at=utc_now_iso(),
        expires_at=expires_at.isoformat(),
        symbol=analysis.symbol,
        requested_position_size_pct=position_size_pct,
        trigger_price=analysis.indicators_1h.close,
        suggested_stop_price=analysis.risk.invalidation_level,
        trigger_reason=(analysis.decision_reasons[0] if analysis.decision_reasons else "pending manual confirmation"),
        trigger_source=trigger_source,
        decision_action=analysis.decision_action or "NONE",
        execution_stage=analysis.execution_stage,
        attention_level=analysis.attention_level,
        market_state=analysis.market_state,
        risk_budget=analysis.risk_budget,
        signal=analysis.signal,
        secondary_signal=analysis.secondary_signal,
        decision_priority=analysis.decision_priority,
        positive_reasons=analysis.positive_reasons,
        blocking_reasons=analysis.blocking_reasons,
        penalty_reasons=analysis.penalty_reasons,
        meta={
            "position_size_pct_from_analysis": analysis.position_size_pct,
            "atr14_at_signal": analysis.indicators_1h.atr14,
            "structure_support_price": analysis.risk.invalidation_level,
            "runway_resistance_price": analysis.runway_resistance_price,
            "runway_upside_pct": analysis.runway_upside_pct,
            "reward_risk_ratio": analysis.reward_risk_ratio,
            "planned_initial_stop_price": analysis.planned_initial_stop_price,
            "planned_tp1_price": analysis.planned_tp1_price,
            "planned_tp2_price": analysis.planned_tp2_price,
            "exit_plan_notes": analysis.exit_plan_notes[:4],
        },
    )


def append_pending_confirmation(
    confirmation: PendingConfirmation,
    base_dir: str | Path | None = None,
) -> Path:
    confirmations = load_pending_confirmations(base_dir)
    confirmations.append(confirmation)
    return save_pending_confirmations(confirmations, base_dir)


def expire_stale_confirmations(base_dir: str | Path | None = None) -> tuple[int, Path]:
    now = _utc_now()
    confirmations = load_pending_confirmations(base_dir)
    changed = 0
    for item in confirmations:
        if item.status == "pending" and _parse_iso(item.expires_at) <= now:
            item.status = "expired"
            changed += 1
    path = save_pending_confirmations(confirmations, base_dir)
    return changed, path
