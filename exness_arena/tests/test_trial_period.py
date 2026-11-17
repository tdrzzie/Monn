from datetime import datetime, timezone
from types import SimpleNamespace as S
import pytest

from exness_arena.tests.test_guard import setup, prepare, request
from exness_arena.tracker import summarize


def test_new_trial_loss_window_preserves_historical_attribution(setup):
    g,n=setup
    old=S(ticket=1,time_msc=1000,entry=0,magic=g.magic,position_id=11,type=0,
          profit=-20,swap=0,commission=0,fee=0,symbol='EURUSDc')
    g.config.update(start_epoch=2000,history_start_epoch=0)
    queried=[]
    def history(start,end):
        queried.append(start.timestamp())
        return [old] if start.timestamp()<1 else []
    n.history_deals_get=history
    prepare(g,request())
    assert queried==[2000]
    historical=n.history_deals_get(datetime.fromtimestamp(g.config['history_start_epoch'],timezone.utc),datetime.now(timezone.utc))
    row=next(r for r in summarize(historical,[],{},g.config)[0] if r['repo']=='Monn')
    assert row['closed_net_usc']==-20
    assert not n.sent


def test_fresh_equity_baseline_keeps_drawdown_and_new_losses_enforced(setup):
    g,n=setup;n.account.equity=946.68
    with pytest.raises(RuntimeError,match='drawdown'):prepare(g,request())
    g.config['initial_equity_usc']=946.68
    prepare(g,request())
    n.account.equity=937
    with pytest.raises(RuntimeError,match='drawdown'):prepare(g,request())
    assert not n.sent
