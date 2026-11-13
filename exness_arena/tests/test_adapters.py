import sys
from pathlib import Path
from types import SimpleNamespace as S

import numpy as np
import pytest

from exness_arena import adapters
from exness_arena.guard import Gateway
from exness_arena.tests.test_guard import setup, prepare, request


def bars(count=600):
    price=1.1 + np.arange(count)*.000001 + np.sin(np.arange(count)/8)*.0003
    return np.array([(1700000000+i*900,p-.00001,p+.0001,p-.0001,p,100,10,0) for i,p in enumerate(price)],
        dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8'),('spread','i8'),('real_volume','i8')])


class ReadGateway:
    TIMEFRAME_M15=15
    TRADE_ACTION_DEAL=1
    def __init__(self,data):
        self.data=data
        self.sent=[]
    def symbol_info(self,symbol):return S(point=.00001,digits=5)
    def copy_rates_from_pos(self,*args):
        row=self.data[-1:].copy();row['time']+=900;row['open']=1.1
        return row
    def symbol_info_tick(self,symbol):return S(bid=1.1,ask=1.1001)
    def order_send(self,r):
        self.sent.append(r)
        return S(retcode=10009,comment='test',order=1)


def test_no_order_gateway_never_submits_a_real_request():
    native=ReadGateway(bars())
    dry=adapters.NoOrderGateway(native)
    result=dry.order_send({'action':1})
    assert result.retcode==10006 and dry.proposals==[{'action':1}] and not native.sent


def test_forex_profile_preserves_original_entry_stop_and_target(monkeypatch):
    data=bars();data['low'][-1]=1.09
    g=ReadGateway(data)
    monkeypatch.setattr(adapters,'source_module',lambda *a:S(Indicator=lambda f:(1.12,1.095,1.11)))
    monkeypatch.setattr(adapters,'settings',lambda:{'max_volume':.01})
    adapters.forex_cycle(g,'EURUSDc',data)
    assert len(g.sent)==1
    assert g.sent[0]['type']==0 and g.sent[0]['tp']==1.11
    assert g.sent[0]['sl']==pytest.approx(1.1-(1.11-1.1)/1.2)


def test_forex_forming_bar_extremes_cannot_change_closed_bar_signal(monkeypatch):
    data=bars();data['low'][-1]=1.09
    seen=[]
    def indicator(f):
        seen.append(tuple(f.iloc[-1][k] for k in ('open','high','low','close')))
        return 1.12,1.095,1.11
    monkeypatch.setattr(adapters,'source_module',lambda *a:S(Indicator=indicator))
    monkeypatch.setattr(adapters,'settings',lambda:{'max_volume':.01})
    g=ReadGateway(data)
    adapters.forex_cycle(g,'EURUSDc',data)
    assert seen==[(1.1,1.1,1.1,1.1)]


def test_original_forex_indicator_is_finite_and_supports_numeric_nan():
    module=adapters.source_module('test_original_forex',adapters.WORKSPACE/'Forex-Trading-Bot'/'DataProcessing.py')
    import pandas as pd
    assert module.nz(float('nan'),2)==2
    assert all(np.isfinite(x) for x in module.Indicator(pd.DataFrame(bars())))


def test_aurum_native_artifact_is_verified_and_signal_uses_shared_gateway(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(adapters.WORKSPACE/'tradingbot'))
    monkeypatch.setattr(adapters,'STATE',tmp_path)
    monkeypatch.setattr(adapters,'_artifacts',{})
    monkeypatch.setattr(adapters,'settings',lambda:{'max_volume':.01})
    g=ReadGateway(bars())
    artifact=adapters.aurum_artifact(g,'EURUSDc',g.data)
    assert artifact.symbol=='EURUSDc' and artifact.max_lookback==385
    assert artifact.strategies['ema_cross'].is_fitted
    assert (tmp_path/'artifacts/aurum_ema/EURUSDc/manifest.json').exists()
    dry=adapters.NoOrderGateway(g)
    adapters.aurum_cycle(dry,'EURUSDc',g.data)
    assert not g.sent
    # Force a confident forecast only to verify execution routing, not strategy profitability.
    import pandas as pd
    artifact.strategies['ema_cross'].generate=lambda md:pd.Series(.8,index=md.bars.index)
    adapters.aurum_cycle(dry,'EURUSDc',g.data)
    assert dry.proposals and dry.proposals[-1]['sl']>0


def test_original_sunrise_replay_is_repeatable_and_never_uses_placeholder_adapter():
    first=adapters.sunrise_signals('EURUSDc',bars())
    second=adapters.sunrise_signals('EURUSDc',bars())
    assert first==second and 'Original Sunrise state=' in first[1]
    assert adapters.sunrise_signals('BTCUSDc',bars())[0]==[]


@pytest.mark.parametrize('bot',['tradingbot','Forex-Trading-Bot','mt5_live_trading_bot'])
def test_new_profiles_get_the_same_magic_isolation_and_risk_sizing(setup,bot):
    old,n=setup
    cfg=dict(old.config);cfg['enabled_bots']=cfg['enabled_bots']+[bot]
    g=Gateway(n,bot,cfg)
    prepared=prepare(g,request(magic=0))
    assert prepared['magic']==g.magic and prepared['magic']!=0
    assert prepared['volume']==.01
    assert g.loss(0,'EURUSDc',prepared['volume'],prepared['price'],prepared['sl'])<=cfg['risk_budget_usc']
