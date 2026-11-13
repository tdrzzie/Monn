from types import SimpleNamespace as S
import pytest
from exness_arena import common, control
from exness_arena.guard import GuardError
from exness_arena.volume import entry_volume_cap
from exness_arena.tests.test_guard import setup, request, prepare

CAPS = {'USTECc': .05, 'US30c': .05, 'US500c': .14}


@pytest.mark.parametrize('symbol,cap', CAPS.items())
@pytest.mark.parametrize('side', [0, 1])
def test_index_minimum_accepted_only_within_stop_risk(setup, symbol, cap, side):
    g,n = setup
    g.config.update(max_volume=.01, max_volume_by_symbol=CAPS, risk_budget_usc=3.5, total_risk_budget_usc=None)
    g.config['symbols'].append(symbol)
    old = n.symbol_info
    n.symbol_info = lambda name: S(**{**vars(old(name)), 'volume_min': cap})
    n.order_calc_profit = lambda side,symbol,volume,entry,sl: -volume * 10
    sl = 1.0991 if side == 0 else 1.1011
    r = prepare(g,request(symbol=symbol,type=side,sl=sl,volume=entry_volume_cap(symbol,g.config)))
    assert r['volume'] == cap
    assert g.loss(side,symbol,r['volume'],r['price'],r['sl']) <= 3.5
    assert not n.sent
    # Even a minimum lot below the nominal cap is refused if it cannot fit the
    # unchanged 10% execution reserve and monetary risk budget.
    n.order_calc_profit = lambda side,symbol,volume,entry,sl: -volume * (3.6 / cap)
    with pytest.raises(GuardError,match='Minimum broker volume'):
        prepare(g,request(symbol=symbol,type=side,sl=sl,volume=cap))
    assert not n.sent


def test_index_overrides_do_not_raise_oil_or_forex_lots(setup):
    g,n = setup
    g.config.update(max_volume=.01,max_volume_by_symbol=CAPS,risk_budget_usc=3.5,total_risk_budget_usc=None)
    for symbol in ('EURUSDc','UKOILc','USOILc','XAUUSDc','BTCUSDc'):
        assert entry_volume_cap(symbol,g.config)==.01
    assert prepare(g,request(volume=.14))['volume']==.01


@pytest.mark.parametrize('bad',[0,-1,float('nan'),float('inf'),True,'0.14'])
def test_invalid_index_caps_fail_closed(setup,bad):
    g,n=setup;g.config['max_volume_by_symbol']={'US500c':bad}
    g.config['symbols'].append('US500c')
    with pytest.raises(GuardError,match='volume cap'):
        prepare(g,request(symbol='US500c'))


def test_control_changes_only_index_override_and_preserves_halt(setup,tmp_path,monkeypatch):
    import json
    g,n=setup
    monkeypatch.setattr(control,'STATE',tmp_path)
    monkeypatch.setattr(control,'native',n)
    n.initialize=lambda **kw: True
    n.shutdown=lambda: None
    g.config['terminal_path']='pinned-terminal'
    old=n.symbol_info
    n.symbol_info=lambda symbol:S(**{**vars(old(symbol)),'volume_min':CAPS[symbol]})
    before=dict(g.config)
    (tmp_path/'settings.json').write_text(json.dumps(before))
    (tmp_path/'STOP').touch()
    with common.locked() as db: db.execute("INSERT INTO flags VALUES('halt','existing loss halt')")
    control.enable_index_minimum_lots()
    after=common.settings()
    assert after.pop('max_volume_by_symbol')==CAPS
    assert after==before
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()[0]=='existing loss halt'
