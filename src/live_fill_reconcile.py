from __future__ import annotations

from dataclasses import dataclass, field

from .active_trade_release_log import append_active_trade_release
from .config import load_settings
from .control_plane_reconcile import reconcile_control_plane_state
from .live_inflight_state import build_live_logical_key, load_live_inflight_state, save_live_inflight_state
from .live_position_residue import classify_live_position_residue
from .live_submit_state import load_live_submit_state, save_live_submit_state
from .models import Position
from .position_exit_policy import plan_entry_exit_levels
from .positions_store import classify_position_truth_domain, load_positions, save_positions
from .utils import utc_now_iso


@dataclass
class LiveOrderFact:
    exchange_order_id: str | None
    client_order_id: str | None
    symbol: str
    side: str
    status: str
    average_price: float | None
    filled_base_amount: float
    filled_quote_amount: float
    remaining_base_amount: float | None
    position_id: str | None = None
    action_intent: str | None = None
    requested_position_size_pct: float | None = None
    lifecycle_trigger: str | None = None
    expected_position_status_after_fill: str | None = None
    move_stop_to_breakeven_after_fill: bool = False
    enable_trailing_after_fill: bool = False
    requested_reduce_pct: float | None = None
    atr14_at_signal: float | None = None
    structure_support_price: float | None = None
    runway_resistance_price: float | None = None
    planned_initial_stop_price: float | None = None
    planned_tp1_price: float | None = None
    planned_tp2_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class LiveFillReconcileResult:
    ok: bool
    actions: list[str] = field(default_factory=list)
    submit_state: dict = field(default_factory=dict)
    inflight_state: dict = field(default_factory=dict)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _coerce_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _resolve_delta_filled_base_amount(order_snapshot: dict | None, fact: LiveOrderFact) -> float:
    previous_filled_base_amount = _coerce_float((order_snapshot or {}).get('filled_base_amount')) or 0.0
    return max(fact.filled_base_amount - previous_filled_base_amount, 0.0)


def _resolve_remaining_position_size_pct(position: Position, delta_filled_base_amount: float) -> float:
    entry_base_amount = max(float(position.entry_base_amount or 0.0), 0.0)
    initial_position_size_pct = max(float(position.initial_position_size_pct or 0.0), 0.0)
    current_remaining_pct = max(float(position.remaining_position_size_pct or 0.0), 0.0)

    if entry_base_amount <= 0:
        return current_remaining_pct

    reduced_position_size_pct = initial_position_size_pct * max(min(delta_filled_base_amount / entry_base_amount, 1.0), 0.0)
    return max(current_remaining_pct - reduced_position_size_pct, 0.0)


def _apply_reduce_fill_lifecycle(position: Position, fact: LiveOrderFact) -> None:
    now = utc_now_iso()
    trigger = str(fact.lifecycle_trigger or '').strip().lower()

    if trigger == 'tp2_reduce':
        if not position.tp1_hit:
            position.tp1_hit = True
            position.tp1_hit_time = position.tp1_hit_time or now
        if not position.tp2_hit:
            position.tp2_hit = True
            position.tp2_hit_time = now
        if fact.enable_trailing_after_fill:
            position.trailing_enabled = True
    elif trigger == 'tp1_reduce':
        if not position.tp1_hit:
            position.tp1_hit = True
            position.tp1_hit_time = now

    if fact.move_stop_to_breakeven_after_fill:
        position.active_stop_price = max(position.active_stop_price, position.entry_price)



