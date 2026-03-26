from __future__ import annotations

import logging

from .config import Settings
from .exchange import extract_quote_volume
from .models import SkippedSymbol


LOGGER = logging.getLogger(__name__)


def _is_excluded_symbol(base: str, settings: Settings) -> str | None:
    if base in settings.universe.excluded_bases:
        return "excluded_base_asset"

    upper_base = base.upper()
    for pattern in settings.universe.excluded_symbol_patterns:
        if upper_base.endswith(pattern) or pattern in upper_base:
            return f"excluded_pattern:{pattern}"
    return None


def build_symbol_universe(
    exchange,
    settings: Settings,
    top: int | None = None,
) -> tuple[list[str], list[SkippedSymbol], dict[str, float]]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    eligible: list[tuple[str, float]] = []
    skipped: list[SkippedSymbol] = []
    quote_volume_by_symbol: dict[str, float] = {}

    for symbol, market in markets.items():
        if market.get("quote") != settings.universe.quote_asset:
            continue
        if not market.get("spot"):
            continue
        if not market.get("active", True):
            skipped.append(SkippedSymbol(symbol=symbol, reason="inactive_market"))
            continue

        base = str(market.get("base", "")).upper()
        excluded_reason = _is_excluded_symbol(base, settings)
        if excluded_reason:
            skipped.append(SkippedSymbol(symbol=symbol, reason=excluded_reason))
            continue

        ticker = tickers.get(symbol)
        if not ticker:
            skipped.append(SkippedSymbol(symbol=symbol, reason="missing_ticker"))
            continue

        quote_volume = extract_quote_volume(ticker)
        quote_volume_by_symbol[symbol] = quote_volume
        if quote_volume < settings.universe.min_quote_volume:
            skipped.append(
                SkippedSymbol(
                    symbol=symbol,
                    reason="low_liquidity",
                )
            )
            continue

        eligible.append((symbol, quote_volume))

    eligible.sort(key=lambda item: item[1], reverse=True)
    selected = eligible[:top] if top is not None else eligible
    LOGGER.info(
        "Selected %s eligible symbols from %s markets (%s total eligible before scan cap)",
        len(selected),
        len(markets),
        len(eligible),
    )
    return [symbol for symbol, _ in selected], skipped, quote_volume_by_symbol
