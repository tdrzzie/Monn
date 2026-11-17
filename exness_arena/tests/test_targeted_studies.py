from types import SimpleNamespace as S
import json,pytest
from exness_arena import targeted_studies as study
from exness_arena.excursion_tests import new_trial


def source(side=0):
 return dict(id='s',decision_id='d',bot='tradingbot',strategy='bollinger_reversed',symbol='X',mode='test_only',version_id='v',side=side,volume=1,entry=100,sl=90 if side==0 else 110,tp=200 if side==0 else 20,initial_risk=1,opened=1000,
 features=json.dumps(dict(last_closed_bar=dict(time=0,close=100),sma_50=95,sma_200=90,atr_talib_14=.05)),context=json.dumps(dict(quote=dict(bid=99.9,ask=100),contract={})))


def trial(rule=None,side=0):
 s=source(side);c=dict(trade_tick_size=.01,volume_step=.01,volume_min=.01,volume_max=10,digits=2,point=.01,trade_stops_level=0)
 t=new_trial(s,dict(stop_r=1,target_r=1),'original',c,dict(risk_budget_usc=3.5,max_volume=1))
 t.update(id='t',source_id='s',variant=(rule or {}).get('key','original'),rule=rule or {},plan_sha256='p',risk_limit=3.5,tick_size=.01,digits=2,update_distance=.1,entry_filled=False)
 return t


def q(at,bid,ask):return dict(time_msc=int(at*1000),bid=bid,ask=ask)
def run(t,quotes):
 for quote in quotes:t=study.step(t,quote,[t])
 return t


@pytest.mark.parametrize('side',[0,1])
def test_entry_stop_arms_after_observation_and_fills_gap_at_quote(side):
 t=trial(dict(key='entry_stop_025r',arm_r=.25,lock_r=0),side)
 quotes=[q(1000.3,99.9,100),q(1001,103 if not side else 96.9,103.1 if not side else 97)]
 if side:quotes[0]=q(1000.3,100,100.1)
 t=run(t,quotes);assert t['status']=='OPEN' and t['sl']==100
 t=run(t,[q(1002,99.5 if not side else 100.4,99.6 if not side else 100.5)])
 assert t['status']=='CLOSED' and t['gross_usc']==pytest.approx(-.05)


def test_giveback_exit_uses_retraced_quote_not_peak():
 t=run(trial(dict(key='giveback',giveback_arm_r=.5,giveback_fraction=.5)),[q(1000.3,99.9,100),q(1001,106,106.1),q(1002,102,102.1)])
 assert t['reason']=='observed_giveback_market_exit' and t['exit_price']==102 and t['gross_usc']==pytest.approx(.2)


def test_trailing_never_widens_and_broker_distance_rejects_infeasible_updates():
 t=trial(dict(key='trail',arm_r=.5,trail_r=.25))
 t=run(t,[q(1000.3,99.9,100),q(1001,106,106.1)]);assert t['sl']==103.5
 t=run(t,[q(1002,107,107.1)]);assert t['sl']==104.5
 t=run(t,[q(1003,106,106.1)]);assert t['sl']==104.5
 t=trial(dict(key='entry',arm_r=.25,lock_r=0));t['update_distance']=10
 t=run(t,[q(1000.3,99.9,100),q(1001,103,103.1)])
 assert t['sl']==90 and t['stop_updates_rejected']==1


def test_elapsed_holding_limit_needs_observed_path_and_actual_fill_time():
 t=trial(dict(key='time',hold_seconds=3600))
 t=run(t,[q(1000.3,99.9,100)])
 assert t['deadline']==4600.3
 for at in range(1060,4660,60):t=run(t,[q(at+.3,100.1,100.2)])
 assert t['status']=='CLOSED' and t['reason']=='time'


def test_cap_counts_other_instruments_and_does_not_release_uncertain_exposure():
 t=trial(dict(key='cap',max_positions=2));a=dict(t,id='a',status='OPEN',symbol='A');b=dict(t,id='b',status='OPEN',symbol='B')
 assert study.admission(t,[t,a,b],1001)=='SKIPPED_CAP'
 b['status']='CLOSED';assert study.admission(t,[t,a,b],1001) is None
 b['status']='CENSORED';assert study.admission(t,[t,a,b],1001)=='UNKNOWN_EXPOSURE'
 b['mode']='live_evaluation';assert study.admission(t,[t,a,b],1001) is None


def test_regime_uses_pre_signal_inputs_and_missing_is_not_false():
 s=source();assert study.regime(dict(filter='trend_aligned'),s)
 assert not study.regime(dict(filter='trend_aligned'),dict(s,side=1))
 assert study.regime(dict(filter='atr_band',min_atr_fraction=.0002,max_atr_fraction=.005),s)
 f=json.loads(s['features']);del f['sma_200'];assert study.regime(dict(filter='trend_aligned'),dict(s,features=json.dumps(f))) is None
 f['last_closed_bar']['time']=1000;assert study.regime(dict(filter='atr_band',min_atr_fraction=0,max_atr_fraction=1),dict(s,features=json.dumps(f))) is None


