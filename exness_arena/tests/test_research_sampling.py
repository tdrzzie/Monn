import json
import sqlite3
from types import SimpleNamespace as S
import pytest
from exness_arena import research, research_sampling as sampling
from exness_arena.tests.test_research import lab, setup, shadow


def competing_writer(path):
    with sqlite3.connect(path/'research.sqlite3',timeout=0) as db:
        db.execute("INSERT INTO observations(time,kind,detail) VALUES(0,'competing_writer','{}')")


def test_broker_valuation_releases_writer_and_preserves_exit_engine(lab,monkeypatch):
    g,n,p=lab;row=shadow(lab)
    calc=n.order_calc_profit
    def profit(*args):
        competing_writer(p)
        return calc(*args)
    monkeypatch.setattr(n,'order_calc_profit',profit)
    quote=S(time=row['opened']+1,time_msc=int((row['opened']+1)*1000),bid=row['sl']-.001,ask=row['sl']-.0009)
    assert sampling.advance(n,row,quote,[],quote.time)
    with research.connect() as db:
        closed=dict(db.execute('SELECT * FROM shadows WHERE id=?',(row['id'],)).fetchone())
    assert closed['status']=='closed' and closed['exit_reason']=='stop_gap'
    assert closed['gross_usc']==pytest.approx(calc(0,row['symbol'],row['volume'],row['entry'],quote.bid))
    assert not n.sent


def test_concurrent_state_change_is_not_overwritten(lab,monkeypatch):
    g,n,p=lab;row=shadow(lab);calc=n.order_calc_profit
    def profit(*args):
        with research.connect() as db:
            db.execute("UPDATE shadows SET status='closed',exit_reason='other_sampler' WHERE id=?",(row['id'],))
        return calc(*args)
    monkeypatch.setattr(n,'order_calc_profit',profit)
    quote=S(time=row['opened']+1,time_msc=int((row['opened']+1)*1000),bid=row['sl']-.001,ask=row['sl']-.0009)
    assert not sampling.advance(n,row,quote,[],quote.time)
    with research.connect() as db:
        assert db.execute('SELECT exit_reason FROM shadows WHERE id=?',(row['id'],)).fetchone()[0]=='other_sampler'


def test_opening_risk_and_position_quotes_do_not_hold_write_lock(lab,monkeypatch):
    g,n,p=lab;row=shadow(lab)
    with research.connect() as db:
        db.execute("UPDATE research_decisions SET mode='live_evaluation' WHERE id=?",(row['decision_id'],))
        db.execute('UPDATE attempts SET result_order=25 WHERE decision_id=?',(row['decision_id'],))
        db.execute("UPDATE shadows SET status='closed'")
    deal=S(ticket=100,position_id=25,magic=g.magic,symbol='EURUSDc',entry=0,order=25,type=0,volume=.01,price=1.1001)
    pos=S(identifier=25,magic=g.magic,symbol='EURUSDc',profit=1,swap=-.1,sl=1.09,tp=1.12,volume=.01)
    calc=n.order_calc_profit;quote=n.symbol_info_tick;calls=[]
    def profit(*args):
        competing_writer(p);calls.append(args);return calc(*args)
    def tick(*args):
        competing_writer(p);return quote(*args)
    monkeypatch.setattr(n,'order_calc_profit',profit);monkeypatch.setattr(n,'symbol_info_tick',tick)
    sampling.collect(n,[pos],[deal]);sampling.collect(n,[pos],[deal])
    assert len(calls)==1  # Durable initial risk is not recalculated every collection.
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM live_deals').fetchone()[0]==1
        assert db.execute('SELECT decision_id FROM live_context').fetchone()[0]==row['decision_id']
        assert db.execute('SELECT risk_usc FROM live_initial_risk').fetchone()[0]>0
    assert not n.sent


def test_failed_preparation_leaves_shadow_state_and_events_untouched(lab,monkeypatch):
    g,n,p=lab;row=shadow(lab)
    def broken(db,*args):
        db.execute("UPDATE shadows SET status='closed' WHERE id=?",(row['id'],))
        raise RuntimeError('valuation failed')
    monkeypatch.setattr(research,'advance',broken)
    with pytest.raises(RuntimeError,match='valuation failed'):sampling.advance(n,row,None,[],row['opened']+1)
    with research.connect() as db:
        assert dict(db.execute('SELECT * FROM shadows WHERE id=?',(row['id'],)).fetchone())==row
