"""Stable strategy ownership, including historical repo-level magic numbers."""
from .common import REGISTRY


def strategies(bot):
    spec = REGISTRY[bot]
    return spec.get('strategies', {'default': {'magic': spec['magic']}})


def default_strategy(bot):
    return REGISTRY[bot].get('default_strategy', next(iter(strategies(bot))))


def strategy_for_symbol(bot, symbol):
    if REGISTRY[bot].get('runner') == 'sunrise':
        for name, spec in strategies(bot).items():
            if symbol in spec.get('symbols', []):
                return name
    return default_strategy(bot)


def strategy_key(bot, strategy):
    return bot + '/' + strategy


def specs():
    return {strategy_key(bot, name): {**spec, 'repo': bot, 'strategy': name,
            'runner': REGISTRY[bot].get('runner')} for bot in REGISTRY for name, spec in strategies(bot).items()}


def resolve(magic, symbol=None):
    # Legacy Sunrise orders used one magic across all pair-specific parameter profiles.
    for bot, repo in REGISTRY.items():
        if magic == repo['magic']:
            return strategy_key(bot, strategy_for_symbol(bot, symbol))
    for key, spec in specs().items():
        if magic == spec['magic']:
            return key
    return None


def trial_magics():
    return {v['magic'] for v in REGISTRY.values()} | {v['magic'] for v in specs().values()}


def enabled_for_symbol(bot, symbol, cfg):
    enabled = cfg.get('enabled_strategies', {}).get(bot, list(strategies(bot)))
    return [name for name, spec in strategies(bot).items() if name in enabled
            and (not spec.get('symbols') or symbol in spec['symbols'])]
