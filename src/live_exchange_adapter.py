from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .exchange import create_exchange
from .exchange_state_reconcile import run_exchange_state_reconcile
from .live_fill_reconcile import apply_live_order_fact
from .live_order_payload import LiveOrderPayload
from .live_submit_state import save_live_submit_state
from .submit_preflight import run_submit_preflight
from .utils import utc_now_iso


_LIVE_SELL_BALANCE_BUFFER_RATIO = 0.0005
_LIVE_SELL_BALANCE_BUFFER_MIN_ABS = 1e-8


@dataclass
class LiveCreateOrderRequest:
    symbol: str
    side: str
    order_type: str
    quote_amount: float
    base_amount: float
    client_order_id: str
    reference_price: float
    metadata: dict = field(default_factory=dict)


@dataclass
class LiveExchangeCreateOrderParams:
    exchange_method: str
    symbol: str
    type: str
    side: str
    amount: float | None
    price: float | None
    params: dict = field(default_factory=dict)
    call_preview: dict = field(default_factory=dict)


@dataclass
class LiveCreateOrderResponse:
    exchange_order_id: str | None
    client_order_id: str
    status: str
    filled_quote_amount: float
    filled_base_amount: float
    average_fill_price: float | None = None
    remaining_base_amount: float | None = None
    fee: dict | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class LiveSubmitError:
    type: str
    message: str
    stage: str
    recoverable: bool
    raw: dict = field(default_factory=dict)


@dataclass
class LiveOrderSubmitResult:
    status: str
    message: str
    details: dict = field(default_factory=dict)


def _coerce_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _clone_request_with_updates(request: LiveCreateOrderRequest, **updates) -> LiveCreateOrderRequest:
    metadata = dict(request.metadata or {})
    metadata.update(dict(updates.pop('metadata', {}) or {}))
    return LiveCreateOrderRequest(
        symbol=updates.get('symbol', request.symbol),
        side=updates.get('side', request.side),
        order_type=updates.get('order_type', request.order_type),
        quote_amount=updates.get('quote_amount', request.quote_amount),
        base_amount=updates.get('base_amount', request.base_amount),
        client_order_id=updates.get('client_order_id', request.client_order_id),
        reference_price=updates.get('reference_price', request.reference_price),
        metadata=metadata,
    )


def _payload_from_request(request: LiveCreateOrderRequest) -> LiveOrderPayload:
    return LiveOrderPayload(
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        quote_amount=request.quote_amount,
        base_amount=request.base_amount,
        reference_price=request.reference_price,
        requested_position_size_pct=float((request.metadata or {}).get('requested_position_size_pct') or 0.0),
        client_order_id=request.client_order_id,
        metadata=dict(request.metadata or {}),
    )


def _resolve_base_asset(symbol: str, market: dict | None = None) -> str:
    market_base = str((market or {}).get('base') or '').strip()
    if market_base:
        return market_base
    return str(symbol or '').split('/')[0].split(':')[0].strip()


def _extract_asset_balance_snapshot(balances: dict | None, asset: str) -> tuple[float | None, float | None, float | None, str]:
    if not balances or not asset:
        return None, None, None, 'unavailable'

    asset_bucket = balances.get(asset) if isinstance(balances.get(asset), dict) else {}
    free = _coerce_float((asset_bucket or {}).get('free'))
    used = _coerce_float((asset_bucket or {}).get('used'))
    total = _coerce_float((asset_bucket or {}).get('total'))

    free_balances = balances.get('free') if isinstance(balances.get('free'), dict) else {}
    used_balances = balances.get('used') if isinstance(balances.get('used'), dict) else {}
    total_balances = balances.get('total') if isinstance(balances.get('total'), dict) else {}

    if free is None:
        free = _coerce_float(free_balances.get(asset))
    if used is None:
        used = _coerce_float(used_balances.get(asset))
    if total is None:
        total = _coerce_float(total_balances.get(asset))

    source = 'free'
    if free is None and total is not None and used is not None:
        source = 'total_minus_used'
    elif free is None and total is not None:
        source = 'total'
    elif free is None:
        source = 'unavailable'

    return free, used, total, source


def _resolve_available_base_amount(free: float | None, used: float | None, total: float | None) -> float:
    if free is not None:
        return max(free, 0.0)
    if total is not None and used is not None:
        return max(total - used, 0.0)
    if total is not None:
        return max(total, 0.0)
    return 0.0


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return max(value, 0.0)
    steps = math.floor((value / step) + 1e-12)
    return max(steps * step, 0.0)


