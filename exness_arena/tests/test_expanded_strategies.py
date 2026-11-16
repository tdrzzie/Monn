from pathlib import Path
from types import SimpleNamespace as S
import json

import numpy as np
import pandas as pd
import pytest
import talib

from exness_arena import common, identity, runner, expanded, adapters
from exness_arena.guard import Gateway, GuardError
from exness_arena.tests.test_guard import setup, prepare, request
from exness_arena.tests.test_adapters import bars, ReadGateway


NEW = [(bot,name) for bot in ['Monn','MT5-Python-Trading','tradingbot']
       for name,spec in identity.strategies(bot).items() if spec.get('adapter')]


def test_stream_compat_matches_full_series_last_values():
    from indicators import ta_compat
    prices=pd.Series(np.linspace(1,2,100)+np.sin(np.arange(100))*.01)
    assert ta_compat.SMA(prices,20)==talib.SMA(prices,20).iloc[-1]
    assert ta_compat.EMA(prices,20)==talib.EMA(prices,20).iloc[-1]
    assert ta_compat.RSI(prices,14)==talib.RSI(prices,14).iloc[-1]
    assert ta_compat.MACD(prices)==tuple(x.iloc[-1] for x in talib.MACD(prices))


@pytest.mark.parametrize('bot,strategy',NEW)
def test_every_new_strategy_obeys_same_shared_risk_cap(setup,bot,strategy):
    primary,n=setup
    cfg=dict(primary.config)
    cfg.update(enabled_bots=['Monn','MT5-Python-Trading','tradingbot'],risk_budget_usc=3.5,total_risk_budget_usc=7)
    cfg['symbols']=['EURUSDc','XAUUSDc']
    n.ps=[S(magic=26111302,type=0,symbol='EURUSDc',volume=.06,price_open=1.1,sl=1.099)]
    g=Gateway(n,bot,cfg,strategy=strategy)
    symbol=identity.strategies(bot)[strategy].get('symbols',['EURUSDc'])[0]
    with pytest.raises(GuardError,match='Minimum broker volume'):
        prepare(g,request(symbol=symbol))
    assert not n.sent


def test_dispatch_uses_library_adapters_not_primary_strategy_for_all_labels():
    assert runner.cycle_for('Monn','break_strategy')[1] is expanded.monn_library_cycle
    assert runner.cycle_for('MT5-Python-Trading','smc')[1] is expanded.python_library_cycle
    assert runner.cycle_for('Monn','ma_cross')[1] is runner.monn_cycle
    assert runner.cycle_for('tradingbot','rsi2')[1] is adapters.aurum_cycle


def test_gold_only_allocation_does_not_relabel_legacy_ema_positions():
    assert identity.resolve(26111305,'XAUUSDc')=='tradingbot/ema_cross'
    cfg={'enabled_strategies':{'tradingbot':list(identity.strategies('tradingbot'))}}
    assert 'managed_gold' not in identity.enabled_for_symbol('tradingbot','EURUSDc',cfg)
    assert 'managed_gold' in identity.enabled_for_symbol('tradingbot','XAUUSDc',cfg)


def test_pending_monn_order_is_never_converted_to_market(monkeypatch):
    from order import OrderType
    import strategy_utils
    class Rule:
        def is_params_valid(self):return True
        def set_volume(self,v):pass
        def set_max_sl_pct(self,v):pass
        def attach(self,f):pass
        def attach_trader(self,t):self.trader=t
        def update(self,tf):self.trader.create_trade(S(type=OrderType.LIMIT),.01)
    monkeypatch.setattr(expanded,'monn_definition',lambda s:{'params':{},'max_sl_pct':.3})
    monkeypatch.setattr(expanded,'settings',lambda:{'max_volume':.01})
    monkeypatch.setattr(strategy_utils,'load_strategy',lambda d:Rule())
    g=ReadGateway(bars());g.strategy='break_strategy'
    result=expanded.monn_library_cycle(g,'EURUSDc',g.data)
    assert 'Pending-order signal skipped' in result and not g.sent


def test_python_library_signal_has_server_stop_and_shared_sender(monkeypatch):
    monkeypatch.syspath_prepend(str(adapters.WORKSPACE/'MT5-Python-Trading'))
    from mt5_trading.domain.strategies.smc_strategy import SMCStrategy
    from mt5_trading.domain.signal import Signal
    monkeypatch.setattr(SMCStrategy,'signal',lambda self:('EURUSDc',Signal.BUY))
    monkeypatch.setattr(adapters,'settings',lambda:{'max_volume':.01})
    g=ReadGateway(bars());g.strategy='smc'
    expanded.python_library_cycle(g,'EURUSDc',g.data)
    assert len(g.sent)==1 and g.sent[0]['sl']>0 and g.sent[0]['tp']>0


def test_breakout_can_break_previous_range(monkeypatch):
    monkeypatch.syspath_prepend(str(adapters.WORKSPACE/'MT5-Python-Trading'))
    from mt5_trading.domain.strategies.trend_breakout_strategy import TrendBreakoutStrategy
    from mt5_trading.domain.signal import Signal
    frame=pd.DataFrame(bars())
    frame.loc[frame.index[-1],['close','high']]=[1.15,1.2]
    data=S(get_data=lambda:frame.copy(),get_symbol=lambda:'EURUSDc')
    rule=TrendBreakoutStrategy(data)
    monkeypatch.setattr(rule,'calculate_trend_strength',lambda df:30)
    assert rule.signal()[1]==Signal.BUY


def test_quarantine_blocks_only_failing_strategy_and_preserves_ownership(setup):
    primary,n=setup
    common.strategy_halt('Monn','break_strategy','test failure')
    bad=Gateway(n,'Monn',primary.config,strategy='break_strategy')
    with pytest.raises(GuardError,match='quarantined'):prepare(bad,request())
    assert prepare(primary,request())['magic']==primary.magic
    assert common.strategy_halt_reason('Monn','break_strategy')=='test failure'


def test_unconfigured_external_inputs_are_not_candidates():
    data=json.loads((common.PACKAGE/'compatibility.json').read_text())
    excluded=data['tradingbot']['excluded']
    assert {'macro_factor','risk_off','ml_gbm','meta_label','rl_ppo','regime_switch','intraday_seasonality'}<=set(excluded)
    assert len([x for x in NEW if not identity.strategies(x[0])[x[1]].get('source_strategy')])==19
    assert len(NEW)==22


def test_aurum_non_primary_artifact_is_separate_and_hash_verified(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(adapters.WORKSPACE/'tradingbot'))
    monkeypatch.setattr(adapters,'STATE',tmp_path)
    monkeypatch.setattr(adapters,'_artifacts',{})
    g=ReadGateway(bars());g.strategy='rsi2'
    artifact=adapters.aurum_artifact(g,'EURUSDc',g.data)
    assert list(artifact.strategies)==['rsi2']
    assert artifact.strategies['rsi2'].trainable is False
    assert (tmp_path/'artifacts/aurum_rules/rsi2/EURUSDc/manifest.json').exists()
