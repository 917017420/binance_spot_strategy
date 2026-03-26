from __future__ import annotations

MAJOR_BETA = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"}
AI_BUCKET = {"TAO/USDT", "FET/USDT", "AGIX/USDT", "WLD/USDT"}
PAYMENTS_BUCKET = {"XRP/USDT", "XLM/USDT", "XNO/USDT"}
STORE_OF_VALUE_BUCKET = {"BTC/USDT", "PAXG/USDT"}
L1_BUCKET = {"ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT", "SUI/USDT", "APT/USDT"}
MEME_BUCKET = {"DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "BONK/USDT", "WIF/USDT"}


def classify_asset_bucket(symbol: str) -> str:
    if symbol in STORE_OF_VALUE_BUCKET:
        return "store_of_value"
    if symbol in AI_BUCKET:
        return "ai_beta"
    if symbol in PAYMENTS_BUCKET:
        return "payments"
    if symbol in L1_BUCKET:
        return "layer1_beta"
    if symbol in MEME_BUCKET:
        return "meme_beta"
    if symbol in MAJOR_BETA:
        return "major_beta"
    return f"alt_{symbol.split('/')[0][:3].lower()}"
