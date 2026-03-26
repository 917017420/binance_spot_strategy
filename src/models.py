from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SignalType = Literal[
    "BUY_READY_BREAKOUT",
    "BUY_READY_PULLBACK",
    "NEAR_BREAKOUT",
    "PULLBACK_FORMING",
    "RELATIVE_STRENGTH_WATCH",
    "WATCH_ONLY",
]
RegimeType = Literal["risk_on", "neutral", "risk_off"]


class IndicatorSnapshot(BaseModel):
    close: float
    ema20: float
    ema50: float
    ema200: float
    atr14: float
    atr14_pct: float
    high20: float
    low20: float
    avg_volume20: float
    volume: float
    quote_volume_24h: float
    distance_to_ema20_pct: float
    change_24h_pct: float
    change_7d_pct: float
    upper_wick_pct: float
    body_pct: float


class ScoreBreakdown(BaseModel):
    trend_score: int
    liquidity_score: int
    strength_score: int
    breakout_score: int
    runway_score: int = 0
    runway_penalty: int = 0
    mtf_alignment_score: int = 0
    structure_quality_score: int = 0
    execution_quality_score: int = 0
    overextension_penalty: int
    regime_score: int
    total_score: int
    passed_candidate_gate: bool
    strong_candidate: bool
    reasons: list[str] = Field(default_factory=list)


class RiskPlan(BaseModel):
    invalidation_level: float | None = None
    atr_based_buffer: float | None = None
    notes: list[str] = Field(default_factory=list)


class PairAnalysis(BaseModel):
    symbol: str
    signal: SignalType
    secondary_signal: SignalType | None = None
    decision_action: str | None = None
    execution_stage: str | None = None
    attention_level: str | None = None
    decision_priority: int = 0
    market_state: str | None = None
    risk_budget: str | None = None
    position_size_pct: float = 0.0
    symbol_change_24h_pct: float = 0.0
    symbol_range_24h_pct: float = 0.0
    close_position_in_24h_range: float = 0.0
    pullback_from_24h_high_pct: float = 0.0
    vs_btc_24h_delta: float = 0.0
    day_context_label: str | None = None
    execution_min_notional_ok: bool = True
    execution_min_amount_ok: bool = True
    execution_estimated_base_amount: float = 0.0
    execution_estimated_quote_amount: float = 0.0
    execution_tiny_live_quote_amount: float = 0.0
    execution_tiny_live_base_amount: float = 0.0
    execution_tiny_live_min_amount_ok: bool = True
    execution_market_min_amount: float = 0.0
    execution_market_amount_step: float = 0.0
    execution_dust_risk: str | None = None
    score_delta: float = 0.0
    rank_delta: int = 0
    previous_rank: int | None = None
    previous_total_score: float | None = None
    decision_reasons: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    positive_reasons: list[str] = Field(default_factory=list)
    penalty_reasons: list[str] = Field(default_factory=list)
    runway_upside_pct: float = 0.0
    runway_resistance_price: float | None = None
    local_high_reference_price: float | None = None
    distance_to_local_high_pct: float = 0.0
    near_local_high: bool = False
    expected_upside_pct: float = 0.0
    expected_downside_pct: float = 0.0
    reward_risk_ratio: float = 0.0
    planned_initial_stop_price: float | None = None
    planned_tp1_price: float | None = None
    planned_tp2_price: float | None = None
    exit_plan_notes: list[str] = Field(default_factory=list)
    regime: RegimeType
    indicators_1h: IndicatorSnapshot
    indicators_4h: IndicatorSnapshot
    scores: ScoreBreakdown
    reasons: list[str] = Field(default_factory=list)
    risk: RiskPlan


class SkippedSymbol(BaseModel):
    symbol: str
    reason: str


class MarketRegimeReport(BaseModel):
    symbol: str
    regime: RegimeType
    score: int
    reasons: list[str] = Field(default_factory=list)
    indicators_1h: IndicatorSnapshot
    indicators_4h: IndicatorSnapshot


