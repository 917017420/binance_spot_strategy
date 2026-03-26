from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings, load_settings
from .exchange import create_exchange
from .live_exchange_adapter import LiveCreateOrderRequest, map_exchange_order_response
from .live_fill_reconcile import apply_live_order_fact
from .live_inflight_state import extract_symbol_from_logical_key, load_live_inflight_state
from .live_submit_state import load_live_submit_state
from .order_lifecycle_log import append_order_lifecycle_event, has_recent_order_lifecycle_event
from .runner_state import load_runner_state, save_runner_state
from .utils import utc_now_iso


TERMINAL_ORDER_STATUSES = {'filled', 'closed', 'canceled', 'cancelled', 'rejected'}
PARTIAL_ORDER_STATUSES = {'partial', 'partially_filled', 'partially-filled'}
OPEN_ORDER_STATUSES = {'open', 'new'}


@dataclass
class OrderRefreshTarget:
    symbol: str
    client_order_id: str
    source: str
    request_snapshot: dict = field(default_factory=dict)


@dataclass
class OrderRefreshReconcileResult:
    ok: bool
    actions: list[str] = field(default_factory=list)
    order_found: bool = False
    order_status: str | None = None
    stage: str | None = None
    error: str | None = None
    refreshed_at: str | None = None
    target_count: int = 0
    refreshed_count: int = 0
    raw_order: dict | None = None


def _normalize_status(value: str | None) -> str:
    return str(value or '').strip().lower().replace(' ', '_')


def _collect_refresh_targets(*, base_dir=None) -> list[OrderRefreshTarget]:
    targets: list[OrderRefreshTarget] = []
    seen: set[tuple[str, str]] = set()

    submit_state = load_live_submit_state(base_dir=base_dir)
    last_symbol = submit_state.get('last_symbol')
    last_client_order_id = submit_state.get('last_client_order_id')
    last_status = _normalize_status(submit_state.get('last_submit_status'))
    if last_symbol and last_client_order_id and last_status not in TERMINAL_ORDER_STATUSES:
        key = (str(last_symbol), str(last_client_order_id))
        if key not in seen:
            seen.add(key)
            targets.append(
                OrderRefreshTarget(
                    symbol=str(last_symbol),
                    client_order_id=str(last_client_order_id),
                    source='last_submit_state',
                    request_snapshot=submit_state.get('last_request') or {},
                )
            )

    inflight_state = load_live_inflight_state(base_dir=base_dir)
    for logical_key, item in (inflight_state.get('orders') or {}).items():
        symbol = item.get('symbol') or extract_symbol_from_logical_key(logical_key)
        client_order_id = item.get('client_order_id') or item.get('clientOrderId') or item.get('order_client_id')
        if not symbol or not client_order_id:
            continue
        key = (str(symbol), str(client_order_id))
        if key in seen:
            continue
        seen.add(key)
        targets.append(OrderRefreshTarget(symbol=str(symbol), client_order_id=str(client_order_id), source='live_inflight_state'))

    return targets


def _map_lifecycle_event(status: str, *, first_seen: bool) -> str | None:
    if status in OPEN_ORDER_STATUSES:
        return 'first_seen_open' if first_seen else None
    if status in PARTIAL_ORDER_STATUSES:
        return 'partial_fill_seen'
    if status in {'filled', 'closed'}:
        return 'filled_seen'
    if status in {'canceled', 'cancelled'}:
        return 'canceled_seen'
    if status == 'rejected':
        return 'rejected_seen'
    return None


def _fetch_matching_order(exchange, *, symbol: str, client_order_id: str):
    orders = exchange.fetch_open_orders(symbol)
    matching = [item for item in orders if _matches_client_order_id(item, client_order_id)]
    if matching:
        return matching[0], 'fetch_open_orders'
    closed_orders = exchange.fetch_closed_orders(symbol)
    matching = [item for item in closed_orders if _matches_client_order_id(item, client_order_id)]
    if matching:
        return matching[0], 'fetch_closed_orders'
    return None, 'locate_order'


