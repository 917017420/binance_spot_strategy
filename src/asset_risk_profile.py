from __future__ import annotations

from .asset_buckets import classify_asset_bucket


DEFAULT_BUCKET_WEIGHTS = {
    'strict': 2,
    'normal': 1,
    'lenient': 0,
}

STRICT_BUCKETS = {'meme_beta', 'ai_beta'}
LENIENT_BUCKETS = {'store_of_value'}


def bucket_profile(symbol: str) -> str:
    bucket = classify_asset_bucket(symbol)
    if bucket in STRICT_BUCKETS:
        return 'strict'
    if bucket in LENIENT_BUCKETS:
        return 'lenient'
    return 'normal'


def bucket_soft_risk_weight(symbol: str, overrides: dict[str, int] | None = None) -> int:
    profile = bucket_profile(symbol)
    weights = {**DEFAULT_BUCKET_WEIGHTS, **(overrides or {})}
    return weights.get(profile, DEFAULT_BUCKET_WEIGHTS['normal'])
