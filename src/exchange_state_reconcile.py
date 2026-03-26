from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings, load_settings
from .exchange import create_exchange
from .positions_store import load_live_active_positions
from .live_inflight_state import extract_symbol_from_logical_key, load_live_inflight_state


@dataclass
class ExchangeStateReconcileResult:
    ok: bool
    blocked_reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    remote_summary: dict = field(default_factory=dict)
    local_summary: dict = field(default_factory=dict)



def _check(name: str, ok: bool, detail: str) -> dict:
    return {'name': name, 'ok': ok, 'detail': detail}


def _local_inflight_symbols(inflight_state: dict) -> list[str]:
    symbols: set[str] = set()
    for logical_key, item in (inflight_state.get('orders') or {}).items():
        symbol = item.get('symbol') or extract_symbol_from_logical_key(logical_key)
        if symbol:
            symbols.add(str(symbol))
    return sorted(symbols)



def run_exchange_state_reconcile(config_path: str | None = None, env_path: str | None = None, settings: Settings | None = None) -> ExchangeStateReconcileResult:
    settings = settings or load_settings(config_path=config_path, env_path=env_path)
    checks: list[dict] = []
    blocked_reasons: list[str] = []

    active_positions = load_live_active_positions()
    inflight_state = load_live_inflight_state()
    local_active_symbols = sorted({position.symbol for position in active_positions})
    local_inflight_keys = sorted((inflight_state.get('orders') or {}).keys())
    local_inflight_symbols = _local_inflight_symbols(inflight_state)

    checks.append(_check('private_enabled', bool(settings.api.enable_private), f'enable_private={settings.api.enable_private}'))
    if not settings.api.enable_private:
        blocked_reasons.append('private_mode_disabled')

    checks.append(_check('api_key_present', bool(settings.api.api_key), f'api_key_present={bool(settings.api.api_key)}'))
    if not settings.api.api_key:
        blocked_reasons.append('api_key_missing')

    checks.append(_check('api_secret_present', bool(settings.api.api_secret), f'api_secret_present={bool(settings.api.api_secret)}'))
    if not settings.api.api_secret:
        blocked_reasons.append('api_secret_missing')

    remote_open_orders = []
    remote_nonzero_assets = []
    remote_balances_loaded = False
    remote_orders_loaded = False

    if blocked_reasons:
        return ExchangeStateReconcileResult(
            ok=False,
            blocked_reasons=blocked_reasons,
            checks=checks,
            remote_summary={
                'open_orders': remote_open_orders,
                'nonzero_assets': remote_nonzero_assets,
            },
            local_summary={
                'active_symbols': local_active_symbols,
                'inflight_keys': local_inflight_keys,
                'inflight_symbols': local_inflight_symbols,
            },
        )

    exchange = None
    try:
        exchange = create_exchange(settings)
        try:
            balances = exchange.fetch_balance()
            remote_balances_loaded = True
            totals = balances.get('total') or {}
            remote_nonzero_assets = sorted(
                asset for asset, amount in totals.items()
                if amount not in (None, 0, 0.0)
            )[:30]
            checks.append(_check('fetch_balance', True, f'nonzero_assets_sample={remote_nonzero_assets[:10]}'))
        except Exception as exc:
            blocked_reasons.append('fetch_balance_failed')
            checks.append(_check('fetch_balance', False, f'{type(exc).__name__}: {exc}'))

        try:
            open_orders = exchange.fetch_open_orders()
            remote_orders_loaded = True
            remote_open_orders = [
                {
                    'symbol': item.get('symbol'),
                    'id': item.get('id'),
                    'clientOrderId': item.get('clientOrderId'),
                    'status': item.get('status'),
                    'type': item.get('type'),
                    'side': item.get('side'),
                }
                for item in open_orders[:30]
            ]
            checks.append(_check('fetch_open_orders', True, f'open_orders={len(open_orders)}'))
        except Exception as exc:
            blocked_reasons.append('fetch_open_orders_failed')
            checks.append(_check('fetch_open_orders', False, f'{type(exc).__name__}: {exc}'))
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()

    remote_order_symbols = sorted({item.get('symbol') for item in remote_open_orders if item.get('symbol')})

    if remote_orders_loaded and set(local_inflight_symbols) != set(remote_order_symbols):
        blocked_reasons.append('local_remote_open_order_mismatch')
        checks.append(_check('local_remote_open_order_match', False, f'local_inflight_symbols={local_inflight_symbols} remote_open_order_symbols={remote_order_symbols}'))
    else:
        checks.append(_check('local_remote_open_order_match', True, f'local_inflight_symbols={local_inflight_symbols} remote_open_order_symbols={remote_order_symbols}'))

    return ExchangeStateReconcileResult(
        ok=len(blocked_reasons) == 0,
        blocked_reasons=blocked_reasons,
        checks=checks,
        remote_summary={
            'balances_loaded': remote_balances_loaded,
            'orders_loaded': remote_orders_loaded,
            'open_orders': remote_open_orders,
            'open_order_symbols': remote_order_symbols,
            'nonzero_assets': remote_nonzero_assets,
        },
        local_summary={
            'active_symbols': local_active_symbols,
            'inflight_keys': local_inflight_keys,
            'inflight_symbols': local_inflight_symbols,
            'active_position_count': len(active_positions),
        },
    )



def format_exchange_state_reconcile(config_path: str | None = None, env_path: str | None = None, result: ExchangeStateReconcileResult | None = None) -> str:
    result = result or run_exchange_state_reconcile(config_path=config_path, env_path=env_path)
    lines = [
        'EXCHANGE STATE RECONCILE',
        f'- ok: {result.ok}',
        f'- blocked_reasons: {result.blocked_reasons}',
        '',
        'LOCAL',
        f"- active_symbols: {result.local_summary.get('active_symbols')}",
        f"- inflight_keys: {result.local_summary.get('inflight_keys')}",
        f"- inflight_symbols: {result.local_summary.get('inflight_symbols')}",
        f"- active_position_count: {result.local_summary.get('active_position_count')}",
        '',
        'REMOTE',
        f"- balances_loaded: {result.remote_summary.get('balances_loaded')}",
        f"- orders_loaded: {result.remote_summary.get('orders_loaded')}",
        f"- open_order_symbols: {result.remote_summary.get('open_order_symbols')}",
        f"- nonzero_assets: {result.remote_summary.get('nonzero_assets')}",
        '',
        'CHECKS',
    ]
    for item in result.checks:
        lines.append(f"- {item['name']}: ok={item['ok']} detail={item['detail']}")
    return '\n'.join(lines)