def _floor_to_decimals(value: float, decimals: int) -> float:
    if decimals <= 0:
        return max(math.floor(value + 1e-12), 0.0)
    scale = 10**decimals
    return max(math.floor((value * scale) + 1e-12) / scale, 0.0)


def _floor_amount_to_market_precision(value: float, market: dict | None = None) -> float:
    precision_amount = ((market or {}).get('precision') or {}).get('amount')
    if isinstance(precision_amount, int):
        return _floor_to_decimals(value, max(precision_amount, 0))

    precision_step = _coerce_float(precision_amount) or 0.0
    if precision_step > 0:
        return _floor_to_step(value, precision_step)
    return max(value, 0.0)


def normalize_sell_request_to_available_balance(
    request: LiveCreateOrderRequest,
    *,
    market: dict | None = None,
    balances: dict | None = None,
) -> LiveCreateOrderRequest:
    if str(request.side or '').lower() != 'sell' or float(request.base_amount or 0.0) <= 0:
        return request

    requested_base_amount = max(float(request.base_amount or 0.0), 0.0)
    base_asset = _resolve_base_asset(request.symbol, market)
    free_base_amount, used_base_amount, total_base_amount, balance_source = _extract_asset_balance_snapshot(balances, base_asset)
    available_base_amount = _resolve_available_base_amount(free_base_amount, used_base_amount, total_base_amount)
    balance_buffer = min(
        available_base_amount,
        max(available_base_amount * _LIVE_SELL_BALANCE_BUFFER_RATIO, _LIVE_SELL_BALANCE_BUFFER_MIN_ABS),
    )
    sellable_cap_base_amount = max(available_base_amount - balance_buffer, 0.0)
    adjusted_base_amount = _floor_amount_to_market_precision(min(requested_base_amount, sellable_cap_base_amount), market)
    adjusted_base_amount = min(adjusted_base_amount, requested_base_amount)
    adjusted_quote_amount = adjusted_base_amount * request.reference_price if request.reference_price > 0 else request.quote_amount

    return _clone_request_with_updates(
        request,
        base_amount=adjusted_base_amount,
        quote_amount=adjusted_quote_amount,
        metadata={
            'sell_balance_base_asset': base_asset,
            'sell_balance_source': balance_source,
            'sell_requested_base_amount': requested_base_amount,
            'sell_available_base_amount': available_base_amount,
            'sell_balance_buffer_base_amount': balance_buffer,
            'sellable_cap_base_amount': sellable_cap_base_amount,
            'sell_adjusted_base_amount': adjusted_base_amount,
            'sell_free_base_amount': free_base_amount,
            'sell_used_base_amount': used_base_amount,
            'sell_total_base_amount': total_base_amount,
            'sell_sizing_reason': 'live_balance_cap_with_buffer',
        },
    )


def build_create_order_request(payload: LiveOrderPayload) -> LiveCreateOrderRequest:
    metadata = dict(payload.metadata or {})
    metadata.setdefault('requested_position_size_pct', payload.requested_position_size_pct)
    return LiveCreateOrderRequest(
        symbol=payload.symbol,
        side=payload.side,
        order_type=payload.order_type,
        quote_amount=payload.quote_amount,
        base_amount=payload.base_amount,
        client_order_id=payload.client_order_id,
        reference_price=payload.reference_price,
        metadata=metadata,
    )


def build_exchange_create_order_params(request: LiveCreateOrderRequest) -> LiveExchangeCreateOrderParams:
    order_type = request.order_type.lower()
    params = {
        'clientOrderId': request.client_order_id,
    }
    amount = None
    price = None

    if order_type == 'market':
        if request.side.lower() == 'sell':
            amount = request.base_amount if request.base_amount > 0 else None
        else:
            params['quoteOrderQty'] = request.quote_amount
    elif order_type == 'limit':
        price = request.reference_price
        amount = request.quote_amount / request.reference_price if request.reference_price > 0 else None
        params['timeInForce'] = 'GTC'
    else:
        params['quoteOrderQty'] = request.quote_amount
        params['unmappedOrderType'] = order_type

    call_preview = {
        'method': 'create_order',
        'args': {
            'symbol': request.symbol,
            'type': order_type,
            'side': request.side.lower(),
            'amount': amount,
            'price': price,
            'params': params,
        },
        'intent': {
            'submit_enabled': False,
            'mode': 'preview_only',
            'reason': 'real create_order remains disabled until BINANCE_ENABLE_PRIVATE and BINANCE_ENABLE_ORDER_SUBMIT are enabled',
        },
    }

    return LiveExchangeCreateOrderParams(
        exchange_method='create_order',
        symbol=request.symbol,
        type=order_type,
        side=request.side.lower(),
        amount=amount,
        price=price,
        params=params,
        call_preview=call_preview,
    )


