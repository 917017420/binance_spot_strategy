from __future__ import annotations

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


def resolve_exit_settings(exit_settings: ExitSettings | None = None) -> ExitSettings:
    return exit_settings or ExitSettings()
