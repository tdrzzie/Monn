import time
from datetime import datetime, timezone
from types import SimpleNamespace as S
import pytest

from exness_arena import common, guard
from exness_arena.guard import Gateway, GuardError
from exness_arena.tracker import summarize


class Native:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    SYMBOL_TRADE_MODE_FULL = 4
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self):
        self.ps = []
        self.ds = []
        self.sent = []
        self.account = S(login=123, server="Exness-Test", currency="USC", margin_mode=2,
                         trade_allowed=True, trade_expert=True, equity=1000, margin_free=900)

    def account_info(self): return self.account
    def terminal_info(self): return S(connected=True, trade_allowed=True, tradeapi_disabled=False)
    def positions_get(self, *a, **kw): return self.ps
    def orders_get(self, *a, **kw): return []
    def history_deals_get(self, *a, **kw): return self.ds
    def symbol_info(self, name):
        return S(trade_tick_size=0.00001, digits=5, trade_stops_level=0, point=0.00001,
                 trade_mode=4, filling_mode=2, volume_min=0.01, volume_step=0.01, volume_max=200)
    def symbol_info_tick(self, name): return S(ask=1.1001, bid=1.1000, time=time.time())
    def order_calc_profit(self, side, symbol, volume, entry, sl):
        return (sl-entry) * volume * 100000 * (1 if side == 0 else -1)
    def order_calc_margin(self, *a): return 1.0
    def order_check(self, r): return S(retcode=0)
    def order_send(self, r):
        self.sent.append(r)
        return S(retcode=10009, order=9, _asdict=lambda: {"retcode": 10009})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "STATE", tmp_path)
    monkeypatch.setattr(guard, "STATE", tmp_path)
    cfg = {"login": 123, "server": "Exness-Test", "live": True,
           "enabled_bots": ["Monn", "MT5-Python-Trading"], "initial_equity_usc": 1000,
           "account_loss_fraction": 0.01, "start_epoch": time.time()-100,
           "risk_budget_usc": 2, "bot_loss_budget_usc": 10, "total_risk_budget_usc": 4,
           "max_entries_per_bot": 10, "max_volume": 0.1, "max_quote_age_seconds": 30,
           "symbols": ["EURUSDc"], "symbol_map": {"EURUSD": "EURUSDc"}}
    common.health("tracker", "running")
    n = Native()
    return Gateway(n, "Monn", cfg), n


def request(**kw):
    return {"action": 1, "symbol": "EURUSD", "type": 0, "sl": 1.0991, "volume": 0.1, **kw}


def prepare(g, r):
    with common.locked() as db:
        return g._prepare(db, r)


def test_cent_sizing_rounds_down_and_assigns_owner(setup):
    g,n = setup
    r = prepare(g,request(magic=999))
    assert r["magic"] == 26111301 and r["symbol"] == "EURUSDc"
    assert r["volume"] == 0.01
    assert g.loss(0,r["symbol"],r["volume"],r["price"],r["sl"]) <= 2


@pytest.mark.parametrize("changes", [{"sl": 0}, {"sl": 1.2}, {"volume": float('nan')}, {"action": 5}, {"symbol": "BTCUSD"}])
def test_bad_orders_fail_closed(setup, changes):
    g,n = setup
    with pytest.raises(GuardError): prepare(g,request(**changes))
    assert not n.sent


def test_minimum_volume_never_rounded_up(setup):
    g,n = setup
    g.config["risk_budget_usc"] = 0.01
    with pytest.raises(GuardError, match="Minimum broker volume"): prepare(g,request())


def test_foreign_position_cannot_be_closed_or_modified(setup):
    g,n = setup
    n.ps = [S(ticket=1, magic=0, symbol="EURUSDc")]
    for action in [1,6]:
        with pytest.raises(GuardError, match="another bot"):
            prepare(g,{"action":action,"position":1})


def test_own_position_queries_exclude_other_bots(setup):
    g,n = setup
    n.ps = [S(magic=0), S(magic=26111301), S(magic=26111302)]
    assert [p.magic for p in g.positions_get()] == [26111301]


def test_missing_positions_are_not_treated_as_empty(setup):
    g,n = setup
    n.ps = None
    with pytest.raises(GuardError): g.positions_get()


def test_wrong_account_blocks(setup):
    g,n = setup
    n.account.login=456
    with pytest.raises(GuardError,match="pinned"): prepare(g,request())


def test_stale_tracker_blocks_entries_but_not_owned_exit(setup):
    g,n=setup
    with common.locked() as db: db.execute("DELETE FROM health")
    with pytest.raises(GuardError,match="heartbeat"): prepare(g,request())
    n.ps=[S(ticket=1,magic=g.magic,symbol="EURUSDc",volume=0.01,type=0,sl=1.09)]
    assert prepare(g,{"action":1,"position":1})["type"]==1


