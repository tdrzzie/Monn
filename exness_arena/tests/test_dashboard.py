import json
import sqlite3
import threading
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import pytest
from exness_arena import dashboard_api,tracker


@pytest.fixture
def data(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard_api,'STATE',tmp_path)
    monkeypatch.setattr(tracker,'STATE',tmp_path)
    with sqlite3.connect(tmp_path/'trial.sqlite3') as db:
        db.executescript('''CREATE TABLE samples(time TEXT,bot TEXT,net REAL,floating REAL);
        CREATE TABLE strategy_samples(time TEXT,bot TEXT,strategy TEXT,net REAL,floating REAL);
        CREATE TABLE strategy_owners(position_id INTEGER,bot TEXT,strategy TEXT);
        CREATE TABLE deals(ticket INTEGER,position_id INTEGER,bot TEXT,payload TEXT);''')
        db.executemany('INSERT INTO samples VALUES(?,?,?,?)',[
            ('2026-01-01T00:00:00+00:00','Monn',-2,1),('2026-01-01T00:00:00+00:00','tradingbot',3,2),
            ('2026-01-01T00:01:00+00:00','Monn',-1,4)])
        db.execute('INSERT INTO strategy_samples VALUES(?,?,?,?,?)',('2026-01-01T00:01:00+00:00','Monn','ma_cross',-1,4))
    (tmp_path/'results.json').write_text(json.dumps({'positions':[{'position_id':2}]}))
    return tmp_path


def test_history_scopes_aggregate_and_inherit_only_primary_legacy_prefix(data):
    assert [p['net_usc'] for p in dashboard_api.history()['points']]==[4,3]
    assert [p['net_usc'] for p in dashboard_api.history('Monn')['points']]==[-1,3]
    assert [p['net_usc'] for p in dashboard_api.history('Monn','ma_cross')['points']]==[-1,3]
    assert dashboard_api.history('Monn','break_strategy')['points']==[]


def test_history_downsampling_keeps_observed_endpoints(data):
    with sqlite3.connect(data/'trial.sqlite3') as db:
        db.executemany('INSERT INTO samples VALUES(?,?,?,?)',[(f'2026-01-02T{n:06d}','Monn',n,0) for n in range(700)])
    result=dashboard_api.history('Monn')
    assert len(result['points'])==300
    assert result['points'][0]['net_usc']==-1 and result['points'][-1]['net_usc']==699


@pytest.mark.parametrize('args',[("Monn';DELETE FROM samples;--",),('Monn','unknown'),('','ma_cross'),('Monn','','bad')])
def test_history_rejects_invalid_scopes(data,args):
    with pytest.raises(ValueError):dashboard_api.history(*args)
    with sqlite3.connect(data/'trial.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]==3


def test_closed_trade_history_keeps_costs_and_ownership_on_manual_partial_exits(data):
    with sqlite3.connect(data/'trial.sqlite3') as db:
        db.executemany('INSERT INTO strategy_owners VALUES(?,?,?)',[(1,'Monn','ma_cross'),(2,'Monn','ma_cross')])
        deals=[{'ticket':1,'position_id':1,'entry':0,'time':1000,'type':0,'symbol':'EURUSDc','volume':.02,'price':1.1,'commission':-.1},
               {'ticket':2,'position_id':1,'entry':1,'time':1100,'volume':.01,'price':1.11,'profit':1,'magic':0,'reason':0},
               {'ticket':3,'position_id':1,'entry':1,'time':1200,'volume':.01,'price':1.12,'profit':2,'swap':-.2,'fee':-.05,'reason':5},
               {'ticket':4,'position_id':2,'entry':0,'time':1300,'type':1,'symbol':'US500c','volume':.14,'price':7000}]
        for d in deals:db.execute('INSERT INTO deals VALUES(?,?,?,?)',(d['ticket'],d['position_id'],'Monn',json.dumps(d)))
    rows=dashboard_api.closed_trades()['trades']
    assert len(rows)==1 and rows[0]['strategy']=='ma_cross'
    assert rows[0]['net_usc']==pytest.approx(2.65)
    assert rows[0]['exit_reason']=='Take profit' and rows[0]['volume']==.02


def test_http_assets_are_local_only_and_unknown_paths_cannot_read_files(data):
    server=ThreadingHTTPServer(('127.0.0.1',0),tracker.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with urlopen(base+'/') as r:
            body=r.read().decode();assert 'Portfolio overview' in body
            assert "connect-src 'self'" in r.headers['Content-Security-Policy']
        with urlopen(base+'/dashboard.js') as r:assert r.status==200 and 'javascript' in r.headers['Content-Type']
        with urlopen(base+'/api/history?repo=Monn') as r:assert len(json.load(r)['points'])==2
        for path in ('/../settings.json','/research.sqlite3','/settings.json'):
            with pytest.raises(HTTPError) as exc:urlopen(base+path)
            assert exc.value.code==404
        with pytest.raises(HTTPError) as exc:urlopen(base+'/api/history?repo=bad')
        assert exc.value.code==400
    finally:server.shutdown();server.server_close()

def test_collector_serves_timestamped_dashboard_before_initial_research_warmup(monkeypatch):
 from types import SimpleNamespace as S
 from exness_arena import tracker
 calls=[]
 class Server:
  def __init__(self,*args):calls.append('serve')
  def serve_forever(self):pass
  def shutdown(self):calls.append('shutdown')
 def collect():calls.append('collect');raise KeyboardInterrupt
 monkeypatch.setattr(tracker,'Gateway',lambda *a:S())
 monkeypatch.setattr(tracker,'wait_for_connection',lambda *a,**k:None)
 monkeypatch.setattr(tracker,'ThreadingHTTPServer',Server)
 monkeypatch.setattr(tracker.threading,'Thread',lambda *a,**k:S(start=lambda:None))
 monkeypatch.setattr(tracker,'collect',collect)
 monkeypatch.setattr(tracker.native,'shutdown',lambda:None)
 with pytest.raises(KeyboardInterrupt):tracker.main()
 assert calls==['serve','collect','shutdown']