class ScanReport(BaseModel):
    generated_at: str
    scan_mode: str
    scanned_symbols: int
    eligible_symbols: int
    skipped_symbols: list[SkippedSymbol] = Field(default_factory=list)
    market_regime: MarketRegimeReport
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    candidates: list[PairAnalysis] = Field(default_factory=list)
    priority_candidates: list[PairAnalysis] = Field(default_factory=list)
    execution_ready_candidates: list[PairAnalysis] = Field(default_factory=list)
    watch_quality_candidates: list[PairAnalysis] = Field(default_factory=list)
    live_leader: PairAnalysis | None = None
    secondary_candidates: list[PairAnalysis] = Field(default_factory=list)
    auto_entry_candidates: list[PairAnalysis] = Field(default_factory=list)
    auto_entry_live_candidates: list[PairAnalysis] = Field(default_factory=list)
    auto_entry_paper_candidates: list[PairAnalysis] = Field(default_factory=list)
    auto_entry_shadow_candidates: list[PairAnalysis] = Field(default_factory=list)
    auto_entry_allow_count: int = 0
    auto_entry_deny_count: int = 0
    auto_entry_denials: list[str] = Field(default_factory=list)
    auto_entry_decisions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scan_deltas: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class PendingConfirmation(BaseModel):
    confirmation_id: str
    status: Literal["pending", "confirmed", "cancelled", "expired", "rejected_before_submit", "submit_failed"] = "pending"
    created_at: str
    expires_at: str
    symbol: str
    requested_position_size_pct: float
    trigger_price: float
    suggested_stop_price: float | None = None
    trigger_reason: str
    trigger_source: Literal["priority_list", "secondary_watchlist"] = "priority_list"
    decision_action: str
    execution_stage: str | None = None
    attention_level: str | None = None
    market_state: str | None = None
    risk_budget: str | None = None
    signal: str
    secondary_signal: str | None = None
    decision_priority: int = 0
    positive_reasons: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    penalty_reasons: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    confirmation_id: str
    mode: Literal["dry_run", "paper", "live"] = "dry_run"
    status: Literal["simulated", "paper_submitted", "submitted", "failed"]
    symbol: str
    side: Literal["buy"] = "buy"
    requested_position_size_pct: float
    reference_price: float
    estimated_quote_amount: float
    estimated_base_amount: float
    message: str
    created_at: str
    details: dict[str, Any] = Field(default_factory=dict)


class Position(BaseModel):
    position_id: str
    symbol: str
    status: Literal["open", "partially_reduced", "closed", "stopped", "cancelled"]

    entry_time: str
    entry_price: float
    entry_signal: str
    entry_secondary_signal: str | None = None
    entry_decision_action: str
    entry_execution_stage: str | None = None
    entry_attention_level: str | None = None

    initial_position_size_pct: float
    remaining_position_size_pct: float
    entry_quote_amount: float
    entry_base_amount: float

    initial_stop_price: float
    active_stop_price: float
    suggested_stop_price: float | None = None
    risk_budget: str | None = None
    market_state_at_entry: str | None = None

    tp1_price: float
    tp2_price: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp1_hit_time: str | None = None
    tp2_hit_time: str | None = None
    tp1_reduce_pct: float = 30.0
    tp2_reduce_pct: float = 30.0
    move_stop_to_breakeven_on_tp1: bool = True
    enable_trailing_on_tp2: bool = True
    risk_off_exit_enabled: bool = True

    trailing_enabled: bool = False
    trailing_drawdown_pct: float = 4.0
    highest_price_since_entry: float

    last_price: float
    unrealized_pnl_pct: float = 0.0
    realized_pnl_pct: float = 0.0

    cooldown_until: str | None = None
    notes: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PositionState(BaseModel):
    position_id: str
    symbol: str
    updated_at: str

    status: str
    last_price: float
    remaining_position_size_pct: float
    active_stop_price: float

    tp1_hit: bool
    tp2_hit: bool
    trailing_enabled: bool
    highest_price_since_entry: float

    suggested_action: Literal[
        "HOLD",
        "MOVE_STOP_TO_BREAKEVEN",
        "SELL_REDUCE",
        "SELL_EXIT",
        "ENABLE_TRAILING_STOP",
    ]
    reasons: list[str] = Field(default_factory=list)


class PositionEvent(BaseModel):
    event_id: str
    position_id: str
    symbol: str
    event_type: Literal[
        "POSITION_OPENED",
        "POSITION_UPDATED",
        "TP1_HIT",
        "TP2_HIT",
        "TRAILING_EXIT",
        "STOP_EXIT",
        "RISK_OFF_EXIT",
        "POSITION_ACTION_EXECUTED",
    ]
    created_at: str
    position_status: str
    suggested_action: str
    reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class PositionActionResult(BaseModel):
    action_id: str
    position_id: str
    symbol: str
    mode: Literal["dry_run", "paper", "live"] = "dry_run"
    action: Literal["SELL_REDUCE", "SELL_EXIT", "HOLD"]
    status: Literal["simulated", "submitted", "executed", "skipped", "failed"]
    executed_at: str
    requested_reduce_pct: float = 0.0
    resulting_position_status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


PendingConfirmation.model_rebuild()
ExecutionResult.model_rebuild()
Position.model_rebuild()
PositionState.model_rebuild()
PositionEvent.model_rebuild()
PositionActionResult.model_rebuild()