def _matches_client_order_id(raw_order: dict, client_order_id: str) -> bool:
    candidates = {
        raw_order.get('clientOrderId'),
        raw_order.get('client_order_id'),
        raw_order.get('order_client_id'),
    }
    return str(client_order_id) in {str(item) for item in candidates if item not in {None, ''}}


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _resolve_reference_price(raw_order: dict, request_snapshot: dict) -> float:
    for candidate in (
        raw_order.get('average'),
        raw_order.get('price'),
        request_snapshot.get('reference_price'),
    ):
        value = _coerce_float(candidate, default=0.0)
        if value > 0:
            return value
    return 0.0


def _resolve_quote_amount(raw_order: dict, request_snapshot: dict, reference_price: float) -> float:
    for candidate in (
        raw_order.get('cost'),
        request_snapshot.get('quote_amount'),
    ):
        value = _coerce_float(candidate, default=0.0)
        if value > 0:
            return value
    amount = _coerce_float(raw_order.get('amount'), default=0.0)
    if amount > 0 and reference_price > 0:
        return amount * reference_price
    return 0.0


def _persist_order_refresh_result(result: OrderRefreshReconcileResult, *, base_dir=None) -> None:
    state = load_runner_state(base_dir=base_dir)
    state.update({
        'last_order_refresh_ok': result.ok,
        'last_order_refresh_found': result.order_found,
        'last_order_refresh_status': result.order_status,
        'last_order_refresh_stage': result.stage,
        'last_order_refresh_error': result.error,
        'last_order_refresh_ts': result.refreshed_at,
        'last_order_refresh_attempt_ts': result.refreshed_at,
        'next_order_refresh_after_ts': None,
        'last_order_refresh_target_count': result.target_count,
        'last_order_refresh_refreshed_count': result.refreshed_count,
        'last_order_refresh_actions': result.actions,
    })
    save_runner_state(state, base_dir=base_dir)



