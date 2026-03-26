from __future__ import annotations

import re
from dataclasses import dataclass

from .models import PendingConfirmation


@dataclass
class ConfirmationCommand:
    action: str
    symbol_text: str | None = None
    requested_position_size_pct: float | None = None


BUY_PATTERNS = [
    re.compile(r"^确认买入\s+([A-Za-z0-9/_-]+)(?:\s+(\d+(?:\.\d+)?))?%?$"),
]
CANCEL_PATTERNS = [
    re.compile(r"^取消\s+([A-Za-z0-9/_-]+)$"),
]


def normalize_symbol_text(symbol_text: str) -> str:
    text = symbol_text.strip().upper()
    if "/" not in text and text.endswith("USDT"):
        text = text[:-4] + "/USDT"
    elif "/" not in text:
        text = text + "/USDT"
    return text


def parse_confirmation_command(text: str) -> ConfirmationCommand | None:
    cleaned = text.strip()
    for pattern in BUY_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            symbol_text = normalize_symbol_text(match.group(1))
            size = float(match.group(2)) if match.group(2) is not None else None
            return ConfirmationCommand(action="confirm_buy", symbol_text=symbol_text, requested_position_size_pct=size)
    for pattern in CANCEL_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            symbol_text = normalize_symbol_text(match.group(1))
            return ConfirmationCommand(action="cancel", symbol_text=symbol_text)
    return None


def match_pending_confirmation(
    command: ConfirmationCommand,
    confirmations: list[PendingConfirmation],
) -> PendingConfirmation | None:
    for item in reversed(confirmations):
        if item.status != "pending":
            continue
        if command.symbol_text and item.symbol.upper() == command.symbol_text.upper():
            return item
    return None
