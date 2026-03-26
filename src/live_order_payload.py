from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .models import PairAnalysis, Position, PositionState


_BINANCE_CLIENT_ORDER_ID_MAX_LENGTH = 36
_BINANCE_CLIENT_ORDER_ID_HASH_LENGTH = 10
_CLIENT_ORDER_ID_TOKEN_RE = re.compile(r'[^A-Za-z0-9_-]+')


def _normalize_client_order_id_token(value: object) -> str:
    token = _CLIENT_ORDER_ID_TOKEN_RE.sub('-', str(value or '').strip())
    token = token.strip('-_')
    return token or 'x'


def _build_binance_client_order_id(*parts: object, compact_parts: tuple[object, ...] | None = None) -> str:
    normalized_parts = [_normalize_client_order_id_token(part) for part in parts if str(part or '').strip()]
    candidate = '-'.join(normalized_parts) or 'order'
    if len(candidate) <= _BINANCE_CLIENT_ORDER_ID_MAX_LENGTH:
        return candidate

    compact_source = compact_parts if compact_parts is not None else parts
    compact_tokens = [_normalize_client_order_id_token(part) for part in compact_source if str(part or '').strip()]
    compact_candidate = '-'.join(compact_tokens) or 'order'
    digest = hashlib.sha1(candidate.encode('utf-8')).hexdigest()[:_BINANCE_CLIENT_ORDER_ID_HASH_LENGTH]
    prefix_budget = _BINANCE_CLIENT_ORDER_ID_MAX_LENGTH - len(digest) - 1
    prefix = compact_candidate[:prefix_budget].rstrip('-_') or 'order'
    return f'{prefix}-{digest}'


def _derive_position_lifecycle_trigger(position: Position, state: PositionState) -> tuple[str, str, bool, bool]:
    reasons = {str(reason).strip() for reason in (state.reasons or [])}

    if state.suggested_action == 'SELL_EXIT':
        if any('Current price fell below active stop' in reason for reason in reasons):
            return 'stop_exit', 'stopped', False, False
        if any('Market state turned RISK_OFF' in reason for reason in reasons):
            return 'risk_off_exit', 'closed', False, False
        if any('Trailing stop triggered' in reason for reason in reasons):
            return 'trailing_exit', 'closed', False, False
        return 'sell_exit', 'closed', False, False

    if state.suggested_action == 'ENABLE_TRAILING_STOP' or (position.tp1_hit and not position.tp2_hit):
        return 'tp2_reduce', 'partially_reduced', True, True

    return 'tp1_reduce', 'partially_reduced', True, False


@dataclass
class LiveOrderPayload:
    symbol: str
    side: str
    order_type: str
    quote_amount: float
    base_amount: float = 0.0
    reference_price: float = 0.0
    requested_position_size_pct: float = 0.0
    client_order_id: str = ''
    metadata: dict = field(default_factory=dict)



def build_live_order_payload(
    candidate: PairAnalysis,
    total_equity_quote: float | None = None,
    *,
    quote_amount: float | None = None,
) -> LiveOrderPayload:
    resolved_total_equity_quote = max(float(total_equity_quote or 0.0), 0.0)
    resolved_quote_amount = quote_amount
    if resolved_quote_amount is None:
        resolved_quote_amount = resolved_total_equity_quote * (candidate.position_size_pct / 100.0)
    resolved_quote_amount = max(float(resolved_quote_amount or 0.0), 0.0)
    client_order_id = _build_binance_client_order_id(
        'live',
        candidate.symbol,
        candidate.decision_action or 'BUY',
        int(candidate.decision_priority),
    )
    return LiveOrderPayload(
        symbol=candidate.symbol,
        side='buy',
        order_type='market',
        quote_amount=resolved_quote_amount,
        base_amount=0.0,
        reference_price=candidate.indicators_1h.close,
        requested_position_size_pct=candidate.position_size_pct,
        client_order_id=client_order_id,
        metadata={
            'decision_action': candidate.decision_action,
            'execution_stage': candidate.execution_stage,
            'attention_level': candidate.attention_level,
            'day_context_label': candidate.day_context_label,
            'action_intent': candidate.decision_action,
            'requested_position_size_pct': candidate.position_size_pct,
            'quote_amount_source': 'explicit_quote_amount' if quote_amount is not None else 'position_pct_of_total_equity',
            'resolved_quote_amount': resolved_quote_amount,
            'resolved_total_equity_quote': resolved_total_equity_quote,
        },
    )



def build_position_live_order_payload(position: Position, state: PositionState, requested_reduce_pct: float) -> LiveOrderPayload:
    action = state.suggested_action
    initial_position_size_pct = max(float(position.initial_position_size_pct or 0.0), 0.0)
    remaining_position_size_pct = max(float(position.remaining_position_size_pct or 0.0), 0.0)
    entry_base_amount = max(float(position.entry_base_amount or 0.0), 0.0)
    last_price = max(float(position.last_price or 0.0), 0.0)
    lifecycle_trigger, expected_position_status_after_fill, move_stop_to_breakeven_after_fill, enable_trailing_after_fill = _derive_position_lifecycle_trigger(position, state)
    remaining_ratio = (remaining_position_size_pct / initial_position_size_pct) if initial_position_size_pct > 0 else 1.0

    if action == 'SELL_EXIT':
        base_amount = max(entry_base_amount * remaining_ratio, 0.0)
        action_intent = 'SELL_EXIT'
        sell_target_basis = 'remaining_position_ratio_of_entry_base'
    else:
        reduce_ratio = max(min(float(requested_reduce_pct) / 100.0, 1.0), 0.0)
        base_amount = max(entry_base_amount * reduce_ratio, 0.0)
        action_intent = 'SELL_REDUCE'
        sell_target_basis = 'requested_reduce_ratio_of_entry_base'

    quote_amount = base_amount * last_price if last_price > 0 else 0.0
    action_code = 'SX' if action_intent == 'SELL_EXIT' else 'SR'
    client_order_id = _build_binance_client_order_id(
        'live',
        position.symbol,
        action_intent,
        position.position_id,
        compact_parts=('live', position.symbol, action_code),
    )

    return LiveOrderPayload(
        symbol=position.symbol,
        side='sell',
        order_type='market',
        quote_amount=quote_amount,
        base_amount=base_amount,
        reference_price=last_price,
        requested_position_size_pct=remaining_position_size_pct,
        client_order_id=client_order_id,
        metadata={
            'position_id': position.position_id,
            'position_status': position.status,
            'suggested_action': action,
            'action_intent': action_intent,
            'lifecycle_trigger': lifecycle_trigger,
            'expected_position_status_after_fill': expected_position_status_after_fill,
            'move_stop_to_breakeven_after_fill': move_stop_to_breakeven_after_fill,
            'enable_trailing_after_fill': enable_trailing_after_fill,
            'requested_reduce_pct': requested_reduce_pct,
            'requested_position_size_pct': remaining_position_size_pct,
            'remaining_position_size_pct': remaining_position_size_pct,
            'position_entry_base_amount': entry_base_amount,
            'position_initial_position_size_pct': initial_position_size_pct,
            'position_remaining_ratio': remaining_ratio,
            'position_target_base_amount': base_amount,
            'sell_target_basis': sell_target_basis,
        },
    )