def map_exchange_order_response(raw_response: dict | None, request: LiveCreateOrderRequest) -> LiveCreateOrderResponse:
    raw = raw_response or {}
    average_fill_price = raw.get('average')
    try:
        average_fill_price = float(average_fill_price) if average_fill_price is not None else None
    except Exception:
        average_fill_price = None
    remaining_base_amount = raw.get('remaining')
    try:
        remaining_base_amount = float(remaining_base_amount) if remaining_base_amount is not None else None
    except Exception:
        remaining_base_amount = None
    filled_base_amount = float(raw.get('filled') or 0.0)
    filled_quote_amount = float(raw.get('cost') or 0.0)
    if filled_quote_amount <= 0 and filled_base_amount > 0 and average_fill_price is not None and average_fill_price > 0:
        filled_quote_amount = filled_base_amount * average_fill_price
    if filled_quote_amount <= 0 and filled_base_amount > 0 and request.quote_amount > 0:
        filled_quote_amount = request.quote_amount
    return LiveCreateOrderResponse(
        exchange_order_id=raw.get('id'),
        client_order_id=raw.get('clientOrderId') or request.client_order_id,
        status=raw.get('status') or 'pending_real_submit',
        filled_quote_amount=filled_quote_amount,
        filled_base_amount=filled_base_amount,
        average_fill_price=average_fill_price,
        remaining_base_amount=remaining_base_amount,
        fee=raw.get('fee'),
        raw=raw,
    )


def _request_dict(request: LiveCreateOrderRequest) -> dict:
    return {
        'symbol': request.symbol,
        'side': request.side,
        'order_type': request.order_type,
        'quote_amount': request.quote_amount,
        'base_amount': request.base_amount,
        'client_order_id': request.client_order_id,
        'reference_price': request.reference_price,
        'metadata': request.metadata,
    }


def _exchange_params_dict(params: LiveExchangeCreateOrderParams) -> dict:
    return {
        'exchange_method': params.exchange_method,
        'symbol': params.symbol,
        'type': params.type,
        'side': params.side,
        'amount': params.amount,
        'price': params.price,
        'params': params.params,
        'call_preview': params.call_preview,
    }


def _response_dict(response: LiveCreateOrderResponse) -> dict:
    return {
        'exchange_order_id': response.exchange_order_id,
        'client_order_id': response.client_order_id,
        'status': response.status,
        'filled_quote_amount': response.filled_quote_amount,
        'filled_base_amount': response.filled_base_amount,
        'raw': response.raw,
    }


def _error_dict(error: LiveSubmitError | None) -> dict | None:
    if error is None:
        return None
    return {
        'type': error.type,
        'message': error.message,
        'stage': error.stage,
        'recoverable': error.recoverable,
        'raw': error.raw,
    }


def _save_submit_state(*, request: LiveCreateOrderRequest, exchange_params: LiveExchangeCreateOrderParams, submit_status: str, response: LiveCreateOrderResponse | None = None, error: LiveSubmitError | None = None, base_dir: str | Path | None = None) -> str:
    state = {
        'last_client_order_id': request.client_order_id,
        'last_submit_status': submit_status,
        'last_submit_side': request.side,
        'last_symbol': request.symbol,
        'last_request': _request_dict(request),
        'last_exchange_params': _exchange_params_dict(exchange_params),
        'last_response': _response_dict(response) if response is not None else None,
        'last_action_intent': ((request.metadata or {}).get('action_intent') if request.metadata else None),
        'last_error': _error_dict(error),
        'updated_at': utc_now_iso(),
    }
    path = save_live_submit_state(state, base_dir=base_dir)
    return str(path)


