from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config/strategy.example.yaml"
DEFAULT_ENV_PATH = PACKAGE_ROOT / ".env"


class ExchangeSettings(BaseModel):
    name: str = "binance"
    timeout_ms: int = 15000


class UniverseSettings(BaseModel):
    quote_asset: str = "USDT"
    min_quote_volume: float = 10_000_000
    max_symbols: int = 50
    excluded_bases: list[str] = Field(
        default_factory=lambda: [
            "USDC",
            "FDUSD",
            "BUSD",
            "TUSD",
            "USDP",
            "DAI",
            "PAX",
            "USD1",
            "USDS",
            "USDT",
            "EUR",
            "EURS",
            "AEUR",
        ]
    )
    excluded_symbol_patterns: list[str] = Field(
        default_factory=lambda: ["UP", "DOWN", "BULL", "BEAR"]
    )


class DataSettings(BaseModel):
    primary_timeframe: str = "1h"
    context_timeframe: str = "4h"
    ohlcv_limit: int = 260


class StrategySettings(BaseModel):
    candidate_min_total_score: int = 60
    candidate_min_trend_score: int = 15
    candidate_min_liquidity_score: int = 10
    strong_total_score: int = 75
    breakout_max_ema20_distance_pct: float = 6.0
    pullback_reclaim_lookback: int = 5
    volume_strong_multiple: float = 3.0
    volume_healthy_multiple: float = 2.0
    runway_lookback_bars: int = 96
    runway_min_upside_pct: float = 2.5
    runway_full_score_upside_pct: float = 8.0
    runway_near_high_threshold_pct: float = 1.2
    runway_insufficient_penalty: int = 8
    buy_min_reward_risk_ratio: float = 1.6


class DayContextSettings(BaseModel):
    trending_bonus: int = 8
    overheated_penalty: int = 20
    weak_rebound_penalty: int = 15


class AutoEntrySettings(BaseModel):
    live_min_score: int = 85
    paper_min_score: int = 65
    shadow_min_score: int = 40
    shadow_soft_risk_points: int = 2
    max_open_positions: int = 3
    max_single_position_pct: float = 10.0
    max_total_exposure_pct: float = 30.0
    allow_watchlist_alert_to_paper: bool = True
    allow_manual_confirmation_to_paper: bool = True
    deny_when_market_risk_off: bool = True
    bucket_weight_overrides: dict[str, int] = Field(default_factory=dict)
    scan_reference_equity_quote: float = 1000.0
    scan_tiny_live_notional_quote: float = 6.0
    live_order_quote_amount: float = 6.0


class ExitSettings(BaseModel):
    initial_stop_loss_pct: float = 4.0
    tp1_profit_pct: float = 6.0
    tp2_profit_pct: float = 10.0
    initial_stop_atr_multiple: float = 1.25
    stop_structure_buffer_atr: float = 0.3
    tp1_atr_multiple: float = 1.5
    tp2_atr_multiple: float = 2.8
    tp1_runway_fraction: float = 0.5
    resistance_buffer_pct: float = 0.25
    tp1_reduce_pct: float = 30.0
    tp2_reduce_pct: float = 30.0
    trailing_drawdown_pct: float = 4.0
    move_stop_to_breakeven_on_tp1: bool = True
    enable_trailing_on_tp2: bool = True
    risk_off_exit_enabled: bool = True


class OutputSettings(BaseModel):
    directory: str = str(PACKAGE_ROOT / "data/output")


class ApiSettings(BaseModel):
    api_key: str | None = None
    api_secret: str | None = None
    enable_private: bool = False
    enable_order_submit: bool = False