def test_cannot_widen_or_remove_protection(setup):
    g,n=setup
    n.ps=[S(ticket=1,magic=g.magic,symbol="EURUSDc",volume=0.01,type=0,sl=1.09,tp=1.12)]
    for sl in [0,1.08]:
        with pytest.raises(GuardError): prepare(g,{"action":6,"position":1,"sl":sl})
    assert prepare(g,{"action":6,"position":1,"sl":1.10})["tp"]==1.12


def test_one_position_per_bot_and_account_halt(setup):
    g,n=setup
    n.ps=[S(magic=g.magic)]
    with pytest.raises(GuardError,match="One-position"): prepare(g,request())
    n.ps=[]; n.account.equity=980
    with pytest.raises(GuardError,match="drawdown"): prepare(g,request())


def test_order_denial_has_usable_result(setup):
    g,n=setup
    result=g.order_send(request(sl=0))
    assert result.retcode==10006 and result._asdict()["request"]._asdict()["sl"]==0
    assert not n.sent


def test_manual_exit_and_partial_closes_keep_opening_repo_attribution():
    ds=[S(ticket=1,time_msc=1,entry=0,magic=26111301,position_id=11,type=0,profit=0,swap=0,commission=-1,fee=0),
        S(ticket=2,time_msc=2,entry=1,magic=0,position_id=11,type=1,profit=5,swap=-1,commission=-1,fee=-0.5),
        S(ticket=3,time_msc=3,entry=1,magic=0,position_id=11,type=1,profit=2,swap=0,commission=0,fee=0)]
    rows,_=summarize(ds,[],{}, {"risk_budget_usc":2})
    row=next(r for r in rows if r["repo"]=="Monn")
    assert row["closed_positions"]==1 and row["closed_net_usc"]==3.5
    assert row["total_net_usd"]==0.035


def test_live_false_blocks_even_owned_closes(setup):
    g,n=setup; g.config["live"]=False
    with pytest.raises(GuardError,match="disabled"): prepare(g,{"action":1,"position":1})


def test_stale_quotes_and_failed_broker_valuation_block(setup):
    g,n=setup
    original=n.symbol_info_tick
    n.symbol_info_tick=lambda name:S(ask=1.1001,bid=1.1,time=time.time()-60)
    with pytest.raises(GuardError,match="Stale"): prepare(g,request())
    n.symbol_info_tick=original
    n.order_calc_profit=lambda *a:None
    with pytest.raises(GuardError,match="valuation"): prepare(g,request())


def test_total_risk_cap_includes_other_trial_bot(setup):
    g,n=setup
    n.ps=[S(magic=26111302,type=0,symbol='EURUSDc',volume=0.04,price_open=1.1,sl=1.099)]
    with pytest.raises(GuardError,match="Minimum broker volume"): prepare(g,request())


def test_uncertain_submission_halts_without_retry(setup):
    g,n=setup
    n.order_send=lambda r:None
    n.last_error=lambda:'unknown'
    result=g.order_send(request())
    assert result.retcode==10006
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()
    with pytest.raises(GuardError,match="halted"): prepare(g,request())


def test_submission_exception_persists_halt(setup):
    g,n=setup
    def fail(r): raise RuntimeError('connection lost')
    n.order_send=fail
    result=g.order_send(request())
    assert 'halted without retry' in result.comment
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()


def test_broker_preflight_rejection_sends_nothing(setup):
    g,n=setup
    n.order_check=lambda r:S(retcode=10016,comment='invalid stops')
    assert g.order_send(request()).retcode==10006
    assert not n.sent


def test_restart_deduplication_keeps_one_decision_per_closed_bar(setup):
    assert common.claim_bar('Monn','EURUSDc',123)
    assert not common.claim_bar('Monn','EURUSDc',123)
    assert common.claim_bar('MT5-Python-Trading','EURUSDc',123)


def test_actual_monn_adapter_uses_broker_state_after_restart(setup,monkeypatch):
    import numpy as np
    from exness_arena import runner
    g,n=setup
    monkeypatch.setattr(runner,'settings',lambda:{'max_volume':0.1})
    values=np.linspace(1.09,1.1,300)
    rates=np.array([(i,float(v-.00001),float(v+.0001),float(v-.0001),float(v),100)
                    for i,v in enumerate(values)],dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8')])
    observed=[]
    g.order_send=lambda r:observed.append(r) or S(order=9)
    runner.monn_cycle(g,'EURUSDc',rates)
    assert len(observed)==1 and observed[0]['type']==0 and observed[0]['sl']>0
