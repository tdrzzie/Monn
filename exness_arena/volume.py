"""Explicit symbol lot ceilings; monetary risk is still enforced by the guard."""
import math
from .common import settings

INDEX_SYMBOLS = ('USTECc', 'US30c', 'US500c')


def entry_volume_cap(symbol, config=None):
    c = settings() if config is None else config
    overrides = c.get('max_volume_by_symbol', {})
    if not isinstance(overrides, dict) or set(overrides) - set(INDEX_SYMBOLS):
        raise ValueError('Invalid index volume overrides')
    value = overrides.get(symbol, c['max_volume'])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid symbol volume cap')
    return value
