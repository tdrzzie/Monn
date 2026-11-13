import json
import math
import time
from types import SimpleNamespace as S
import zlib

import pytest

from exness_arena import common, research
from exness_arena.tests.test_adapters import bars
from exness_arena.tests.test_guard import setup, request


@pytest.fixture
def lab(setup,tmp_path,monkeypatch):
    gateway,native=setup
    native.TIMEFRAME_M15=15
    native.copy_rates_from_pos=lambda *args:[]
    monkeypatch.setattr(research,'STATE',tmp_path)
    monkeypatch.setattr(research,'source_snapshot',lambda:{'manifest':{'source.py':'abc'},'git':{'test':'head1'}})
    gateway.config.update(risk_budget_usc=3.5,total_risk_budget_usc=None,max_volume=.01)
    (tmp_path/'settings.json').write_text(json.dumps(gateway.config))
    with common.locked() as db:db.execute("INSERT INTO flags VALUES('halt','account drawdown')")
    return gateway,native,tmp_path


def proposal(g,symbol,rates):
    research.observe(g,indicator_test=42)
    g.order_send(request(symbol=symbol,volume=.01))
    return 'qualifying test signal'


def test_paper_evaluation_records_exact_inputs_and_does_not_call_live_gateway(lab,monkeypatch):
    g,n,p=lab
    def forbidden(*args):raise AssertionError('A paper test reached real order_send')
    monkeypatch.setattr(g,'order_send',forbidden)
    result=research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    assert 'qualifying' in result and not n.sent
    with research.connect() as db:
        row=db.execute('SELECT * FROM research_decisions').fetchone()
        assert row['mode']=='paper_seed' and row['status']=='evaluated'
        assert json.loads(row['features'])['indicator_test']==42
        assert json.loads(row['context'])['live_flags']['halt']=='account drawdown'
        raw=db.execute('SELECT payload FROM inputs').fetchone()[0]
        assert json.loads(zlib.decompress(raw))['rows']==research.plain(bars().tolist())
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==3
        assert all(0<r[0]<=3.5 for r in db.execute('SELECT initial_risk FROM shadows'))
        cfg=json.loads(db.execute('SELECT config FROM versions').fetchone()[0])
        assert 'login' not in cfg and 'server' not in cfg
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()[0]=='account drawdown'
        assert db.execute('SELECT COUNT(*) FROM strategy_decisions').fetchone()[0]==0


def test_halted_live_attempt_is_logged_and_shadowed_without_bypassing_halt(lab):
    g,n,p=lab
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    assert not n.sent
    with research.connect() as db:
        row=db.execute('SELECT result FROM attempts').fetchone()
        assert 'halted' in json.loads(row[0])['comment']
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==3


def test_paper_restart_deduplicates_decisions_and_scenarios(lab):
    g,n,p=lab
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    assert 'already recorded' in research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==3


def test_observation_failure_does_not_repeat_or_change_live_call(lab,monkeypatch):
    g,n,p=lab
    with common.locked() as db:db.execute("DELETE FROM flags WHERE name='halt'")
    original=research.Recorder.finish
    def broken(*a,**k):raise RuntimeError('disk unavailable')
    monkeypatch.setattr(research.Recorder,'finish',broken)
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    assert len(n.sent)==1
    monkeypatch.setattr(research.Recorder,'finish',original)


def test_research_begin_failure_never_turns_paper_into_live(lab,monkeypatch):
    g,n,p=lab
    monkeypatch.setattr(research,'Recorder',lambda *a:(_ for _ in ()).throw(RuntimeError('bad DB')))
    with pytest.raises(RuntimeError,match='Paper research initialization'):
        research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    assert not n.sent


def test_versions_change_for_rule_or_source_change_but_not_credentials(lab,monkeypatch):
    g,n,p=lab
    with research.connect() as db:
        first=research.version(db,g.config)
        assert research.version(db,{**g.config,'login':999})==first
        assert research.version(db,{**g.config,'risk_budget_usc':1})!=first
        monkeypatch.setattr(research,'source_snapshot',lambda:{'manifest':{'source.py':'changed'},'git':{}})
        assert research.version(db,g.config)!=first


@pytest.mark.parametrize('side,o,h,l,sl,tp,expected',[
    (0,100,120,80,90,110,(90,'stop')),
    (1,100,120,80,110,90,(110,'stop')),
    (0,85,100,80,90,110,(85,'stop_gap')),
    (1,115,120,100,110,90,(115,'stop_gap')),
    (0,100,105,95,90,0,None),
])
def test_conservative_shadow_exit_order(side,o,h,l,sl,tp,expected):
    assert research.bar_exit(side,o,h,l,sl,tp)==expected


def shadow(lab):
    g,n,p=lab
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    with research.connect() as db:
        return dict(db.execute("SELECT * FROM shadows WHERE variant='proposed'").fetchone())