class Settings(BaseModel):
    exchange: ExchangeSettings = Field(default_factory=ExchangeSettings)
    universe: UniverseSettings = Field(default_factory=UniverseSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    strategy: StrategySettings = Field(default_factory=StrategySettings)
    day_context: DayContextSettings = Field(default_factory=DayContextSettings)
    auto_entry: AutoEntrySettings = Field(default_factory=AutoEntrySettings)
    exit: ExitSettings = Field(default_factory=ExitSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    runtime_btc_indicators_1h: Any | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_config_path(config_path: str | None) -> Path:
    if not config_path:
        return DEFAULT_CONFIG_PATH
    candidate = Path(config_path)
    if candidate.exists() or candidate.is_absolute():
        return candidate
    package_relative = PACKAGE_ROOT / config_path
    return package_relative if package_relative.exists() else candidate


def _resolve_output_directory(directory: str) -> str:
    path = Path(directory)
    if path.is_absolute():
        return str(path)
    return str((PACKAGE_ROOT / path).resolve())


def _env_float(name: str, fallback: Any) -> float:
    raw = os.getenv(name)
    if raw in {None, ''}:
        return float(fallback)
    try:
        return float(raw)
    except ValueError:
        return float(fallback)


def _env_bool(name: str, fallback: Any) -> bool:
    raw = os.getenv(name)
    if raw in {None, ''}:
        return bool(fallback)
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def load_settings(config_path: str | None = None, env_path: str | None = None) -> Settings:
    env_file = Path(env_path) if env_path else DEFAULT_ENV_PATH
    if env_file.exists():
        load_dotenv(env_file)

    resolved_config_path = _resolve_config_path(config_path)
    file_data: dict[str, Any] = {}
    if resolved_config_path.exists():
        file_data = yaml.safe_load(resolved_config_path.read_text()) or {}

    default_data = Settings().model_dump()
    merged = _deep_merge(default_data, file_data)
    merged["output"]["directory"] = _resolve_output_directory(merged["output"]["directory"])
    merged.setdefault("auto_entry", {})
    merged["auto_entry"]["scan_reference_equity_quote"] = _env_float(
        "AUTO_ENTRY_SCAN_REFERENCE_EQUITY_QUOTE",
        merged["auto_entry"].get("scan_reference_equity_quote", 1000.0),
    )
    merged["auto_entry"]["scan_tiny_live_notional_quote"] = _env_float(
        "AUTO_ENTRY_SCAN_TINY_LIVE_NOTIONAL_QUOTE",
        merged["auto_entry"].get("scan_tiny_live_notional_quote", 6.0),
    )
    merged["auto_entry"]["live_order_quote_amount"] = _env_float(
        "AUTO_ENTRY_LIVE_ORDER_QUOTE_AMOUNT",
        merged["auto_entry"].get("live_order_quote_amount", 6.0),
    )
    merged.setdefault('exit', {})
    merged['exit']['initial_stop_loss_pct'] = _env_float(
        'POSITION_EXIT_INITIAL_STOP_LOSS_PCT',
        merged['exit'].get('initial_stop_loss_pct', 4.0),
    )
    merged['exit']['tp1_profit_pct'] = _env_float(
        'POSITION_EXIT_TP1_PROFIT_PCT',
        merged['exit'].get('tp1_profit_pct', 6.0),
    )
    merged['exit']['tp2_profit_pct'] = _env_float(
        'POSITION_EXIT_TP2_PROFIT_PCT',
        merged['exit'].get('tp2_profit_pct', 10.0),
    )
    merged['exit']['initial_stop_atr_multiple'] = _env_float(
        'POSITION_EXIT_INITIAL_STOP_ATR_MULTIPLE',
        merged['exit'].get('initial_stop_atr_multiple', 1.25),
    )
    merged['exit']['stop_structure_buffer_atr'] = _env_float(
        'POSITION_EXIT_STOP_STRUCTURE_BUFFER_ATR',
        merged['exit'].get('stop_structure_buffer_atr', 0.3),
    )
    merged['exit']['tp1_atr_multiple'] = _env_float(
        'POSITION_EXIT_TP1_ATR_MULTIPLE',
        merged['exit'].get('tp1_atr_multiple', 1.5),
    )
    merged['exit']['tp2_atr_multiple'] = _env_float(
        'POSITION_EXIT_TP2_ATR_MULTIPLE',
        merged['exit'].get('tp2_atr_multiple', 2.8),
    )
    merged['exit']['tp1_runway_fraction'] = _env_float(
        'POSITION_EXIT_TP1_RUNWAY_FRACTION',
        merged['exit'].get('tp1_runway_fraction', 0.5),
    )
    merged['exit']['resistance_buffer_pct'] = _env_float(
        'POSITION_EXIT_RESISTANCE_BUFFER_PCT',
        merged['exit'].get('resistance_buffer_pct', 0.25),
    )
    merged['exit']['tp1_reduce_pct'] = _env_float(
        'POSITION_EXIT_TP1_REDUCE_PCT',
        merged['exit'].get('tp1_reduce_pct', 30.0),
    )
    merged['exit']['tp2_reduce_pct'] = _env_float(
        'POSITION_EXIT_TP2_REDUCE_PCT',
        merged['exit'].get('tp2_reduce_pct', 30.0),
    )
    merged['exit']['trailing_drawdown_pct'] = _env_float(
        'POSITION_EXIT_TRAILING_DRAWDOWN_PCT',
        merged['exit'].get('trailing_drawdown_pct', 4.0),
    )
    merged['exit']['move_stop_to_breakeven_on_tp1'] = _env_bool(
        'POSITION_EXIT_MOVE_STOP_TO_BREAKEVEN_ON_TP1',
        merged['exit'].get('move_stop_to_breakeven_on_tp1', True),
    )
    merged['exit']['enable_trailing_on_tp2'] = _env_bool(
        'POSITION_EXIT_ENABLE_TRAILING_ON_TP2',
        merged['exit'].get('enable_trailing_on_tp2', True),
    )
    merged['exit']['risk_off_exit_enabled'] = _env_bool(
        'POSITION_EXIT_RISK_OFF_EXIT_ENABLED',
        merged['exit'].get('risk_off_exit_enabled', True),
    )
    merged["api"] = {
        "api_key": os.getenv("BINANCE_API_KEY") or merged.get("api", {}).get("api_key"),
        "api_secret": os.getenv("BINANCE_API_SECRET") or merged.get("api", {}).get("api_secret"),
        "enable_private": str(
            os.getenv("BINANCE_ENABLE_PRIVATE")
            or merged.get("api", {}).get("enable_private", False)
        ).lower()
        in {"1", "true", "yes", "on"},
        "enable_order_submit": str(
            os.getenv("BINANCE_ENABLE_ORDER_SUBMIT")
            or merged.get("api", {}).get("enable_order_submit", False)
        ).lower()
        in {"1", "true", "yes", "on"},
    }
    return Settings.model_validate(merged)
