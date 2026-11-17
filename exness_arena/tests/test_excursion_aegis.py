import hashlib,json,sqlite3,sys,types,zlib
from types import SimpleNamespace as S
from exness_arena.excursion_aegis import sources


def setup_bridge(tmp_path,monkeypatch,proposal=None):
 root=tmp_path/'workspace';state=root/'Monn/.exness-arena';state.mkdir(parents=True)
 folder=root/'aegisquant';(folder/'.runtime').mkdir(parents=True);(folder/'aegisquant_bot').mkdir();(folder/'aegisquant_bot/runtime.py').write_text('fixture')
 monkeypatch.syspath_prepend(str(folder))
 pkg=types.ModuleType('aegisquant_bot');pkg.__path__=[];monkeypatch.setitem(sys.modules,'aegisquant_bot',pkg)
 runtime=types.ModuleType('aegisquant_bot.runtime');runtime.levels=lambda p,q,c,spread:dict(type=0,price=q['ask'],sl=90.,tp=120.)
 replay=types.ModuleType('aegisquant_bot.qualified_replay');replay.revalidate=lambda job:({},None)
 library=types.ModuleType('aegisquant_bot.library');library.version=lambda sid:'v'
 for m in [runtime,replay,library]:monkeypatch.setitem(sys.modules,m.__name__,m)
 contract=dict(trade_tick_size=1.,trade_tick_value_loss=1.,volume_min=.01,volume_max=10,volume_step=.01,point=1.,digits=2)
 snapshot=dict(now=1000.,contract=contract,quote=dict(event_at=1000.,ask=100.1,bid=100.))
 raw=json.dumps(snapshot).encode();sha=hashlib.sha256(raw).hexdigest()
 with sqlite3.connect(folder/'.runtime/journal.sqlite3') as db:
  db.executescript('CREATE TABLE inputs(id TEXT,payload BLOB);CREATE TABLE observations(id INTEGER PRIMARY KEY,time REAL,strategy TEXT,symbol TEXT,version TEXT,input_id TEXT,status TEXT,result TEXT,reason TEXT);')
  db.execute('INSERT INTO inputs VALUES(?,?)',(sha,zlib.compress(raw)))
  db.execute('INSERT INTO observations VALUES(1,999,\'MR01\',\'X\',\'v\',?,\'NO_SIGNAL\',?,NULL)',(sha,json.dumps({'state':{}})))
  db.execute('INSERT INTO observations VALUES(2,1000,\'MR01\',\'X\',\'v\',?,\'SIGNAL\',?,NULL)',(sha,json.dumps({'signals':[proposal or {'side':'long','stop':{'kind':'atr'}}]})))
 class Native:
  def account_info(self):return S(equity=1000)
  def order_send(self,*args):raise AssertionError('order submission forbidden')
 return state,dict(risk_budget_usc=3.5,max_volume=.01),Native(),folder/'.runtime/journal.sqlite3'


def test_bridge_records_only_gross_shadow_intents_without_native_live_qualification(tmp_path,monkeypatch):
 state,risk,n,db=setup_bridge(tmp_path,monkeypatch)
 rows,report=sources(state,risk,n,1001,900)
 assert len(rows)==1 and rows[0]['mode']=='test_only' and rows[0]['initial_risk']<=3.5*.9
 assert rows[0]['source_origin']=='AEGIS_REVALIDATED_PROTECTIVE_COUNTERFACTUAL'
 assert sources(state,risk,n,1400,900)[0]==[]


def test_checksum_reconstruction_and_deferred_intents_remain_blocked(tmp_path,monkeypatch):
 state,risk,n,path=setup_bridge(tmp_path,monkeypatch,{'side':'long','stop':{'kind':'atr'},'not_before':2000})
 rows,report=sources(state,risk,n,1001,900);assert rows==[] and report['reasons']['ORIGINAL_ELIGIBILITY_OR_DEFERRED_ENTRY']==1
 with sqlite3.connect(path) as db:db.execute("UPDATE inputs SET payload=?",(zlib.compress(b'{}'),))
 rows,report=sources(state,risk,n,1001,900);assert rows==[] and report['reasons']['INPUT_CHECKSUM']==1


def test_two_leg_intents_are_not_silently_converted_to_market_orders(tmp_path,monkeypatch):
 state,risk,n,path=setup_bridge(tmp_path,monkeypatch,{'side':'long','stop':{'kind':'atr'},'legs':[{},{}]})
 rows,report=sources(state,risk,n,1001,900)
 assert not rows and report['reasons']['UNSUPPORTED_MULTI_LEG_OR_CONTROLLER']==1