def _build_result(
    *,
    status: str,
    message: str,
    exchange_name: str | None,
    state_path: str,
    request: LiveCreateOrderRequest,
    exchange_params: LiveExchangeCreateOrderParams,
    response: LiveCreateOrderResponse,
    error: LiveSubmitError | None = None,
) -> LiveOrderSubmitResult:
    details = {
        'submitted_at': utc_now_iso(),
        'submit_enabled': bool(error is None and exchange_params.call_preview.get('intent', {}).get('submit_enabled', False)),
        'exchange_id': exchange_name,
        'state_path': state_path,
        'request': _request_dict(request),
        'exchange_params': _exchange_params_dict(exchange_params),
        'response': _response_dict(response),
        'error': _error_dict(error),
        'submit_contract': {
            'request_stage': 'prepared',
            'exchange_mapping_stage': 'mapped',
            'call_preview_stage': 'ready',
            'adapter_call_stage': ('submitted' if exchange_params.call_preview.get('intent', {}).get('submit_enabled', False) else 'adapter_stubbed') if error is None else error.stage,
            'response_stage': response.status,
            'terminal_submit_status': 'submitted' if exchange_params.call_preview.get('intent', {}).get('submit_enabled', False) and error is None else 'submit_failed' if response.status == 'submit_failed' else None,
            'error_stage': error.stage if error is not None else None,
        },
        'submit_preflight': (error.raw if error is not None and error.stage == 'submit_preflight' else None),
        'exchange_state_reconcile': (error.raw if error is not None and error.stage == 'exchange_state_reconcile' else None),
    }
    return LiveOrderSubmitResult(status=status, message=message, details=details)


def _build_stubbed_raw_response(request: LiveCreateOrderRequest, exchange_params: LiveExchangeCreateOrderParams, exchange_name: str | None) -> dict:
    return {
        'id': None,
        'clientOrderId': request.client_order_id,
        'status': 'pending_real_submit',
        'filled': 0.0,
        'cost': 0.0,
        'submitMode': 'stubbed',
        'submitEnabled': False,
        'exchangeName': exchange_name,
        'mappedOrder': _exchange_params_dict(exchange_params),
    }


def _build_failed_response(request: LiveCreateOrderRequest) -> LiveCreateOrderResponse:
    return map_exchange_order_response(
        {
            'id': None,
            'clientOrderId': request.client_order_id,
            'status': 'submit_failed',
            'filled': 0.0,
            'cost': 0.0,
        },
        request,
    )


def _call_exchange_create_order(
    exchange,
    request: LiveCreateOrderRequest,
    exchange_params: LiveExchangeCreateOrderParams,
    exchange_name: str | None,
    *,
    submit_enabled: bool,
) -> tuple[LiveCreateOrderResponse, LiveSubmitError | None]:
    if exchange is None:
        error = LiveSubmitError(
            type='RuntimeError',
            message='exchange instance is required for adapter call preview',
            stage='adapter_call',
            recoverable=False,
            raw={'exchange_name': exchange_name},
        )
        return _build_failed_response(request), error

    try:
        if not submit_enabled:
            preview_response = _build_stubbed_raw_response(request, exchange_params, exchange_name)
            preview_response['adapterCall'] = {
                'method': exchange_params.exchange_method,
                'executed': False,
                'reason': 'submit_enabled_false',
            }
            return map_exchange_order_response(preview_response, request), None

        raw_response = exchange.create_order(
            exchange_params.symbol,
            exchange_params.type,
            exchange_params.side,
            exchange_params.amount,
            exchange_params.price,
            exchange_params.params,
        )
        raw_payload = dict(raw_response or {})
        raw_payload.setdefault('clientOrderId', request.client_order_id)
        raw_payload.setdefault('status', 'submitted')
        raw_payload['adapterCall'] = {
            'method': exchange_params.exchange_method,
            'executed': True,
            'reason': 'submit_enabled_true',
        }
        return map_exchange_order_response(raw_payload, request), None
    except Exception as exc:
        error = LiveSubmitError(
            type=type(exc).__name__,
            message=str(exc),
            stage='adapter_call',
            recoverable=False,
            raw={'exchange_name': exchange_name},
        )
        return _build_failed_response(request), error