def test_shadow_does_not_use_pre_entry_or_unfinished_bar_extremes(lab):
    g,n,p=lab;row=shadow(lab)
    entered=1800000010
    row.update(opened=entered,last_bar=entered//900*900-900)
    raw=[{'time':entered//900*900,'open':1.1,'high':2,'low':.1,'close':1.1,'spread':10},
         {'time':entered//900*900+900,'open':1.1,'high':2,'low':.1,'close':1.1,'spread':10}]
    with research.connect() as db:
        research.advance(db,n,row,None,raw,observed=entered+950)
        assert db.execute('SELECT status FROM shadows WHERE id=?',(row['id'],)).fetchone()[0]=='open'


def test_shadow_closure_persists_and_live_order_is_never_used(lab):
    g,n,p=lab;row=shadow(lab)
    entered=1800000001;bar=math.ceil(entered/900)*900
    row.update(opened=entered,last_bar=bar-900)
    raw=[{'time':bar,'open':row['entry'],'high':row['tp']+.001,'low':row['sl']-.001,'close':row['entry'],'spread':10}]
    with research.connect() as db:
        research.advance(db,n,row,None,raw,observed=bar+900)
    with research.connect() as db:
        closed=db.execute('SELECT * FROM shadows WHERE id=?',(row['id'],)).fetchone()
        assert closed['status']=='closed' and closed['exit_reason']=='stop'
        assert closed['r_multiple']==pytest.approx(-1)
    assert not n.sent


def test_sampled_live_trades_keep_unknown_legacy_version(lab):
    g,n,p=lab
    d=S(ticket=100,position_id=25,magic=g.magic,symbol='EURUSDc',entry=0,order=25)
    pos=S(identifier=25,magic=g.magic,symbol='EURUSDc',profit=1,swap=-.1,sl=1.09,tp=1.12,volume=.01)
    research.collect(n,[pos],[d])
    with research.connect() as db:
        row=db.execute('SELECT * FROM live_context').fetchone()
        assert row['version_id'] is None and 'unknown' in row['attribution']
        assert db.execute('SELECT COUNT(*) FROM live_path').fetchone()[0]==1


def test_paper_rejects_minimum_lot_risk_over_budget(lab):
    g,n,p=lab
    n.order_calc_profit=lambda *a:-1000*a[2]
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==0
        assert 'Minimum volume' in db.execute('SELECT reason FROM shadow_rejections').fetchone()[0]


def test_no_signal_is_a_durable_decision_and_not_a_paper_trade(lab):
    g,n,p=lab
    research.evaluate(lambda *a:'No qualifying signal',g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    report=research.summary()
    assert report['counts']['research_decisions']==1
    assert report['counts']['attempts']==0 and not report['shadows']


def test_artifact_change_gets_distinct_version(lab):
    g,n,p=lab
    with research.connect() as db:
        assert research.version(db,g.config,'artifact-a')!=research.version(db,g.config,'artifact-b')


def test_real_opening_deal_is_linked_to_exact_observed_version(lab):
    g,n,p=lab
    with common.locked() as db:db.execute("DELETE FROM flags WHERE name='halt'")
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    d=S(ticket=200,position_id=9,magic=g.magic,symbol='EURUSDc',entry=0,order=9)
    research.collect(n,[],[d])
    with research.connect() as db:
        context=db.execute('SELECT * FROM live_context').fetchone()
        decision=db.execute('SELECT * FROM research_decisions').fetchone()
        assert context['version_id']==decision['version_id']
        assert context['decision_id']==decision['id'] and len(n.sent)==1


def test_rejected_paper_candidates_do_not_change_live_risk_config(lab):
    g,n,p=lab;before=(p/'settings.json').read_bytes()
    research.evaluate(lambda proxy,s,r:proxy.order_send(request(sl=0)),g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    assert (p/'settings.json').read_bytes()==before and not n.sent
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==0
        assert 'stop' in db.execute('SELECT reason FROM shadow_rejections').fetchone()[0]


def test_short_shadow_uses_ask_extremes_and_keeps_live_separate(lab):
    g,n,p=lab
    research.evaluate(lambda proxy,s,r:proxy.order_send(request(symbol=s,type=1,sl=1.101,volume=.01)),
                      g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    with research.connect() as db:
        row=dict(db.execute("SELECT * FROM shadows WHERE variant='proposed'").fetchone())
        row.update(opened=1800000001,last_bar=1800000000)
        bar=1800000900
        # Bid high is below SL, but estimated ask high crosses it.
        raw=[{'time':bar,'open':1.1,'high':1.10095,'low':1.1,'close':1.1,'spread':10}]
        research.advance(db,n,row,None,raw,observed=bar+900)
        assert db.execute('SELECT status FROM shadows WHERE id=?',(row['id'],)).fetchone()[0]=='closed'
    assert not n.sent


def test_exception_is_logged_without_retrying_a_live_call(lab,monkeypatch):
    g,n,p=lab;calls=[]
    def fail(r):calls.append(r);raise RuntimeError('native failed')
    monkeypatch.setattr(g,'order_send',fail)
    with pytest.raises(RuntimeError,match='native failed'):
        research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    assert len(calls)==1
    with research.connect() as db:
        assert db.execute('SELECT status FROM research_decisions').fetchone()[0]=='error'


def test_summary_does_not_merge_source_versions(lab,monkeypatch):
    g,n,p=lab
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    monkeypatch.setattr(research,'source_snapshot',lambda:{'manifest':{'source.py':'new'},'git':{}})
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    groups=research.summary()['shadows']
    assert len({r['version_id'] for r in groups})==2
    assert len(groups)==6 and all(r['count']==1 for r in groups)


@pytest.mark.parametrize('bid,ask',[(0,1.1),(1.1,1),(float('nan'),1.1)])
def test_invalid_quotes_cannot_open_paper_trades(lab,bid,ask):
    g,n,p=lab
    n.symbol_info_tick=lambda s:S(bid=bid,ask=ask,time=time.time())
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars(),paper=True)
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM shadow_rejections').fetchone()[0]==1


def test_paper_gateway_does_not_expose_other_native_mutations(lab):
    g,n,p=lab
    wrapper=research.ObservedGateway(g,S(attempts=[],symbol='EURUSDc'),paper=True)
    for name in ('initialize','login','shutdown','symbol_select','for_strategy','native'):
        with pytest.raises(AttributeError,match='does not expose'):
            getattr(wrapper,name)
