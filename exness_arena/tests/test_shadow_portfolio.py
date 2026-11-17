from exness_arena.shadow_portfolio import metrics,collect
from types import SimpleNamespace as S
import sqlite3,json

def sample(t,e,m=10):return dict(time=t,equity_usc=e,margin_usc=m)

def test_recovery_does_not_erase_drawdown_or_underwater():
 r=metrics([sample(0,0),sample(60,10),sample(120,-100000),sample(180,-10),sample(240,20)])
 assert r['sampled_max_drawdown_usc']==100010
 assert r['equity_usc']==20 and r['window_return_usc']==20
 assert r['longest_observed_underwater_seconds']==60
 assert r['current_underwater_seconds']==0

def test_gaps_and_missing_values_never_become_flat_equity():
 r=metrics([sample(0,0),sample(60,-10),sample(600,-20),sample(660,None,99)])
 assert r['sampled_max_drawdown_usc']==20 and r['longest_observed_underwater_seconds']==0
 assert r['current_underwater_seconds'] is None and r['incomplete_samples']==1
 assert r['peak_margin_usc']==99 and r['gaps']==2

def test_first_observation_is_window_baseline_not_invented_history():
 r=metrics([sample(100,-1000)])
 assert r['sampled_max_drawdown_usc']==0 and r['window_return_usc']==0
 assert not r['historical_equity_available']

def test_simultaneous_marks_margin_and_unknown_quotes(tmp_path):
 db=sqlite3.connect(tmp_path/'research.sqlite3')
 db.executescript('CREATE TABLE research_decisions(id TEXT,mode TEXT);CREATE TABLE shadows(id TEXT,decision_id TEXT,bot TEXT,strategy TEXT,plan_id TEXT,variant TEXT,status TEXT,symbol TEXT,side INTEGER,entry REAL,volume REAL,gross_usc REAL,version_id TEXT);')
 db.execute("INSERT INTO research_decisions VALUES('d','test_only')")
 for sid,entry in [('a',10),('b',20)]:db.execute("INSERT INTO shadows VALUES(?, 'd','bot','rule','plan','proposed','open','X',0,?,1,NULL,'v')",(sid,entry))
 db.commit();db.close()
 class Native:
  def symbol_info_tick(self,s):return S(bid=5,ask=6,time=100)
  def symbol_info(self,s):return S(trade_tick_size=1)
  def order_calc_profit(self,side,s,volume,entry,close):return (close-entry)*volume
  def order_calc_margin(self,*args):return 7
  def order_send(self,*args):raise AssertionError('orders forbidden')
 n=Native();r=collect(n,tmp_path,100)['portfolios'][0]
 assert r['equity_usc']==-20 and r['margin_usc']==14 and r['open_positions']==2
 assert r['sampled_max_drawdown_usc']==0
 n.symbol_info_tick=lambda s:None
 r=collect(n,tmp_path,200)['portfolios'][0]
 assert r['incomplete_samples']==1 and r['margin_usc'] is None
 assert r['current_underwater_seconds'] is None