def build_live_order_fact(response, request) -> LiveOrderFact:
    raw = getattr(response, 'raw', {}) or {}
    status = getattr(response, 'status', None) or raw.get('status') or 'submitted'
    filled_base = float(getattr(response, 'filled_base_amount', 0.0) or 0.0)
    filled_quote = float(getattr(response, 'filled_quote_amount', 0.0) or 0.0)
    average_price = getattr(response, 'average_fill_price', None)
    if average_price is None:
        average_price = raw.get('average')
    try:
        average_price = float(average_price) if average_price is not None else None
    except Exception:
        average_price = None
    remaining_base = getattr(response, 'remaining_base_amount', None)
    if remaining_base is None:
        remaining_base = raw.get('remaining')
    try:
        remaining_base = float(remaining_base) if remaining_base is not None else None
    except Exception:
        remaining_base = None
    request_metadata = getattr(request, 'metadata', {}) or {}
    requested_reduce_pct = _coerce_float(request_metadata.get('requested_reduce_pct'))
    requested_position_size_pct = request_metadata.get('requested_position_size_pct')
    try:
        requested_position_size_pct = float(requested_position_size_pct) if requested_position_size_pct is not None else None
    except Exception:
        requested_position_size_pct = None
    return LiveOrderFact(
        exchange_order_id=getattr(response, 'exchange_order_id', None),
        client_order_id=getattr(response, 'client_order_id', None) or request.client_order_id,
        symbol=request.symbol,
        side=request.side,
        status=str(status).lower(),
        average_price=average_price,
        filled_base_amount=filled_base,
        filled_quote_amount=filled_quote,
        remaining_base_amount=remaining_base,
        position_id=request_metadata.get('position_id'),
        action_intent=request_metadata.get('action_intent'),
        requested_position_size_pct=requested_position_size_pct,
        lifecycle_trigger=request_metadata.get('lifecycle_trigger'),
        expected_position_status_after_fill=request_metadata.get('expected_position_status_after_fill'),
        move_stop_to_breakeven_after_fill=_coerce_bool(request_metadata.get('move_stop_to_breakeven_after_fill')),
        enable_trailing_after_fill=_coerce_bool(request_metadata.get('enable_trailing_after_fill')),
        requested_reduce_pct=requested_reduce_pct,
        atr14_at_signal=_coerce_float(request_metadata.get('atr14_at_signal')),
        structure_support_price=_coerce_float(request_metadata.get('structure_support_price')),
        runway_resistance_price=_coerce_float(request_metadata.get('runway_resistance_price')),
        planned_initial_stop_price=_coerce_float(request_metadata.get('planned_initial_stop_price')),
        planned_tp1_price=_coerce_float(request_metadata.get('planned_tp1_price')),
        planned_tp2_price=_coerce_float(request_metadata.get('planned_tp2_price')),
        raw=raw,
    )



def _find_or_create_position(positions: list[Position], fact: LiveOrderFact) -> Position:
    for position in positions:
        if (
            position.symbol == fact.symbol
            and position.status in {'open', 'partially_reduced'}
            and classify_position_truth_domain(position) != 'simulation'
        ):
            return position
    exit_settings = load_settings().exit
    entry_price = fact.average_price or (fact.filled_quote_amount / fact.filled_base_amount if fact.filled_base_amount > 0 else 0.0)
    base_size_pct = fact.requested_position_size_pct if fact.requested_position_size_pct and fact.requested_position_size_pct > 0 else 100.0
    exit_plan = plan_entry_exit_levels(
        entry_price,
        exit_settings=exit_settings,
        suggested_stop_price=fact.structure_support_price,
        atr14=fact.atr14_at_signal,
        structure_support_price=fact.structure_support_price,
        local_resistance_price=fact.runway_resistance_price,
    )
    initial_stop_price = fact.planned_initial_stop_price if fact.planned_initial_stop_price and fact.planned_initial_stop_price > 0 else exit_plan.initial_stop_price
    tp1_price = fact.planned_tp1_price if fact.planned_tp1_price and fact.planned_tp1_price > 0 else exit_plan.tp1_price
    tp2_price = fact.planned_tp2_price if fact.planned_tp2_price and fact.planned_tp2_price > 0 else exit_plan.tp2_price
    return Position(
        position_id=f"pos:live:{fact.client_order_id or utc_now_iso()}",
        symbol=fact.symbol,
        status='open',
        entry_time=utc_now_iso(),
        entry_price=entry_price,
        entry_signal='LIVE_SUBMIT_FILLED',
        entry_secondary_signal=None,
        entry_decision_action='BUY_APPROVED',
        entry_execution_stage='live_fill_reconciled',
        entry_attention_level='high',
        initial_position_size_pct=base_size_pct,
        remaining_position_size_pct=base_size_pct,
        entry_quote_amount=fact.filled_quote_amount,
        entry_base_amount=fact.filled_base_amount,
        initial_stop_price=initial_stop_price,
        active_stop_price=initial_stop_price,
        suggested_stop_price=None,
        risk_budget='normal',
        market_state_at_entry='LIVE_RECONCILED',
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        tp1_reduce_pct=exit_settings.tp1_reduce_pct,
        tp2_reduce_pct=exit_settings.tp2_reduce_pct,
        move_stop_to_breakeven_on_tp1=exit_settings.move_stop_to_breakeven_on_tp1,
        enable_trailing_on_tp2=exit_settings.enable_trailing_on_tp2,
        risk_off_exit_enabled=exit_settings.risk_off_exit_enabled,
        trailing_drawdown_pct=exit_settings.trailing_drawdown_pct,
        highest_price_since_entry=entry_price,
        last_price=entry_price,
        notes=['created by live_fill_reconcile', *[f"exit_plan: {note}" for note in exit_plan.notes[:2]]],
        tags=['live_fill_reconciled', 'truth_domain_live'],
    )



