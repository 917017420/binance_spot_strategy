from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings, load_settings
from .exchange import create_exchange


@dataclass
class BinanceReadinessCheckResult:
    ok: bool
    ready_for_private_reads: bool
    ready_for_order_submit: bool
    checks: list[dict] = field(default_factory=list)



def _check(name: str, ok: bool, detail: str, *, level: str = 'info') -> dict:
    return {
        'name': name,
        'ok': ok,
        'level': level,
        'detail': detail,
    }



def run_binance_readiness_check(config_path: str | None = None, env_path: str | None = None) -> BinanceReadinessCheckResult:
    settings: Settings = load_settings(config_path=config_path, env_path=env_path)
    checks: list[dict] = []

    private_enabled = bool(settings.api.enable_private)
    has_key = bool(settings.api.api_key)
    has_secret = bool(settings.api.api_secret)
    order_submit_enabled = bool(settings.api.enable_order_submit)

    checks.append(_check('exchange_name', settings.exchange.name == 'binance', f"exchange.name={settings.exchange.name}"))
    checks.append(_check('private_mode_enabled', private_enabled, f"enable_private={private_enabled}", level='warn' if not private_enabled else 'info'))
    checks.append(_check('api_key_present', has_key, f"api_key_present={has_key}", level='warn' if not has_key else 'info'))
    checks.append(_check('api_secret_present', has_secret, f"api_secret_present={has_secret}", level='warn' if not has_secret else 'info'))
    checks.append(_check('order_submit_flag', order_submit_enabled, f"enable_order_submit={order_submit_enabled}", level='warn' if not order_submit_enabled else 'info'))

    exchange = None
    markets_loaded = False
    balance_loaded = False
    market_detail = 'not attempted'
    balance_detail = 'not attempted'
    symbol_detail = 'not attempted'

    try:
        exchange = create_exchange(settings)
        checks.append(_check('exchange_bootstrap', True, 'ccxt binance client created'))

        try:
            markets = exchange.load_markets()
            markets_loaded = True
            btcusdt = markets.get('BTC/USDT') or {}
            symbol_detail = (
                f"BTC/USDT precision={btcusdt.get('precision')} limits={btcusdt.get('limits')}"
                if btcusdt else 'BTC/USDT market metadata not found'
            )
            market_detail = f"loaded_markets={len(markets)}"
            checks.append(_check('load_markets', True, market_detail))
            checks.append(_check('market_metadata_btcusdt', bool(btcusdt), symbol_detail, level='warn' if not btcusdt else 'info'))
        except Exception as exc:
            market_detail = f'{type(exc).__name__}: {exc}'
            checks.append(_check('load_markets', False, market_detail, level='error'))

        if private_enabled and has_key and has_secret:
            try:
                balance = exchange.fetch_balance()
                balance_loaded = True
                total_keys = sorted((balance.get('total') or {}).keys())[:10]
                balance_detail = f"fetch_balance ok sample_assets={total_keys}"
                checks.append(_check('fetch_balance', True, balance_detail))
            except Exception as exc:
                balance_detail = f'{type(exc).__name__}: {exc}'
                checks.append(_check('fetch_balance', False, balance_detail, level='error'))
        else:
            checks.append(_check('fetch_balance', False, 'skipped because private mode or credentials are not fully enabled', level='warn'))
    except Exception as exc:
        checks.append(_check('exchange_bootstrap', False, f'{type(exc).__name__}: {exc}', level='error'))
    finally:
        close_fn = getattr(exchange, 'close', None)
        if callable(close_fn):
            close_fn()

    ready_for_private_reads = private_enabled and has_key and has_secret and markets_loaded and balance_loaded
    ready_for_order_submit = ready_for_private_reads and order_submit_enabled

    checks.append(_check('ready_for_private_reads', ready_for_private_reads, f'markets_loaded={markets_loaded} balance_loaded={balance_loaded}', level='warn' if not ready_for_private_reads else 'info'))
    checks.append(_check('ready_for_order_submit', ready_for_order_submit, f'order_submit_enabled={order_submit_enabled}', level='warn' if not ready_for_order_submit else 'info'))
    checks.append(_check('next_gate', ready_for_private_reads, 'private reads must pass before exchange-state-reconcile and live submit can be trusted', level='warn' if not ready_for_private_reads else 'info'))

    return BinanceReadinessCheckResult(
        ok=all(item['ok'] or item['level'] == 'warn' for item in checks),
        ready_for_private_reads=ready_for_private_reads,
        ready_for_order_submit=ready_for_order_submit,
        checks=checks,
    )



def format_binance_readiness_check(config_path: str | None = None, env_path: str | None = None) -> str:
    result = run_binance_readiness_check(config_path=config_path, env_path=env_path)
    lines = [
        'BINANCE READINESS',
        f'- ok: {result.ok}',
        f'- ready_for_private_reads: {result.ready_for_private_reads}',
        f'- ready_for_order_submit: {result.ready_for_order_submit}',
        '',
        'CHECKS',
    ]
    for item in result.checks:
        lines.append(f"- [{item['level']}] {item['name']}: ok={item['ok']} detail={item['detail']}")
    return '\n'.join(lines)
