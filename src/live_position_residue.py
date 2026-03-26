from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import Settings, load_settings
from .models import Position


_ACTIVE_POSITION_STATUSES = {'open', 'partially_reduced'}
_EFFECTIVELY_ZERO_EPSILON = 1e-12
_FULLY_OPEN_RATIO_EPSILON = 1e-9


@dataclass
class LivePositionResidueClassification:
    symbol: str
    position_id: str
    is_residue: bool
    residue_kind: str | None = None
    reason: str | None = None
    blocking: bool = True
    remaining_position_size_pct: float = 0.0
    estimated_remaining_base_amount: float = 0.0
    estimated_remaining_quote_amount: float = 0.0
    reference_price: float = 0.0
    tiny_live_quote_threshold: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce_positive_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if number > 0 else default


def _position_ratio(position: Position) -> float:
    initial_pct = _coerce_positive_float(position.initial_position_size_pct)
    remaining_pct = max(float(position.remaining_position_size_pct or 0.0), 0.0)
    if initial_pct <= 0:
        return 0.0
    return min(max(remaining_pct / initial_pct, 0.0), 1.0)


def _position_has_been_reduced(position: Position) -> bool:
    return _position_ratio(position) < (1.0 - _FULLY_OPEN_RATIO_EPSILON)


def estimate_remaining_base_amount(position: Position, *, remaining_base_amount: float | None = None) -> float:
    if remaining_base_amount is not None:
        return max(float(remaining_base_amount or 0.0), 0.0)
    entry_base_amount = _coerce_positive_float(position.entry_base_amount)
    if entry_base_amount <= 0:
        return 0.0
    return entry_base_amount * _position_ratio(position)


def estimate_remaining_quote_amount(
    position: Position,
    *,
    remaining_base_amount: float | None = None,
    reference_price: float | None = None,
) -> tuple[float, float]:
    resolved_reference_price = max(
        _coerce_positive_float(reference_price),
        _coerce_positive_float(position.last_price),
        _coerce_positive_float(position.entry_price),
    )
    estimated_remaining_base_amount = estimate_remaining_base_amount(
        position,
        remaining_base_amount=remaining_base_amount,
    )
    estimated_remaining_quote_amount = estimated_remaining_base_amount * resolved_reference_price
    if estimated_remaining_quote_amount > 0:
        return estimated_remaining_quote_amount, resolved_reference_price

    entry_quote_amount = _coerce_positive_float(position.entry_quote_amount)
    return entry_quote_amount * _position_ratio(position), resolved_reference_price


def classify_live_position_residue(
    position: Position,
    *,
    remaining_base_amount: float | None = None,
    reference_price: float | None = None,
    settings: Settings | None = None,
) -> LivePositionResidueClassification:
    remaining_position_size_pct = max(float(position.remaining_position_size_pct or 0.0), 0.0)
    estimated_remaining_base_amount = estimate_remaining_base_amount(
        position,
        remaining_base_amount=remaining_base_amount,
    )
    estimated_remaining_quote_amount, resolved_reference_price = estimate_remaining_quote_amount(
        position,
        remaining_base_amount=remaining_base_amount,
        reference_price=reference_price,
    )

    residue_kind = None
    reason = None

    if position.status in _ACTIVE_POSITION_STATUSES and remaining_position_size_pct > 0:
        if estimated_remaining_base_amount <= _EFFECTIVELY_ZERO_EPSILON:
            residue_kind = 'effectively_zero_remaining_base'
            reason = 'remaining base amount is effectively zero after live reconcile'
        else:
            settings = settings or load_settings()
            tiny_live_quote_threshold = max(float(settings.auto_entry.scan_tiny_live_notional_quote or 0.0), 0.0)
            if (
                _position_has_been_reduced(position)
                and 0 < estimated_remaining_quote_amount < tiny_live_quote_threshold
            ):
                residue_kind = 'dust_notional_below_tiny_live_threshold'
                reason = (
                    f'estimated remaining notional {estimated_remaining_quote_amount:.8f} '
                    f'is below tiny-live threshold {tiny_live_quote_threshold:.8f}'
                )
    else:
        tiny_live_quote_threshold = max(float((settings or load_settings()).auto_entry.scan_tiny_live_notional_quote or 0.0), 0.0)

    return LivePositionResidueClassification(
        symbol=position.symbol,
        position_id=position.position_id,
        is_residue=residue_kind is not None,
        residue_kind=residue_kind,
        reason=reason,
        blocking=residue_kind is None and position.status in _ACTIVE_POSITION_STATUSES and remaining_position_size_pct > 0,
        remaining_position_size_pct=remaining_position_size_pct,
        estimated_remaining_base_amount=estimated_remaining_base_amount,
        estimated_remaining_quote_amount=estimated_remaining_quote_amount,
        reference_price=resolved_reference_price,
        tiny_live_quote_threshold=tiny_live_quote_threshold,
    )


def partition_live_positions_for_control_plane(
    positions: list[Position],
    *,
    settings: Settings | None = None,
) -> tuple[list[Position], list[dict]]:
    blocking_positions: list[Position] = []
    residue_positions: list[dict] = []

    settings = settings or load_settings()
    for position in positions:
        classification = classify_live_position_residue(position, settings=settings)
        if classification.is_residue:
            residue_positions.append({
                'symbol': position.symbol,
                'position_id': position.position_id,
                'status': position.status,
                **classification.to_dict(),
            })
            continue
        blocking_positions.append(position)

    return blocking_positions, residue_positions


def summarize_position_residue(residue_positions: list[dict]) -> dict:
    kinds = sorted({item.get('residue_kind') for item in residue_positions if item.get('residue_kind')})
    return {
        'count': len(residue_positions),
        'symbols': sorted({str(item.get('symbol')) for item in residue_positions if item.get('symbol')}),
        'position_ids': [item.get('position_id') for item in residue_positions if item.get('position_id')],
        'residue_kinds': kinds,
        'estimated_remaining_quote_amount': round(
            sum(float(item.get('estimated_remaining_quote_amount') or 0.0) for item in residue_positions),
            12,
        ),
        'estimated_remaining_base_amount': round(
            sum(float(item.get('estimated_remaining_base_amount') or 0.0) for item in residue_positions),
            12,
        ),
        'blocking': False,
    }