def apply_live_order_fact(response, request, *, base_dir=None) -> LiveFillReconcileResult:
    fact = build_live_order_fact(response, request)
    actions: list[str] = []
    post_exit_reconcile_required = False

    submit_state = load_live_submit_state(base_dir=base_dir)
    existing_request = submit_state.get('last_request') or {}
    existing_metadata = existing_request.get('metadata') or {}
    next_metadata = {
        **existing_metadata,
    }
    if fact.action_intent:
        next_metadata['action_intent'] = fact.action_intent
    if fact.requested_position_size_pct is not None:
        next_metadata['requested_position_size_pct'] = fact.requested_position_size_pct
    submit_state['last_client_order_id'] = fact.client_order_id
    submit_state['last_submit_status'] = fact.status
    submit_state['last_submit_side'] = fact.side
    submit_state['last_symbol'] = fact.symbol
    submit_state['last_request'] = {
        **existing_request,
        'symbol': fact.symbol,
        'side': fact.side,
        'client_order_id': fact.client_order_id,
        'metadata': next_metadata,
    }
    submit_state['last_response'] = fact.raw or {'status': fact.status}
    submit_state['last_action_intent'] = fact.action_intent
    submit_state['last_error'] = None
    save_live_submit_state(submit_state, base_dir=base_dir)
    actions.append(f'SUBMIT_STATE_UPDATED status={fact.status} symbol={fact.symbol}')

    inflight_state = load_live_inflight_state(base_dir=base_dir)
    logical_key = build_live_logical_key(fact.symbol, 'live', 'armed')
    orders = inflight_state.get('orders') or {}
    prior_order_snapshot = dict(orders.get(logical_key) or {})

    terminal_statuses = {'filled', 'closed', 'canceled', 'cancelled', 'rejected'}
    partial_statuses = {'partially_filled', 'partial', 'open'}

    if fact.status in terminal_statuses:
        orders.pop(logical_key, None)
        actions.append(f'INFLIGHT_CLEARED logical_key={logical_key} status={fact.status}')
    else:
        orders[logical_key] = {
            'status': fact.status,
            'side': fact.side,
            'position_id': fact.position_id,
            'action_intent': fact.action_intent,
            'lifecycle_trigger': fact.lifecycle_trigger,
            'client_order_id': fact.client_order_id,
            'exchange_order_id': fact.exchange_order_id,
            'updated_at': utc_now_iso(),
            'filled_base_amount': fact.filled_base_amount,
            'filled_quote_amount': fact.filled_quote_amount,
            'remaining_base_amount': fact.remaining_base_amount,
        }
        actions.append(f'INFLIGHT_UPDATED logical_key={logical_key} status={fact.status}')

    inflight_state['orders'] = orders
    save_live_inflight_state(inflight_state, base_dir=base_dir)

    positions = load_positions(base_dir=base_dir)
    active_position = next(
        (
            p for p in positions
            if p.symbol == fact.symbol
            and p.status in {'open', 'partially_reduced'}
            and classify_position_truth_domain(p) != 'simulation'
        ),
        None,
    )

    if fact.side.lower() == 'buy' and (fact.filled_base_amount > 0 or fact.filled_quote_amount > 0):
        position = active_position or _find_or_create_position(positions, fact)
        entry_price = fact.average_price or position.entry_price
        if position.entry_price == 0 and entry_price > 0:
            position.entry_price = entry_price
        position.last_price = entry_price or position.last_price
        if position.initial_position_size_pct <= 0 and fact.requested_position_size_pct and fact.requested_position_size_pct > 0:
            position.initial_position_size_pct = fact.requested_position_size_pct
        if position.remaining_position_size_pct <= 0:
            if fact.requested_position_size_pct and fact.requested_position_size_pct > 0:
                position.remaining_position_size_pct = fact.requested_position_size_pct
            elif position.initial_position_size_pct > 0:
                position.remaining_position_size_pct = position.initial_position_size_pct
        if position.entry_base_amount == 0 and fact.filled_base_amount > 0:
            position.entry_base_amount = fact.filled_base_amount
        if position.entry_quote_amount == 0 and fact.filled_quote_amount > 0:
            position.entry_quote_amount = fact.filled_quote_amount
        if fact.status in partial_statuses:
            position.status = 'open'
            position.notes.append(f'{utc_now_iso()} partial fill reconciled')
            actions.append(f'POSITION_PARTIAL_FILL_RECONCILED symbol={fact.symbol}')
        elif fact.status in {'filled', 'closed'}:
            position.status = 'open'
            position.notes.append(f'{utc_now_iso()} full fill reconciled')
            actions.append(f'POSITION_FILLED_RECONCILED symbol={fact.symbol}')
        if not any(existing.position_id == position.position_id for existing in positions):
            positions.append(position)
        save_positions(positions, base_dir=base_dir)

    if fact.side.lower() == 'sell' and active_position is not None and fact.filled_base_amount > 0:
        remaining_base = fact.remaining_base_amount
        active_position.last_price = fact.average_price or active_position.last_price
        delta_filled_base_amount = _resolve_delta_filled_base_amount(prior_order_snapshot, fact)

        exit_like_intent = fact.action_intent in {'SELL_EXIT', 'EXIT', 'FULL_EXIT'}
        reduce_like_intent = fact.action_intent in {'SELL_REDUCE', 'REDUCE', 'PARTIAL_EXIT'}

        active_position.remaining_position_size_pct = _resolve_remaining_position_size_pct(active_position, delta_filled_base_amount)

        should_close = False
        if exit_like_intent and fact.status in {'filled', 'closed'} and active_position.remaining_position_size_pct <= 0:
            should_close = True
        if active_position.remaining_position_size_pct <= 0:
            should_close = True

        residue = classify_live_position_residue(
            active_position,
            reference_price=fact.average_price,
        )
        if reduce_like_intent:
            _apply_reduce_fill_lifecycle(active_position, fact)
        if not should_close and residue.is_residue:
            should_close = True
            _append_unique(active_position.tags, 'residue_dust')
            active_position.notes.append(
                (
                    f'{utc_now_iso()} residue dust classified kind={residue.residue_kind} '
                    f'remaining_base={residue.estimated_remaining_base_amount:.12f} '
                    f'remaining_quote={residue.estimated_remaining_quote_amount:.8f} '
                    f'threshold_quote={residue.tiny_live_quote_threshold:.8f}'
                )
            )
            actions.append(
                (
                    f'POSITION_RESIDUE_CLASSIFIED symbol={fact.symbol} '
                    f'kind={residue.residue_kind} '
                    f'remaining_quote={residue.estimated_remaining_quote_amount:.8f} '
                    f'threshold_quote={residue.tiny_live_quote_threshold:.8f}'
                )
            )

        if not should_close and (reduce_like_intent or fact.status in partial_statuses or (remaining_base is not None and remaining_base > 0)):
            active_position.status = 'partially_reduced'
            active_position.notes.append(f'{utc_now_iso()} sell partial fill reconciled intent={fact.action_intent}')
            actions.append(f'POSITION_SELL_PARTIAL_RECONCILED symbol={fact.symbol} remaining_pct={active_position.remaining_position_size_pct}')
        elif should_close:
            active_position.remaining_position_size_pct = 0.0
            if str(fact.expected_position_status_after_fill or '').strip().lower() == 'stopped':
                active_position.status = 'stopped'
            else:
                active_position.status = 'closed'
            if residue.is_residue:
                active_position.notes.append(f'{utc_now_iso()} sell exit reconciled as residue intent={fact.action_intent}')
            else:
                active_position.notes.append(f'{utc_now_iso()} sell exit reconciled intent={fact.action_intent}')
            release_path = append_active_trade_release(
                {
                    'position_id': active_position.position_id,
                    'symbol': active_position.symbol,
                    'release_reason': 'live_fill_reconcile_residue_dust' if residue.is_residue else 'live_fill_reconcile_exit',
                    'resulting_position_status': active_position.status,
                    'client_order_id': fact.client_order_id,
                    'exchange_order_id': fact.exchange_order_id,
                    'residue_kind': residue.residue_kind,
                    'estimated_remaining_base_amount': residue.estimated_remaining_base_amount,
                    'estimated_remaining_quote_amount': residue.estimated_remaining_quote_amount,
                    'tiny_live_quote_threshold': residue.tiny_live_quote_threshold,
                },
                base_dir=base_dir,
            )
            actions.append(f'POSITION_EXIT_RECONCILED symbol={fact.symbol} release_log={release_path}')
            post_exit_reconcile_required = True
        save_positions(positions, base_dir=base_dir)

    if post_exit_reconcile_required:
        reconcile = reconcile_control_plane_state(base_dir=base_dir)
        actions.extend(reconcile.actions)
        actions.append(
            'CONTROL_PLANE_POST_EXIT_RECONCILED '
            f'before={reconcile.before_status} after={reconcile.after_status}'
        )
        submit_state = load_live_submit_state(base_dir=base_dir)
        inflight_state = load_live_inflight_state(base_dir=base_dir)

    return LiveFillReconcileResult(ok=True, actions=actions, submit_state=submit_state, inflight_state=inflight_state)
