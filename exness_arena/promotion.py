"""Explicit entry stages and frozen user-selected shadow recipes.

Stages never override account, strategy, loss, position or broker gates.
"""
import hashlib
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path(__file__).with_name('promotion_policy.json')


def live_selected(cfg, bot, strategy):
    # Legacy configurations retain their behavior; deployed staged configs fail closed.
    stages = cfg.get('strategy_stages')
    if stages is None:
        return True
    return stages.get(bot, {}).get(strategy) == 'live_candidate'


def frozen_issue(bot, strategy, symbol, expected_policy=None, artifact_pins=None):
    raw = POLICY.read_bytes()
    if expected_policy is not None and hashlib.sha256(raw).hexdigest() != expected_policy:
        return 'Frozen promotion policy changed'
    policy = json.loads(raw)
    candidate = policy['candidates'].get(bot + '/' + strategy)
    if candidate is None:
        return 'No frozen promotion recipe'
    for name, expected in candidate['source_sha256'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return 'Frozen signal source changed: ' + name
    if bot == 'tradingbot':
        from .common import STATE
        folder = STATE/'artifacts'/'aurum_rules'/candidate['source_strategy']/symbol
        files = (artifact_pins or {}).get(candidate['source_strategy'], {}).get(symbol)
        if not files:
            return 'No frozen original artifact for this instrument'
        for name, expected in files.items():
            path = (folder/name).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return 'Frozen strategy artifact changed'
    return None


class RecipeGateway:
    """Expose the original signal identity, retain the derived execution owner."""
    def __init__(self, gateway, source, variant):
        self.gateway, self.strategy, self.variant = gateway, source, variant

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def order_send(self, request):
        # Existing owned positions retain their protection and close semantics.
        if request.get('position'):
            return self.gateway.order_send(request)
        from .common import settings
        from .research import plain, observe
        from .shadow_plan import candidates, size
        symbol = request['symbol']
        quote = plain(self.gateway.symbol_info_tick(symbol))
        try:
            config = settings()
            if (not quote or not all(isinstance(quote.get(k),(int,float)) and math.isfinite(quote[k]) and quote[k]>0 for k in ('bid','ask','time'))
                    or quote['ask']<quote['bid'] or time.time()-quote['time']>config['max_quote_age_seconds']):
                raise ValueError('Missing, invalid or stale recipe quote')
            proposal = dict(request)
            if self.variant == 'target_1r':
                # Preserve the winning legacy 1R recipe: normalize the original
                # stop to the broker grid before measuring its entry distance.
                contract = self.gateway.symbol_info(symbol)
                proposal['sl'] = round(round(float(proposal['sl'])/contract.trade_tick_size)*contract.trade_tick_size, contract.digits)
            chosen = next(c for c in candidates(proposal, quote) if c['variant'] == self.variant)
            selected = size(chosen, self.gateway, config, symbol,
                            self.gateway.symbol_info(symbol), request['volume'], quote)
        except (ValueError, TypeError, KeyError) as exc:
            return SimpleNamespace(retcode=10006, comment='Frozen recipe skipped: ' + str(exc), order=0, deal=0)
        observe(self.gateway, promotion_source_strategy=self.strategy, promotion_variant=self.variant,
                original_proposal=dict(request), recipe_quote=quote)
        transformed = dict(request, type=selected['side'], sl=selected['sl'], tp=selected['tp'], volume=selected['volume'])
        return self.gateway.order_send(transformed)


def recipe_cycle(source_cycle, source, variant):
    def cycle(gateway, symbol, rates):
        return source_cycle(RecipeGateway(gateway, source, variant), symbol, rates)
    return cycle
