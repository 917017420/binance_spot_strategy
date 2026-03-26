from __future__ import annotations

from dataclasses import dataclass, field

from .config import ExitSettings


def _clamp_non_negative(value: float) -> float:
    return max(float(value or 0.0), 0.0)


def build_initial_stop_price(entry_price: float, stop_loss_pct: float) -> float:
    if entry_price <= 0:
        return 0.0
    return max(entry_price * (1.0 - (_clamp_non_negative(stop_loss_pct) / 100.0)), 0.0)


def build_take_profit_price(entry_price: float, profit_pct: float) -> float:
    if entry_price <= 0:
        return 0.0
    return entry_price * (1.0 + (_clamp_non_negative(profit_pct) / 100.0))


@dataclass
class ExitPlan:
    initial_stop_price: float
    tp1_price: float
    tp2_price: float
    expected_upside_pct: float
    expected_downside_pct: float
    reward_risk_ratio: float
    notes: list[str] = field(default_factory=list)


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _pct_move(from_price: float, to_price: float) -> float:
    if from_price <= 0:
        return 0.0
    return ((to_price / from_price) - 1.0) * 100.0


def plan_entry_exit_levels(
    entry_price: float,
    *,
    exit_settings: ExitSettings | None = None,
    suggested_stop_price: float | None = None,
    atr14: float | None = None,
    structure_support_price: float | None = None,
    local_resistance_price: float | None = None,
) -> ExitPlan:
    resolved_exit = resolve_exit_settings(exit_settings)
    notes: list[str] = []
    if entry_price <= 0:
        return ExitPlan(
            initial_stop_price=0.0,
            tp1_price=0.0,
            tp2_price=0.0,
            expected_upside_pct=0.0,
            expected_downside_pct=0.0,
            reward_risk_ratio=0.0,
            notes=["Entry price is invalid; adaptive exit planning returned zeroed levels."],
        )

    atr_value = max(_safe_float(atr14) or 0.0, 0.0)
    base_stop = build_initial_stop_price(entry_price, resolved_exit.initial_stop_loss_pct)
    stop_candidates: list[tuple[str, float]] = [("fixed_pct", base_stop)]

    requested_stop = _safe_float(suggested_stop_price)
    if requested_stop is not None and 0 < requested_stop < entry_price:
        stop_candidates.append(("suggested_structure", requested_stop))
    support_value = _safe_float(structure_support_price)
    if support_value is not None and 0 < support_value < entry_price:
        structure_stop = support_value - (atr_value * max(resolved_exit.stop_structure_buffer_atr, 0.0))
        if 0 < structure_stop < entry_price:
            stop_candidates.append(("support_buffered", structure_stop))
    if atr_value > 0:
        atr_stop = entry_price - (atr_value * max(resolved_exit.initial_stop_atr_multiple, 0.0))
        if 0 < atr_stop < entry_price:
            stop_candidates.append(("atr_multiple", atr_stop))

    initial_stop_name, initial_stop_price = max(stop_candidates, key=lambda item: item[1])
    notes.append(f"Initial stop uses {initial_stop_name} reference.")

    base_tp1 = build_take_profit_price(entry_price, resolved_exit.tp1_profit_pct)
    base_tp2 = build_take_profit_price(entry_price, resolved_exit.tp2_profit_pct)
    tp2_candidates: list[tuple[str, float]] = [("fixed_pct_tp2", base_tp2)]
    if atr_value > 0:
        tp2_candidates.append(("atr_tp2", entry_price + (atr_value * max(resolved_exit.tp2_atr_multiple, 0.0))))
    resistance_value = _safe_float(local_resistance_price)
    if resistance_value is not None and resistance_value > entry_price:
        resistance_buffer = max(resolved_exit.resistance_buffer_pct, 0.0) / 100.0
        buffered_resistance = resistance_value * (1.0 - resistance_buffer)
        if buffered_resistance > entry_price:
            tp2_candidates.append(("resistance_buffered", buffered_resistance))

    tp2_name, tp2_price = min(tp2_candidates, key=lambda item: item[1])
    notes.append(f"TP2 uses {tp2_name} reference.")

    tp1_candidates: list[tuple[str, float]] = [("fixed_pct_tp1", base_tp1)]
    if atr_value > 0:
        tp1_candidates.append(("atr_tp1", entry_price + (atr_value * max(resolved_exit.tp1_atr_multiple, 0.0))))
    runway_fraction_target = entry_price + ((tp2_price - entry_price) * max(min(resolved_exit.tp1_runway_fraction, 0.9), 0.2))
    if runway_fraction_target > entry_price:
        tp1_candidates.append(("runway_fraction", runway_fraction_target))

    tp1_name, tp1_price = min(tp1_candidates, key=lambda item: item[1])
    tp1_price = min(tp1_price, tp2_price * 0.995)
    tp1_price = max(tp1_price, entry_price * 1.001)
    tp2_price = max(tp2_price, tp1_price * 1.005)
    notes.append(f"TP1 uses {tp1_name} reference.")

    expected_upside_pct = max(_pct_move(entry_price, tp2_price), 0.0)
    expected_downside_pct = max(((entry_price - initial_stop_price) / max(entry_price, 1e-9)) * 100.0, 0.0)
    reward_risk_ratio = (expected_upside_pct / expected_downside_pct) if expected_downside_pct > 0 else 0.0

    return ExitPlan(
        initial_stop_price=float(initial_stop_price),
        tp1_price=float(tp1_price),
        tp2_price=float(tp2_price),
        expected_upside_pct=float(expected_upside_pct),
        expected_downside_pct=float(expected_downside_pct),
        reward_risk_ratio=float(reward_risk_ratio),
        notes=notes,
    )


def resolve_exit_settings(exit_settings: ExitSettings | None = None) -> ExitSettings:
    if exit_settings is None:
        return ExitSettings()
    if isinstance(exit_settings, ExitSettings):
        return exit_settings

    default_payload = ExitSettings().model_dump()
    if isinstance(exit_settings, dict):
        override_payload = exit_settings
    elif hasattr(exit_settings, "model_dump"):
        override_payload = exit_settings.model_dump()
    elif hasattr(exit_settings, "__dict__"):
        override_payload = dict(vars(exit_settings))
    else:
        override_payload = {}
    merged = {**default_payload, **override_payload}
    return ExitSettings.model_validate(merged)