def _preview_disabled_submit(
    *,
    request: LiveCreateOrderRequest,
    exchange_params: LiveExchangeCreateOrderParams,
    exchange_name: str | None,
    disabled_reasons: list[str],
    base_dir: str | Path | None = None,
) -> LiveOrderSubmitResult:
    response = map_exchange_order_response(_build_stubbed_raw_response(request, exchange_params, exchange_name), request)
    state_path = _save_submit_state(request=request, exchange_params=exchange_params, submit_status='adapter_stubbed', response=response, base_dir=base_dir)
    result = _build_result(
        status='adapter_stubbed',
        message='live exchange adapter call preview prepared; real submit remains disabled by settings',
        exchange_name=exchange_name,
        state_path=state_path,
        request=request,
        exchange_params=exchange_params,
        response=response,
    )
    result.details['submit_disabled_reasons'] = disabled_reasons
    result.details['submit_contract']['adapter_call_stage'] = 'adapter_stubbed'
    return result


def submit_live_order(settings: Settings | None, payload: LiveOrderPayload, *, base_dir: str | Path | None = None) -> LiveOrderSubmitResult:
    request = build_create_order_request(payload)

    try:
        exchange_params = build_exchange_create_order_params(request)
    except Exception as exc:
        error = LiveSubmitError(
            type=type(exc).__name__,
            message=str(exc),
            stage='exchange_mapping',
            recoverable=False,
            raw={},
        )
        fallback_params = LiveExchangeCreateOrderParams(
            exchange_method='create_order',
            symbol=request.symbol,
            type=request.order_type.lower(),
            side=request.side.lower(),
            amount=None,
            price=None,
            params={},
            call_preview={},
        )
        response = _build_failed_response(request)
        state_path = _save_submit_state(
            request=request,
            exchange_params=fallback_params,
            submit_status='submit_failed',
            response=response,
            error=error,
            base_dir=base_dir,
        )
        return _build_result(
            status='failed',
            message='live exchange mapping failed before adapter call',
            exchange_name=settings.exchange.name if settings is not None else None,
            state_path=state_path,
            request=request,
            exchange_params=fallback_params,
            response=response,
            error=error,
        )

    exchange_name = settings.exchange.name if settings is not None else None

    if settings is None:
        response = map_exchange_order_response(_build_stubbed_raw_response(request, exchange_params, exchange_name), request)
        state_path = _save_submit_state(request=request, exchange_params=exchange_params, submit_status='adapter_stubbed', response=response, base_dir=base_dir)
        return _build_result(
            status='adapter_stubbed',
            message='live exchange adapter path prepared without settings; real create_order remains disabled',
            exchange_name=exchange_name,
            state_path=state_path,
            request=request,
            exchange_params=exchange_params,
            response=response,
        )

    private_enabled = bool(settings.api.enable_private)
    order_submit_enabled = bool(settings.api.enable_order_submit)
    submit_enabled = private_enabled and order_submit_enabled
    exchange_params.call_preview['intent']['submit_enabled'] = submit_enabled
    exchange_params.call_preview['intent']['mode'] = 'live_submit' if submit_enabled else 'preview_only'
    exchange_params.call_preview['intent']['reason'] = (
        'real create_order enabled by BINANCE_ENABLE_PRIVATE and BINANCE_ENABLE_ORDER_SUBMIT'
        if submit_enabled
        else 'real create_order remains disabled until BINANCE_ENABLE_PRIVATE and BINANCE_ENABLE_ORDER_SUBMIT are enabled'
    )

    if not submit_enabled:
        disabled_reasons: list[str] = []
        if not private_enabled:
            disabled_reasons.append('private_mode_disabled')
        if not order_submit_enabled:
            disabled_reasons.append('order_submit_disabled')
        return _preview_disabled_submit(
            request=request,
            exchange_params=exchange_params,
            exchange_name=exchange_name,
            disabled_reasons=disabled_reasons,
            base_dir=base_dir,
        )

    exchange = None
    try:
        exchange = create_exchange(settings)
        markets = exchange.load_markets()
        market = markets.get(request.symbol) if isinstance(markets, dict) else None

        if request.side.lower() == 'sell' and request.base_amount > 0:
            try:
                balances = exchange.fetch_balance()
            except Exception as exc:
                error = LiveSubmitError(
                    type=type(exc).__name__,
                    message=str(exc),
                    stage='sell_balance_resolve',
                    recoverable=False,
                    raw={'symbol': request.symbol, 'side': request.side},
                )
                response = _build_failed_response(request)
                state_path = _save_submit_state(
                    request=request,
                    exchange_params=exchange_params,
                    submit_status='submit_failed',
                    response=response,
                    error=error,
                    base_dir=base_dir,
                )
                return _build_result(
                    status='failed',
                    message='live sell sizing failed while resolving available base balance',
                    exchange_name=exchange_name,
                    state_path=state_path,
                    request=request,
                    exchange_params=exchange_params,
                    response=response,
                    error=error,
                )

            request = normalize_sell_request_to_available_balance(request, market=market, balances=balances)
            exchange_params = build_exchange_create_order_params(request)

        preflight = run_submit_preflight(settings, _payload_from_request(request), markets=markets)
        if not preflight.ok:
            error = LiveSubmitError(
                type='SubmitPreflightError',
                message='; '.join(preflight.blocked_reasons) or 'submit preflight failed',
                stage='submit_preflight',
                recoverable=False,
                raw={
                    'blocked_reasons': preflight.blocked_reasons,
                    'checks': preflight.checks,
                    'normalized': preflight.normalized,
                },
            )
            response = _build_failed_response(request)
            state_path = _save_submit_state(
                request=request,
                exchange_params=exchange_params,
                submit_status='submit_failed',
                response=response,
                error=error,
                base_dir=base_dir,
            )
            result = _build_result(
                status='failed',
                message='live exchange submit preflight failed',
                exchange_name=exchange_name,
                state_path=state_path,
                request=request,
                exchange_params=exchange_params,
                response=response,
                error=error,
            )
            if request.side.lower() == 'sell':
                result.details['sell_sizing'] = dict(request.metadata or {})
            return result

        reconcile = run_exchange_state_reconcile(settings=settings)
        if not reconcile.ok:
            error = LiveSubmitError(
                type='ExchangeStateReconcileError',
                message='; '.join(reconcile.blocked_reasons) or 'exchange state reconcile failed',
                stage='exchange_state_reconcile',
                recoverable=False,
                raw={
                    'blocked_reasons': reconcile.blocked_reasons,
                    'checks': reconcile.checks,
                    'remote_summary': reconcile.remote_summary,
                    'local_summary': reconcile.local_summary,
                },
            )
            response = _build_failed_response(request)
            state_path = _save_submit_state(
                request=request,
                exchange_params=exchange_params,
                submit_status='submit_failed',
                response=response,
                error=error,
                base_dir=base_dir,
            )
            return _build_result(
                status='failed',
                message='live exchange state reconcile failed before submit',
                exchange_name=exchange_name,
                state_path=state_path,
                request=request,
                exchange_params=exchange_params,
                response=response,
                error=error,
            )

        response, error = _call_exchange_create_order(
            exchange,
            request,
            exchange_params,
            exchange_name,
            submit_enabled=submit_enabled,
        )
        submit_status = 'submitted' if error is None else 'submit_failed'
        state_path = _save_submit_state(
            request=request,
            exchange_params=exchange_params,
            submit_status=submit_status,
            response=response,
            error=error,
            base_dir=base_dir,
        )
        reconcile_result = apply_live_order_fact(response, request, base_dir=base_dir) if error is None else None
        result = _build_result(
            status='submitted' if error is None else 'failed',
            message='live exchange order submitted to Binance' if error is None else 'live exchange adapter call failed before submit',
            exchange_name=exchange_name,
            state_path=state_path,
            request=request,
            exchange_params=exchange_params,
            response=response,
            error=error,
        )
        if request.side.lower() == 'sell':
            result.details['sell_sizing'] = dict(request.metadata or {})
        if reconcile_result is not None:
            result.details['live_fill_reconcile'] = {
                'ok': reconcile_result.ok,
                'actions': reconcile_result.actions,
            }
        return result
    except Exception as exc:
        error = LiveSubmitError(
            type=type(exc).__name__,
            message=str(exc),
            stage='exchange_bootstrap',
            recoverable=False,
            raw={},
        )
        response = _build_failed_response(request)
        state_path = _save_submit_state(
            request=request,
            exchange_params=exchange_params,
            submit_status='submit_failed',
            response=response,
            error=error,
            base_dir=base_dir,
        )
        return _build_result(
            status='failed',
            message='live exchange adapter path failed before submit',
            exchange_name=exchange_name,
            state_path=state_path,
            request=request,
            exchange_params=exchange_params,
            response=response,
            error=error,
        )
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()
