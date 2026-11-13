from types import SimpleNamespace as S
import numpy as np
import pandas as pd
import pytest
from exness_arena import adapters
from exness_arena.tests.test_adapters import bars, ReadGateway


@pytest.mark.parametrize('sign', [1, -1])
def test_native_donchian_stop_stays_at_original_entry_and_is_causal(monkeypatch,sign):
    monkeypatch.syspath_prepend(str(adapters.WORKSPACE/'tradingbot'))
    from aurum.strategies.trend import DonchianBreakout
    from aurum.features.volatility import atr
    rule=DonchianBreakout(entry_n=3,exit_n=2,atr_n=2,stop_atr=2.5)
    close=100 + sign*np.arange(14,dtype=float)
    frame=pd.DataFrame({'close':close,'high':close+.1,'low':close-.1})
    positions=rule.positions(frame); stops=rule.stop_levels(frame)
    first=np.flatnonzero(positions)[0]
    expected=close[first]-sign*2.5*atr(frame,2).iloc[first]
    assert positions[first]==sign
    assert np.isnan(stops[:first]).all()
    assert np.allclose(stops[first:],expected)
    for n in range(first+1,len(frame)):
        assert rule.stop_levels(frame.iloc[:n])[-1]==pytest.approx(stops[n-1])
        assert rule.positions(frame.iloc[:n])[-1]==positions[n-1]


@pytest.mark.parametrize('side,stop',[(0,1.09),(1,1.11)])
def test_donchian_adapter_preserves_native_stop_and_does_not_invent_tp(monkeypatch,side,stop):
    g=ReadGateway(bars());g.strategy='donchian'
    native=S(generate=lambda md:pd.Series([.8 if side==0 else -.8]),stop_levels=lambda b:np.array([stop]))
    monkeypatch.setattr(adapters,'aurum_artifact',lambda *a:S(strategies={'donchian':native}))
    monkeypatch.setattr(adapters,'aurum_data',lambda *a:S(bars=None))
    monkeypatch.setattr(adapters,'settings',lambda:{'max_volume':.01})
    adapters.aurum_cycle(g,'EURUSDc',g.data)
    r=g.sent[0]; half=(1.1001-1.1)/2
    assert r['sl']==pytest.approx(stop+(-half if side==0 else half))
    assert r['tp']==0 and r['type']==side


@pytest.mark.parametrize('stop',[float('nan'),float('inf'),0,-1])
def test_missing_native_stop_skips_instead_of_silent_fallback(monkeypatch,stop):
    g=ReadGateway(bars());g.strategy='donchian'
    native=S(generate=lambda md:pd.Series([.8]),stop_levels=lambda b:np.array([stop]))
    monkeypatch.setattr(adapters,'aurum_artifact',lambda *a:S(strategies={'donchian':native}))
    monkeypatch.setattr(adapters,'aurum_data',lambda *a:S(bars=None))
    assert 'skipped without fallback' in adapters.aurum_cycle(g,'EURUSDc',g.data)
    assert not g.sent
