from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .live_order_payload import LiveOrderPayload
from .single_active_trade_state import build_single_active_trade_state


@dataclass
class SubmitPreflightResult:
    ok: bool
    blocked_reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    normalized: dict = field(default_factory=dict)



def _check(name: str, ok: bool, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}



def run_submit_preflight(settings: Settings, payload: LiveOrderPayload, markets: dict | None = None) -> SubmitPreflightResult:
    checks: list[dict] = []
    blocked_reasons: list[str] = []
    side = str(payload.side or '').lower()
    order_type = str(payload.order_type or '').lower()
    quote_amount = float(payload.quote_amount)
    base_amount = float(getattr(payload, 'base_amount', 0.0) or 0.0)
    reference_price = float(payload.reference_price)

    supported_sides = {'buy', 'sell'}
    checks.append(_check('side_supported', side in supported_sides, f'side={payload.side}'))
    if side not in supported_sides:
        blocked_reasons.append('unsupported_side')

    supported_order_types = {'market', 'limit'}
    checks.append(_check('order_type_supported', order_type in supported_order_types, f'order_type={payload.order_type}'))
    if order_type not in supported_order_types:
        blocked_reasons.append('unsupported_order_type')

    checks.append(_check('quote_amount_positive', quote_amount > 0, f'quote_amount={payload.quote_amount}'))
    if quote_amount <= 0:
        blocked_reasons.append('quote_amount_non_positive')

    checks.append(_check('limit_reference_price_valid', order_type != 'limit' or reference_price > 0, f'order_type={payload.order_type} reference_price={payload.reference_price}'))
    if order_type == 'limit' and reference_price <= 0:
        blocked_reasons.append('reference_price_non_positive')

    checks.append(_check('private_enabled', bool(settings.api.enable_private), f'enable_private={settings.api.enable_private}'))
    if not settings.api.enable_private:
        blocked_reasons.append('private_mode_disabled')

    checks.append(_check('api_key_present', bool(settings.api.api_key), f'api_key_present={bool(settings.api.api_key)}'))
    if not settings.api.api_key:
        blocked_reasons.append('api_key_missing')

    checks.append(_check('api_secret_present', bool(settings.api.api_secret), f'api_secret_present={bool(settings.api.api_secret)}'))
    if not settings.api.api_secret:
        blocked_reasons.append('api_secret_missing')

    checks.append(_check('order_submit_enabled', bool(settings.api.enable_order_submit), f'enable_order_submit={settings.api.enable_order_submit}'))
    if not settings.api.enable_order_submit:
        blocked_reasons.append('order_submit_disabled')

    active_trade_state = build_single_active_trade_state()
    checks.append(_check('single_active_trade_state', not active_trade_state.lock.blocking or active_trade_state.lock.active_symbol == payload.symbol, f"active_symbol={active_trade_state.lock.active_symbol} lock_reason={active_trade_state.lock.lock_reason}"))
    if active_trade_state.lock.blocking and active_trade_state.lock.active_symbol not in {None, payload.symbol}:
        blocked_reasons.append('single_active_trade_locked_by_other_symbol')
    if active_trade_state.lock.blocking and active_trade_state.lock.lock_reason in {'multiple_active_positions_detected', 'multiple_live_inflight_detected', 'live_domain_symbol_conflict'}:
        blocked_reasons.append(active_trade_state.lock.lock_reason)

    market = (markets or {}).get(payload.symbol) if isinstance(markets, dict) else None
    checks.append(_check('market_loaded', market is not None, f'market_present={market is not None} symbol={payload.symbol}'))
    if market is None:
        blocked_reasons.append('market_metadata_missing')
        normalized = {
            'symbol': payload.symbol,
            'quote_amount': payload.quote_amount,
            'order_type': payload.order_type,
            'side': payload.side,
        }
        return SubmitPreflightResult(ok=False, blocked_reasons=blocked_reasons, checks=checks, normalized=normalized)

    limits = market.get('limits') or {}
    precision = market.get('precision') or {}
    min_cost = (((limits.get('cost') or {}).get('min')) if isinstance(limits.get('cost'), dict) else None)
    min_amount = (((limits.get('amount') or {}).get('min')) if isinstance(limits.get('amount'), dict) else None)

    estimated_base_amount = None
    estimated_notional = quote_amount
    if side == 'sell':
        estimated_base_amount = base_amount if base_amount > 0 else None
        if estimated_base_amount is not None and reference_price > 0:
            estimated_notional = estimated_base_amount * reference_price
    elif reference_price > 0:
        estimated_base_amount = quote_amount / reference_price

    if min_cost is not None:
        enough_cost = estimated_notional >= float(min_cost)
        checks.append(_check('min_notional', enough_cost, f'estimated_notional={estimated_notional} min_cost={min_cost} side={side}'))
        if not enough_cost:
            blocked_reasons.append('quote_amount_below_min_notional')

    if side == 'sell':
        checks.append(_check('base_amount_positive', base_amount > 0, f'base_amount={base_amount}'))
        if base_amount <= 0:
            blocked_reasons.append('base_amount_non_positive')

    if min_amount is not None and estimated_base_amount is not None:
        meets_min_amount = estimated_base_amount >= float(min_amount)
        checks.append(_check('min_amount', meets_min_amount, f'estimated_base_amount={estimated_base_amount} min_amount={min_amount}'))
        if not meets_min_amount:
            blocked_reasons.append('base_amount_below_min_amount')

    normalized = {
        'symbol': payload.symbol,
        'side': side,
        'order_type': order_type,
        'quote_amount': quote_amount,
        'base_amount': base_amount,
        'reference_price': reference_price,
        'estimated_base_amount': estimated_base_amount,
        'estimated_notional': estimated_notional,
        'client_order_id': payload.client_order_id,
        'market_precision': precision,
        'market_min_cost': min_cost,
        'market_min_amount': min_amount,
    }
    return SubmitPreflightResult(
        ok=len(blocked_reasons) == 0,
        blocked_reasons=blocked_reasons,
        checks=checks,
        normalized=normalized,
    )
