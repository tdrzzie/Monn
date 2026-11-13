import json
from types import SimpleNamespace as S

import pytest

from exness_arena import research
from exness_arena.shadow_plan import PLAN_ID, LEGACY_PLAN, candidates, size, tighten
from exness_arena.tests.test_research import lab, setup, proposal
from exness_arena.tests.test_adapters import bars
from exness_arena.tests.test_guard import request


def create(lab, tp=1.1021):
    g,n,p=lab
    research.evaluate(lambda proxy,s,r:proxy.order_send(request(symbol=s,volume=.01,tp=tp)),
                      g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    return g,n,p


def test_six_variants_and_independently_sized_reverse(lab):
    g,n,p=create(lab)
    with research.connect() as db:
        rows={r['variant']:dict(r) for r in db.execute('SELECT * FROM shadows')}
        assert len(rows)==6
        f,r=rows['proposed'],rows['reversed_swap']
        assert r['side']==1 and r['entry']==1.1
        assert r['sl']==f['tp'] and r['tp']==f['sl']
        assert all(0<x['initial_risk']<=3.5*.9 for x in rows.values())
        assert all(x['plan_id']==PLAN_ID for x in rows.values())
    assert not n.sent


def test_missing_tp_rejection_does_not_discard_forward_tests(lab):
    create(lab,tp=0)
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==5
        rejected=db.execute('SELECT * FROM shadow_variant_rejections').fetchone()
        assert rejected['variant']=='reversed_swap' and 'no valid fixed TP' in rejected['reason']


@pytest.mark.parametrize('side,sl,tp',[(0,1.0991,1.1101),(1,1.101,1.09)])
def test_inverse_too_expensive_is_rejected_without_raising_budget(lab,side,sl,tp):
    g,n,p=lab
    quote={'bid':1.1,'ask':1.1001}
    inverse=list(candidates(request(type=side,sl=sl,tp=tp),quote))[-1]
    with pytest.raises(ValueError,match='Minimum volume'):
        size(inverse,n,g.config,'EURUSDc',n.symbol_info('EURUSDc'),.01,quote)
    assert g.config['risk_budget_usc']==3.5 and not n.sent


@pytest.mark.parametrize('side,entry,sl,tp,expected',[(0,100,90,120,(1,120,90)),(1,100,110,80,(0,80,110))])
def test_reversal_direction_and_level_swap(side,entry,sl,tp,expected):
    inverse=list(candidates({'type':side,'sl':sl,'tp':tp},{'bid':entry,'ask':entry}))[-1]
    assert (inverse['side'],inverse['sl'],inverse['tp'])==expected


def virtual(lab,variant='mfe_lock_05r',side=0):
    g,n,p=create(lab,tp=0)
    with research.connect() as db:
        row=dict(db.execute('SELECT * FROM shadows WHERE variant=?',(variant,)).fetchone())
        row.update(side=side,entry=100.,sl=90. if side==0 else 110.,initial_sl=90. if side==0 else 110.,
                   initial_risk=10.,volume=1.,opened=1800000000.,last_bar=1799999100,
                   sl_effective_epoch=1800000000.,policy_state=json.dumps({'peak':100.,'armed':False}))
        db.execute('UPDATE shadows SET side=?,entry=?,sl=?,initial_sl=?,initial_risk=?,volume=?,opened=?,last_bar=?,sl_effective_epoch=?,policy_state=? WHERE id=?',
                   tuple(row[k] for k in ('side','entry','sl','initial_sl','initial_risk','volume','opened','last_bar','sl_effective_epoch','policy_state','id')))
    n.order_calc_profit=lambda side,symbol,volume,entry,exit:(exit-entry)*volume*(1 if side==0 else -1)
    n.symbol_info=lambda s:S(point=.01,trade_tick_size=.01,digits=2)
    return row,n


def read(db,row):
    return dict(db.execute('SELECT * FROM shadows WHERE id=?',(row['id'],)).fetchone())


@pytest.mark.parametrize('side,peak,retrace',[(0,106.,99.),(1,94.,101.)])
def test_quote_arm_then_retrace_and_restart_preserves_stop(lab,side,peak,retrace):
    row,n=virtual(lab,side=side)
    with research.connect() as db:
        t=row['opened']+10
        research.advance(db,n,row,S(time=t,time_msc=t*1000,bid=peak,ask=peak),[],observed=t)
        armed=read(db,row)
        assert armed['status']=='open' and armed['sl']==100
    with research.connect() as db:
        research.advance(db,n,armed,S(time=t+10,bid=retrace,ask=retrace),[],observed=t+10)
        closed=read(db,row)
        assert closed['status']=='closed' and closed['exit_price']==retrace
        count=db.execute('SELECT COUNT(*) FROM shadow_events WHERE kind=\'closed\'').fetchone()[0]
        research.advance(db,n,closed,None,[],observed=t+20)
        assert db.execute('SELECT COUNT(*) FROM shadow_events WHERE kind=\'closed\'').fetchone()[0]==count


@pytest.mark.parametrize('side,high,low,expected',[(0,115,99,110),(1,101,85,90)])
def test_trailing_stop_uses_initial_distance_and_never_widens(lab,side,high,low,expected):
    row,n=virtual(lab,'mfe_trail_1r',side)
    state={}
    assert tighten(row,state,high,low,1,n.symbol_info('x'))==expected
    row['sl']=expected
    assert tighten(row,state,101,99,2,n.symbol_info('x')) is None
    row['plan_id']=LEGACY_PLAN
    assert tighten(row,state,150,50,3,n.symbol_info('x')) is None


def test_bar_stop_first_and_no_retrospective_mfe_fill(lab):
    row,n=virtual(lab)
    with research.connect() as db:
        raw=[dict(time=int(row['opened']),open=100,high=107,low=89,close=102,spread=0)]
        research.advance(db,n,row,None,raw,observed=row['opened']+900)
        result=read(db,row)
        assert result['exit_reason']=='stop' and result['exit_price']==90
        assert not json.loads(result['policy_state']).get('armed')


def test_bar_peak_known_at_close_exits_at_close_not_invented_entry(lab):
    row,n=virtual(lab)
    with research.connect() as db:
        raw=[dict(time=int(row['opened']),open=100,high=107,low=98,close=99,spread=0)]
        research.advance(db,n,row,None,raw,observed=row['opened']+900)
        result=read(db,row)
        assert result['exit_reason']=='mfe_retrace_observed_close' and result['exit_price']==99


def test_stop_activated_mid_bar_does_not_use_earlier_low(lab):
    row,n=virtual(lab)
    with research.connect() as db:
        t=row['opened']+100
        research.advance(db,n,row,S(time=t,bid=106,ask=106),[],observed=t)
        row=read(db,row)
        raw=[dict(time=int(row['opened']),open=100,high=107,low=98,close=104,spread=0)]
        research.advance(db,n,row,None,raw,observed=row['opened']+900)
        assert read(db,row)['status']=='open'


def test_comparisons_only_match_same_attempt_and_report_pending(lab):
    create(lab)
    with research.connect() as db:
        db.execute("UPDATE shadows SET status='closed',r_multiple=-1,gross_usc=-1 WHERE variant='proposed'")
        db.execute("UPDATE shadows SET status='closed',r_multiple=.5,gross_usc=.5 WHERE variant='mfe_lock_05r'")
        db.execute("UPDATE shadows SET plan_id=? WHERE variant='reversed_swap'",(LEGACY_PLAN,))
    comparisons={r['variant']:r for r in research.summary()['comparisons']}
    assert comparisons['mfe_lock_05r']['delta_mean_r']==1.5
    assert comparisons['mfe_trail_1r']['pending_pairs']==1
    assert comparisons['mfe_trail_1r']['delta_mean_r'] is None
    assert comparisons['reversed_swap']['baseline_missing']==1
    assert comparisons['reversed_swap']['paired_closed']==0


def test_legacy_schema_migrates_without_reinterpreting_old_observations(lab):
    import sqlite3
    g,n,p=lab
    with sqlite3.connect(p/'research.sqlite3') as db:
        db.execute('''CREATE TABLE shadows(id TEXT PRIMARY KEY, decision_id TEXT, ordinal INTEGER, variant TEXT,
          bot TEXT,strategy TEXT,symbol TEXT,version_id TEXT,side INTEGER,volume REAL,entry REAL,sl REAL,tp REAL,
          initial_risk REAL,opened REAL,last_bar INTEGER,status TEXT,closed REAL,exit_price REAL,exit_reason TEXT,
          gross_usc REAL,r_multiple REAL,mfe_r REAL DEFAULT 0,mae_r REAL DEFAULT 0)''')
        db.execute("INSERT INTO shadows(id,variant,sl,status,gross_usc) VALUES('old','proposed',90,'closed',-2)")
    with research.connect() as db:
        old=dict(db.execute("SELECT * FROM shadows WHERE id='old'").fetchone())
        assert old['plan_id']==LEGACY_PLAN and old['gross_usc']==-2 and old['sl']==90
        assert old['initial_sl'] is None and old['policy_state']=='{}'
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==1


def test_collector_releases_writer_lock_between_independent_shadows(lab,monkeypatch):
    import sqlite3
    g,n,p=create(lab)
    original=research.advance
    calls=[]
    def checked(db,native,row,*args):
        if calls:
            with sqlite3.connect(p/'research.sqlite3',timeout=.1) as other:
                other.execute("INSERT INTO observations(time,kind,detail) VALUES(0,'concurrent_writer','ok')")
        calls.append(row['id'])
        return original(db,native,row,*args)
    monkeypatch.setattr(research,'advance',checked)
    research.collect(n,[],[])
    assert len(calls)==6
    with research.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations WHERE kind='concurrent_writer'").fetchone()[0]==5
        assert db.execute("SELECT COUNT(*) FROM observations WHERE kind='shadow_mark_error'").fetchone()[0]==0


def test_slow_broker_sizing_never_holds_research_writer_lock(lab,monkeypatch):
    import sqlite3
    g,n,p=lab
    original=n.order_calc_profit
    def checked(*args):
        with sqlite3.connect(p/'research.sqlite3',timeout=.1) as other:
            other.execute("INSERT INTO observations(time,kind,detail) VALUES(0,'during_broker_read','ok')")
        return original(*args)
    monkeypatch.setattr(n,'order_calc_profit',checked)
    create(lab)
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==6
        assert db.execute("SELECT COUNT(*) FROM observations WHERE kind='during_broker_read'").fetchone()[0]==12