def run_order_refresh_reconcile(*, symbol: str | None = None, client_order_id: str | None = None, config_path: str | None = None, env_path: str | None = None, settings: Settings | None = None, base_dir=None) -> OrderRefreshReconcileResult:
    settings = settings or load_settings(config_path=config_path, env_path=env_path)
    refreshed_at = utc_now_iso()

    if symbol and client_order_id:
        targets = [OrderRefreshTarget(symbol=symbol, client_order_id=client_order_id, source='explicit')]
    else:
        targets = _collect_refresh_targets(base_dir=base_dir)

    if not targets:
        result = OrderRefreshReconcileResult(
            ok=False,
            actions=['ORDER_REFRESH_SKIPPED no_refresh_targets'],
            order_found=False,
            stage='bootstrap',
            error='no_refresh_targets',
            refreshed_at=refreshed_at,
            target_count=0,
            refreshed_count=0,
        )
        _persist_order_refresh_result(result, base_dir=base_dir)
        return result

    exchange = None
    actions: list[str] = []
    raw_order = None
    last_status = None
    any_found = False
    refreshed_count = 0
    first_error = None
    stage = 'start'

    try:
        exchange = create_exchange(settings)
        for target in targets:
            try:
                raw_order, stage = _fetch_matching_order(exchange, symbol=target.symbol, client_order_id=target.client_order_id)
            except Exception as exc:
                if first_error is None:
                    first_error = f'{type(exc).__name__}: {exc}'
                    stage = stage if stage != 'start' else 'fetch_open_orders'
                actions.append(f'ORDER_REFRESH_FETCH_FAILED symbol={target.symbol} client_order_id={target.client_order_id} error={type(exc).__name__}: {exc}')
                continue

            if raw_order is None:
                actions.append(f'ORDER_REFRESH_NOT_FOUND symbol={target.symbol} client_order_id={target.client_order_id}')
                continue

            any_found = True
            reference_price = _resolve_reference_price(raw_order, target.request_snapshot)
            quote_amount = _resolve_quote_amount(raw_order, target.request_snapshot, reference_price)
            metadata = {
                **((target.request_snapshot.get('metadata') or {}) if isinstance(target.request_snapshot, dict) else {}),
                'source': 'order_refresh_reconcile',
                'refresh_source': target.source,
            }
            requested_position_size_pct = target.request_snapshot.get('requested_position_size_pct')
            if requested_position_size_pct is not None:
                metadata['requested_position_size_pct'] = requested_position_size_pct
            base_amount = _coerce_float(raw_order.get('amount') or raw_order.get('filled'), default=0.0)
            request = LiveCreateOrderRequest(
                symbol=target.symbol,
                side=(raw_order.get('side') or 'buy'),
                order_type=(raw_order.get('type') or 'market'),
                quote_amount=quote_amount,
                base_amount=base_amount,
                client_order_id=target.client_order_id,
                reference_price=reference_price,
                metadata=metadata,
            )
            response = map_exchange_order_response(raw_order, request)
            reconcile = apply_live_order_fact(response, request, base_dir=base_dir)
            refreshed_count += 1
            last_status = response.status
            actions.append(f'ORDER_REFRESH_RECONCILED symbol={target.symbol} status={response.status} client_order_id={target.client_order_id} source={target.source}')
            actions.extend(reconcile.actions)

            normalized_status = _normalize_status(response.status)
            lifecycle_event = _map_lifecycle_event(normalized_status, first_seen=(target.source == 'last_submit_state'))
            if lifecycle_event:
                duplicated = has_recent_order_lifecycle_event(
                    event=lifecycle_event,
                    symbol=target.symbol,
                    client_order_id=target.client_order_id,
                    status=normalized_status,
                )
                if duplicated:
                    actions.append(f'ORDER_LIFECYCLE_EVENT_SKIPPED_DUPLICATE event={lifecycle_event} symbol={target.symbol} client_order_id={target.client_order_id}')
                else:
                    path = append_order_lifecycle_event({
                        'event': lifecycle_event,
                        'symbol': target.symbol,
                        'client_order_id': target.client_order_id,
                        'status': normalized_status,
                        'source': target.source,
                    })
                    actions.append(f'ORDER_LIFECYCLE_EVENT event={lifecycle_event} path={path}')

        ok = any_found and refreshed_count > 0
        if not ok and first_error is None:
            first_error = 'order_not_found'
            stage = 'locate_order'
        elif ok:
            stage = 'reconciled'

        result = OrderRefreshReconcileResult(
            ok=ok,
            actions=actions,
            order_found=any_found,
            order_status=last_status,
            stage=stage,
            error=first_error,
            refreshed_at=refreshed_at,
            target_count=len(targets),
            refreshed_count=refreshed_count,
            raw_order=raw_order,
        )
        _persist_order_refresh_result(result, base_dir=base_dir)
        return result
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()


def format_order_refresh_reconcile(symbol: str | None = None, client_order_id: str | None = None, config_path: str | None = None, env_path: str | None = None, result: OrderRefreshReconcileResult | None = None) -> str:
    result = result or run_order_refresh_reconcile(symbol=symbol, client_order_id=client_order_id, config_path=config_path, env_path=env_path)
    lines = [
        'ORDER REFRESH RECONCILE',
        f'- ok: {result.ok}',
        f'- order_found: {result.order_found}',
        f'- order_status: {result.order_status}',
        f'- stage: {result.stage}',
        f'- error: {result.error}',
        f'- refreshed_at: {result.refreshed_at}',
        f'- target_count: {result.target_count}',
        f'- refreshed_count: {result.refreshed_count}',
        '',
        'ACTIONS',
    ]
    for item in result.actions:
        lines.append(f'- {item}')
    if result.raw_order:
        lines.extend([
            '',
            'RAW ORDER',
            str({
                'id': result.raw_order.get('id'),
                'clientOrderId': result.raw_order.get('clientOrderId'),
                'symbol': result.raw_order.get('symbol'),
                'status': result.raw_order.get('status'),
                'side': result.raw_order.get('side'),
                'type': result.raw_order.get('type'),
                'filled': result.raw_order.get('filled'),
                'remaining': result.raw_order.get('remaining'),
                'cost': result.raw_order.get('cost'),
                'average': result.raw_order.get('average'),
            })
        ])
    return '\n'.join(lines)
