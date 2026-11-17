from exness_arena.shadow_costs import costs
from copy import deepcopy

def fixture():
 s=dict(decision_id='d',symbol='X',side=0,volume=1,entry=10,opened=100.1,status='closed',exit_price=12,closed=200.1,gross_usc=2)
 ds=[dict(entry=0,type=0,symbol='X',volume=1,price=10,time=100,profit=0,commission=-.2,fee=0,swap=0),dict(entry=1,type=1,symbol='X',volume=1,price=12,time=200,profit=2,commission=-.2,fee=0,swap=-.1)]
 return s,{'d':[1]},{1:ds}

def test_exact_lifecycle_observed_cash_components():
 s,c,d=fixture();r=costs(s,c,d)
 assert r['complete'] and r['commission_usc']==-.4 and r['swap_usc']==-.1 and r['slippage_adjustment_usc']==0
 assert s['gross_usc']+r['commission_usc']+r['swap_usc']==1.5

def test_absence_is_unknown_not_zero():
 s,c,d=fixture();r=costs(s,{},d)
 assert not r['complete'] and r['commission_usc'] is None and r['swap_usc'] is None and r['slippage_adjustment_usc'] is None

def test_changed_exit_cannot_borrow_swap_or_exit_commission():
 s,c,d=fixture();s['closed']=300
 r=costs(s,c,d)
 assert r['commission_usc']==-.2 and r['swap_usc'] is None and not r['complete']

def test_reversal_resizing_or_fill_changes_cannot_borrow_costs():
 for key,value in [('side',1),('volume',2),('entry',10.1),('opened',101.1)]:
  s,c,d=fixture();s[key]=value
  assert costs(s,c,d)['commission_usc'] is None

def test_missing_fee_partial_and_ambiguous_records_fail_closed():
 s,c,d=fixture();del d[1][1]['fee'];assert not costs(s,c,d)['complete']
 s,c,d=fixture();d[1].append(deepcopy(d[1][1]));assert not costs(s,c,d)['complete']
 s,c,d=fixture();c['d'].append(2);d[2]=deepcopy(d[1]);assert costs(s,c,d)['commission_usc'] is None

def test_actual_gross_difference_is_not_assumed_slippage_conversion():
 s,c,d=fixture();d[1][1]['profit']=3
 r=costs(s,c,d);assert not r['complete'] and r['slippage_adjustment_usc'] is None

def test_net_portfolio_keeps_gross_results_separate(tmp_path):
 import sqlite3,json
 from exness_arena.shadow_portfolio import collect,connect,summary
 db=sqlite3.connect(tmp_path/'research.sqlite3')
 db.executescript('CREATE TABLE research_decisions(id TEXT,mode TEXT);CREATE TABLE shadows(id TEXT,decision_id TEXT,bot TEXT,strategy TEXT,plan_id TEXT,variant TEXT,status TEXT,symbol TEXT,side INTEGER,entry REAL,volume REAL,gross_usc REAL,version_id TEXT,opened REAL,closed REAL,exit_price REAL);CREATE TABLE live_context(position_id INTEGER,decision_id TEXT,attribution TEXT);CREATE TABLE live_deals(position_id INTEGER,payload TEXT);')
 db.execute("INSERT INTO research_decisions VALUES('d','test_only')")
 db.execute("INSERT INTO shadows VALUES('s','d','bot','rule','plan','proposed','closed','X',0,10,1,2,'v',100.1,200.1,12)")
 s,c,deals=fixture()
 db.execute("INSERT INTO live_context VALUES(1,'d','opening deal/order match')")
 for d in deals[1]:db.execute('INSERT INTO live_deals VALUES(1,?)',(json.dumps(d),))
 db.commit();db.close()
 r=collect(object(),tmp_path,300)['portfolios'][0]
 assert r['equity_usc']==2 and r['net_equity_usc']==1.5
 assert r['cost_complete_trades']==1 and r['known_commission_usc']==-.4
 assert r['net_complete_equity_samples']==1
