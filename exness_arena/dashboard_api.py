"""Bounded, read-only dashboard queries. No trading or configuration endpoints."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from .common import STATE, REGISTRY
from .identity import strategies, default_strategy


def read_db():
    return sqlite3.connect((STATE/'trial.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)


def history(repo='', strategy='', period='all'):
    if repo and repo not in REGISTRY:
        raise ValueError('Unknown repository')
    if strategy and (not repo or strategy not in strategies(repo)):
        raise ValueError('Unknown strategy')
    if period not in ('1h','4h','all'):
        raise ValueError('Unknown time range')
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=1 if period=='1h' else 4)).isoformat() if period!='all' else ''
    with closing(read_db()) as db:
        if strategy:
            rows=db.execute('SELECT time,net+floating FROM strategy_samples WHERE bot=? AND strategy=? AND time>=? ORDER BY time',
                            (repo,strategy,cutoff)).fetchall()
            if strategy==default_strategy(repo):
                first=db.execute('SELECT MIN(time) FROM strategy_samples WHERE bot=? AND strategy=?',(repo,strategy)).fetchone()[0]
                prefix=db.execute('SELECT time,net+floating FROM samples WHERE bot=? AND time>=? AND (? IS NULL OR time<?) ORDER BY time',
                                  (repo,cutoff,first,first)).fetchall()
                rows=prefix+rows
        elif repo:
            rows=db.execute('SELECT time,net+floating FROM samples WHERE bot=? AND time>=? ORDER BY time',(repo,cutoff)).fetchall()
        else:
            rows=db.execute('SELECT time,SUM(net+floating) FROM samples WHERE time>=? GROUP BY time ORDER BY time',(cutoff,)).fetchall()
    count=len(rows)
    if count>300:
        rows=[rows[round(i*(count-1)/299)] for i in range(300)]
    return {'repo':repo,'strategy':strategy,'range':period,'source_samples':count,
            'points':[{'time':t,'net_usc':v} for t,v in rows],
            'note':'Observed strategy net P&L, fees and swap included. Display downsampled to at most 300 actual observations; manual/external positions excluded.'}


def closed_trades():
    """Latest completed lifecycles; identity comes from original opening ownership."""
    snapshot=json.loads((STATE/'results.json').read_text(encoding='utf-8'))
    active={p['position_id'] for p in snapshot.get('positions',[])}
    with closing(read_db()) as db:
        owned={pid:(bot,strategy) for pid,bot,strategy in db.execute('SELECT * FROM strategy_owners')}
        rows=db.execute('SELECT position_id,payload FROM deals ORDER BY ticket').fetchall()
    grouped={}
    for pid,payload in rows:
        if pid in active or pid not in owned: continue
        d=json.loads(payload)
        group=grouped.setdefault(pid,[]);group.append(d)
    result=[]
    reasons={0:'Desktop / manual',1:'Mobile',2:'Web',3:'Expert',4:'Stop loss',5:'Take profit',6:'Stop out'}
    for pid,deals in grouped.items():
        entries=[d for d in deals if d.get('entry')==0]
        exits=[d for d in deals if d.get('entry') in (1,3)]
        if not entries or not exits: continue
        first=min(entries,key=lambda d:d.get('time_msc',d['time']*1000))
        last=max(exits,key=lambda d:d.get('time_msc',d['time']*1000))
        bot,strategy=owned[pid]
        result.append({'position_id':pid,'ticket':pid,'repo':bot,'strategy':strategy,'symbol':first['symbol'],
                       'direction':'buy' if first['type']==0 else 'sell','volume':sum(d['volume'] for d in entries),
                       'entry_price':first['price'],'exit_price':last['price'],
                       'opened_utc':datetime.fromtimestamp(first['time'],timezone.utc).isoformat(),
                       'closed_utc':datetime.fromtimestamp(last['time'],timezone.utc).isoformat(),
                       'net_usc':sum(d.get('profit',0)+d.get('swap',0)+d.get('commission',0)+d.get('fee',0) for d in deals),
                       'exit_reason':reasons.get(last.get('reason'),'Other')})
    return {'trades':sorted(result,key=lambda r:r['closed_utc'],reverse=True)[:500],
            'limit':500,'note':'Latest completed tracked positions. Includes partial exits and attributed manual closes.'}