def test_excluded_winner_is_opportunity_loss_and_unknown_costs_are_not_zero(tmp_path):
 base=trial();base.update(id='b',source_id='s',variant='original',status='CLOSED',gross_usc=2,net_usc=None)
 filtered=dict(base,id='f',variant='trend_aligned',status='FILTERED')
 with study.connect(tmp_path) as db:
  for r in [base,filtered]:db.execute('INSERT INTO trials VALUES(?,?,?,?,?,?)',(r['id'],r['source_id'],r['strategy'],r['variant'],r['status'],json.dumps(r)))
 r=next(r for r in study.summary(tmp_path)['comparisons'] if r['variant']=='trend_aligned')
 assert r['paired']==1 and r['gross_delta_usc']==-2 and r['net_pairs']==0 and r['net_delta_usc'] is None


def test_censored_or_stale_open_positions_keep_portfolio_unknown():
 t=trial();t.update(status='OPEN',entry_filled=True)
 sample=next(iter(study.sample_portfolios([t],{}, {},1001).values()))
 assert sample['equity_usc'] is None and sample['margin_usc'] is None and sample['net_equity_usc'] is None
 t['status']='CENSORED';sample=next(iter(study.sample_portfolios([t],{}, {},1001).values()))
 assert sample['equity_usc'] is None

def test_collection_admits_exposure_in_global_tick_order_not_symbol_batches(tmp_path,monkeypatch):
 import numpy as np
 monkeypatch.setattr(study.time,'time',lambda:1001.)
 plan={'id':'fixture','strategies':{'bollinger_reversed':[{'key':'portfolio_cap_2','max_positions':2}]}}
 path=tmp_path/'plan.json';path.write_text(json.dumps(plan));monkeypatch.setattr(study,'PLAN_PATH',path)
 contract=dict(trade_tick_size=.01,volume_step=.01,volume_min=.01,volume_max=10,digits=2,point=.01,trade_stops_level=0)
 with __import__('sqlite3').connect(tmp_path/'research.sqlite3') as db:
  db.executescript('CREATE TABLE research_decisions(id TEXT,mode TEXT,features TEXT,context TEXT);CREATE TABLE shadows(id TEXT,decision_id TEXT,bot TEXT,strategy TEXT,symbol TEXT,side INTEGER,volume REAL,entry REAL,sl REAL,tp REAL,initial_risk REAL,opened REAL,closed REAL,status TEXT,version_id TEXT,variant TEXT);')
  for symbol in ['A','B','C']:
   db.execute('INSERT INTO research_decisions VALUES(?,?,?,?)',(symbol,'test_only','{}',json.dumps({'contract':contract})))
   db.execute("INSERT INTO shadows VALUES(?,?,'tradingbot','bollinger_reversed',?,0,1,100,90,110,1,1000,NULL,'open','v','proposed')",(symbol,symbol,symbol))
 with study.connect(tmp_path) as db:
  db.execute('INSERT INTO meta VALUES(?,?)',('start:'+study.digest(plan),'0'));db.execute("INSERT INTO meta VALUES('last','900')")
 class Native:
  COPY_TICKS_ALL=0
  def copy_ticks_range(self,s,*args):
   at={'A':1000300,'B':1000400,'C':1000500}[s]
   return np.array([(at,99.9,100),(1001000,111,111.1)],dtype=[('time_msc','i8'),('bid','f8'),('ask','f8')])
  def order_send(self,*args):raise AssertionError('live orders forbidden')
 study.collect(Native(),tmp_path,dict(risk_budget_usc=3.5,max_volume=1))
 r=study.summary(tmp_path);cap=next(x for x in r['comparisons'] if x['variant']=='portfolio_cap_2')
 assert cap['total']==3 and cap['closed']==2 and cap['excluded']==1 and cap['paired']==3 and cap['gross_delta_usc']==-1
 assert cap['net_pairs']==0

def test_spread_filter_rejects_stale_and_future_quote_evidence():
 s=source();ctx=json.loads(s['context']);ctx['quote']['time']=999
 assert study.regime(dict(filter='spread',max_spread_r=.05),dict(s,context=json.dumps(ctx)))
 ctx['quote']['time']=1001;assert study.regime(dict(filter='spread',max_spread_r=.05),dict(s,context=json.dumps(ctx))) is None
 ctx['quote']['time']=900;assert study.regime(dict(filter='spread',max_spread_r=.05),dict(s,context=json.dumps(ctx))) is None


def test_ambiguous_same_millisecond_quotes_are_not_silently_skipped():
 import numpy as np
 dtype=[('time_msc','i8'),('bid','f8'),('ask','f8')]
 assert study.valid_path(np.array([(1000,100,101),(1000,100,101),(1001,101,102)],dtype=dtype))
 assert not study.valid_path(np.array([(1000,100,101),(1000,99,100)],dtype=dtype))
 assert not study.valid_path(np.array([(1001,100,101),(1000,100,101)],dtype=dtype))
