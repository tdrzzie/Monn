from types import SimpleNamespace as S
import pytest
from exness_arena.universe import tradable_pairs


def symbol(name, base, profit, mode=4, path='Cent\\Forex'):
    return S(name=name,currency_base=base,currency_profit=profit,trade_mode=mode,path=path)


def test_discovery_includes_forex_metals_crypto_and_excludes_non_pairs():
    specs=[symbol('EURUSDc','EUR','USD'),symbol('XAUUSDc','XAU','USD'),
           symbol('BTCUSDc','BTC','USD',path='Cent\\Crypto'),
           symbol('US30c','USD','USD'),symbol('UKOILc','USD','USD'),
           symbol('ETHBTCc','ETH','BTC',mode=0),symbol('BTCUSDTc','BTC','TET',mode=3),
           symbol('EURUSDm','EUR','USD'),symbol('USDUSCc','USD','USC',mode=0)]
    assert tradable_pairs(specs)==['BTCUSDc','EURUSDc','XAUUSDc']


def test_discovery_failure_does_not_look_like_an_empty_account():
    with pytest.raises(RuntimeError):tradable_pairs(None)


def test_instruments_above_volume_cap_remain_monitored():
    # ETHUSDc's minimum volume is larger than the trial cap: scan it, let guard reject sizing.
    assert tradable_pairs([symbol('ETHUSDc','ETH','USD',path='Cent\\Crypto')])==['ETHUSDc']


def test_refresh_preserves_limits_baseline_and_owned_close_only_pair(tmp_path,monkeypatch):
    import json
    from exness_arena import common, universe
    monkeypatch.setattr(common,'STATE',tmp_path)
    monkeypatch.setattr(universe,'STATE',tmp_path)
    cfg={'discover_pairs':True,'symbols':['EURUSDc'],'symbol_map':{},'risk_budget_usc':3.5,
         'total_risk_budget_usc':7,'bot_loss_budget_usc':5.2444,'account_loss_fraction':.01,
         'start_epoch':123,'initial_equity_usc':1048.88}
    (tmp_path/'settings.json').write_text(json.dumps(cfg))
    class Gateway:
        SYMBOL_TRADE_MODE_FULL=4
        def symbols_get(self):return [symbol('AUDUSDc','AUD','USD'),symbol('EURUSDc','EUR','USD',mode=3)]
        def trial_position_symbols(self):return ['EURUSDc']
    assert universe.refresh(Gateway())==['AUDUSDc','EURUSDc']
    updated=common.settings()
    assert updated['discovered_pairs']==['AUDUSDc']
    for key in ['risk_budget_usc','total_risk_budget_usc','bot_loss_budget_usc','account_loss_fraction','start_epoch','initial_equity_usc']:
        assert updated[key]==cfg[key]
