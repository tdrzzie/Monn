"""Order-free targeted exit/filter/exposure studies, with globally ordered ticks."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timezone
import hashlib,heapq,json,math,sqlite3,time,zlib
from pathlib import Path
from .excursion_tests import new_trial,replay,digest,finite
from .shadow_costs import costs,load
from .shadow_portfolio import metrics

PLAN_PATH=Path(__file__).with_name('targeted_plan.json')
ACTIVE=('PENDING','OPEN')
ZERO=('FILTERED','SKIPPED_CAP')
UNKNOWN=('CENSORED','UNKNOWN_FILTER','UNKNOWN_EXPOSURE','LATE_SIGNAL')


def valid_path(raw):
    import numpy as np
    if not {'time_msc','bid','ask'}<=set(raw.dtype.names or ()):return False
    dt=np.diff(raw['time_msc'])
    ambiguous=(dt==0)&((np.diff(raw['bid'])!=0)|(np.diff(raw['ask'])!=0))
    return bool((dt>=0).all() and not ambiguous.any() and (np.isfinite(raw['bid']) & np.isfinite(raw['ask']) & (raw['bid']>0) & (raw['ask']>=raw['bid'])).all())


def connect(state):
    db=sqlite3.connect(Path(state)/'targeted_studies.sqlite3',timeout=3);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,source_id TEXT,strategy TEXT,variant TEXT,status TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS tapes(id TEXT PRIMARY KEY,payload BLOB,dtype TEXT,start REAL,end REAL,symbol TEXT);
    CREATE TABLE IF NOT EXISTS samples(key TEXT,time REAL,payload TEXT,PRIMARY KEY(key,time));''')
    return db


def regime(rule,source):
    f=json.loads(source['features']);c=json.loads(source['context']);kind=rule.get('filter')
    if not kind:return True
    if kind=='spread':
        q=c.get('quote',{});bid=q.get('bid');ask=q.get('ask')
        if not all(finite(x) for x in (bid,ask,q.get('time'))) or ask<bid or not 0<=source['opened']-q['time']<=30:return None
        return ask-bid<=rule['max_spread_r']*abs(source['entry']-source['sl'])
    b=f.get('last_closed_bar',{});close=b.get('close');at=b.get('time')
    if not all(finite(x) for x in (close,at)) or close<=0 or at+900>source['opened']:return None
    if kind=='trend_aligned':
        fast=f.get('sma_50');slow=f.get('sma_200')
        if not all(finite(x) and x>0 for x in (fast,slow)):return None
        return close>slow and fast>=slow if source['side']==0 else close<slow and fast<=slow
    atr=f.get('atr_talib_14')
    if not finite(atr) or atr<=0:return None
    return rule['min_atr_fraction']<=atr/close<=rule['max_atr_fraction']


def portfolio_key(r):return (r['plan_sha256'],r['bot'],r['strategy'],r['mode'],r['variant'])


def admission(t,all_trials,at):
    cap=t['rule'].get('max_positions')
    if cap is None:return None
    same=[r for r in all_trials if r['id']!=t['id'] and portfolio_key(r)==portfolio_key(t)]
    if any(r['status'] in UNKNOWN for r in same):return 'UNKNOWN_EXPOSURE'
    return 'SKIPPED_CAP' if sum(r['status']=='OPEN' for r in same)>=cap else None


def close_at(r,price,at,reason):
    pnl=(price-r['entry'])*(1 if r['side']==0 else -1)*r['cash_per_unit']*r['volume']
    r.update(status='CLOSED',closed=at,exit_price=price,reason=reason,gross_usc=pnl,r_multiple=pnl/r['initial_risk'],last_at=at,last_msc=int(at*1000))
    return r


def step(trial,q,all_trials):
    r=dict(trial);at=int(q['time_msc'])/1000
    if r['status'] not in ACTIVE or int(q['time_msc'])<=r['last_msc'] or at<=r['signal_at']+.25:return r
    if r['status']=='PENDING':
        blocked=admission(r,all_trials,at)
        if blocked:r.update(status=blocked,reason='Shadow portfolio admission rule',last_at=at);return r
    before=r['status'];r=replay(r,[q],at)
    if before=='PENDING' and r['status'] in ('OPEN','CLOSED'):
        r['entry_filled']=True;r['anchor_sl']=trial['sl'];r['peak_price_r']=0.
        if r['rule'].get('hold_seconds'):r['deadline']=min(r['deadline'],r['opened']+r['rule']['hold_seconds'])
    if r['status']!='OPEN':return r
    sign=1 if r['side']==0 else -1;price=float(q['bid'] if sign==1 else q['ask'])
    distance=abs(r['entry']-r['anchor_sl']);price_r=(price-r['entry'])*sign/distance
    peak=max(r.get('peak_price_r',0),price_r);r['peak_price_r']=peak;rule=r['rule']
    # A market giveback exit uses the current quote, never the earlier peak.
    if rule.get('giveback_arm_r') is not None and peak>=rule['giveback_arm_r'] and price_r<=peak*(1-rule['giveback_fraction']):
        return close_at(r,price,at,'observed_giveback_market_exit')
    if rule.get('arm_r') is not None and peak>=rule['arm_r']:
        lock=peak-rule['trail_r'] if 'trail_r' in rule else rule['lock_r']
        candidate=r['entry']+sign*lock*distance;tick=r['tick_size']
        candidate=(math.floor(candidate/tick+1e-8) if sign==1 else math.ceil(candidate/tick-1e-8))*tick
        candidate=round(candidate,r['digits'])
        # Hard protection is checked first. A tighter stop takes effect only on a later tick.
        if (price-candidate)*sign<=r['update_distance']:
            r['stop_updates_rejected']=r.get('stop_updates_rejected',0)+1
        elif (candidate-r['sl'])*sign>tick*.5:r['sl']=candidate;r['stop_updates']=r.get('stop_updates',0)+1
    return r


def sample_portfolios(trials,quotes,margin_rates,at):
    grouped=defaultdict(list)
    for r in trials:grouped[portfolio_key(r)].append(r)
    out={}
    for key,rows in grouped.items():
        pnl=0.;margin=0.;missing=False;unknown_margin=False;complete=0;traded=0
        for r in rows:
            status=r['status']
            if status in ZERO:continue
            if status in UNKNOWN:missing=True;unknown_margin=True;continue
            if status=='REJECTED':continue
            if status=='PENDING':continue
            traded+=1
            if status=='CLOSED':
                pnl+=r['gross_usc'];complete+=r.get('net_usc') is not None
            elif status=='OPEN':
                q=quotes.get(r['symbol']);rate=margin_rates.get((r['symbol'],r['side']))
                if not q or not 0<=at-q['time_msc']/1000<=30:missing=True
                else:
                    price=q['bid'] if r['side']==0 else q['ask'];pnl+=(price-r['entry'])*(1 if r['side']==0 else -1)*r['cash_per_unit']*r['volume']
                if rate is None:unknown_margin=True
                else:margin+=rate*r['volume']
        net=sum(r.get('net_usc',0) or 0 for r in rows if r['status']=='CLOSED') if complete==traded and not missing else None
        out[key]=dict(time=at,equity_usc=None if missing else pnl,net_equity_usc=net,margin_usc=None if unknown_margin else margin,
            open_positions=sum(r['status']=='OPEN' for r in rows),skipped=sum(r['status'] in ZERO for r in rows),
            unknown=sum(r['status'] in UNKNOWN for r in rows),cost_complete=complete,cost_total=traded,versions=sorted({r['version_id'] for r in rows}))
    return out


def collect(native,state,risk):
    state=Path(state);plan=json.loads(PLAN_PATH.read_text(encoding='utf-8'));sha=digest(plan);now=time.time()
    with closing(connect(state)) as own:
        last=own.execute("SELECT value FROM meta WHERE key='last'").fetchone()
        if last and now-float(last[0])<60:return
        own.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('start:'+sha,str(now)));own.commit()
        start=float(own.execute('SELECT value FROM meta WHERE key=?',('start:'+sha,)).fetchone()[0])
    with closing(sqlite3.connect((state/'research.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        sources=[dict(r) for r in db.execute("SELECT s.*,d.mode,d.features,d.context FROM shadows s JOIN research_decisions d ON s.decision_id=d.id WHERE s.bot='tradingbot' AND s.variant='proposed' AND s.opened>? AND s.opened<=? AND d.mode IN ('live_evaluation','test_only')",(start,now)) if r['strategy'] in plan['strategies']]
        contexts,deals=load(db)
    with closing(connect(state)) as own:
        rows=[json.loads(r['payload']) for r in own.execute('SELECT payload FROM trials')];ids={r['id'] for r in rows}
        for source in sources:
            for rule in [{'key':'original'}]+plan['strategies'][source['strategy']]:
                ident=digest([sha,source['id'],rule['key']])
                if ident in ids:continue
                try:
                    contract=json.loads(source['context'])['contract']
                    t=new_trial(source,{'stop_r':1,'target_r':1},'original',contract,risk)
                    t.update(id=ident,source_id=source['id'],variant=rule['key'],rule=rule,plan_sha256=sha,plan_id=plan['id'],risk_limit=risk['risk_budget_usc'],
                        tick_size=contract['trade_tick_size'],digits=contract['digits'],update_distance=max(contract.get('trade_stops_level',0),contract.get('trade_freeze_level',0))*contract['point'],
                        entry_filled=False,method_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
                    passed=regime(rule,source)
                    if passed is None:t.update(status='UNKNOWN_FILTER',reason='Missing/noncausal pre-signal features')
                    elif not passed:t.update(status='FILTERED',reason='Predeclared market-condition filter')
                    elif last and source['opened']<=float(last[0]):t.update(status='LATE_SIGNAL',reason='Late-published source: no retroactive portfolio admission')
                except (ValueError,TypeError,KeyError,ZeroDivisionError,OverflowError) as exc:
                    t=dict(source,id=ident,source_id=source['id'],variant=rule['key'],rule=rule,plan_sha256=sha,plan_id=plan['id'],status='REJECTED',reason=str(exc))
                rows.append(t);ids.add(ident)
        pending=[r for r in rows if r['status'] in ACTIVE];streams=[];quotes={}
        for symbol in sorted({r['symbol'] for r in pending}):
            group=[]
            for t in pending:
                if t['symbol']!=symbol:continue
                if now-t['last_at']>300:t.update(status='CENSORED',reason='Missing tick collection window')
                else:group.append(t)
            if not group:continue
            lo=min(r['last_at'] for r in group);raw=None
            try:raw=native.copy_ticks_range(symbol,datetime.fromtimestamp(lo,timezone.utc),datetime.fromtimestamp(now,timezone.utc),native.COPY_TICKS_ALL)
            except Exception:pass
            if raw is None or len(raw)==0:
                for t in group:t.update(status='CENSORED',reason='Unavailable/empty provider tick window')
                continue
            if not valid_path(raw):
                for t in group:t.update(status='CENSORED',reason='Invalid/unordered or ambiguous same-millisecond provider path')
                continue
            blob=zlib.compress(raw.tobytes());tapeid=digest([symbol,lo,now,hashlib.sha256(blob).hexdigest()])
            own.execute('INSERT OR IGNORE INTO tapes VALUES(?,?,?,?,?,?)',(tapeid,blob,json.dumps(raw.dtype.descr),lo,now,symbol))
            def stream(a,s):
                for i,q in enumerate(a):
                    if q['time_msc']/1000<=now:yield (int(q['time_msc']),s,i,q)
            streams.append(stream(raw,symbol))
        by_symbol=defaultdict(list)
        for i,r in enumerate(rows):
            if r['status'] in ACTIVE:by_symbol[r['symbol']].append(i)
        for msc,symbol,index,raw in heapq.merge(*streams):
            q=dict(time_msc=msc,bid=float(raw['bid']),ask=float(raw['ask']));quotes[symbol]=q
            for i in sorted(by_symbol[symbol],key=lambda i:(rows[i]['signal_at'],rows[i]['source_id'],rows[i]['variant'])):
                rows[i]=step(rows[i],q,rows)
        for t in rows:
            if t['status'] in ACTIVE:
                if t['status']=='PENDING' and now-t['signal_at']<=30:t['last_at']=now
                elif t['last_msc']/1000<now-30:t.update(status='CENSORED',reason='Stale tick-window tail')
                else:t['last_at']=now
            if t['status']=='CLOSED' and t.get('net_usc') is None:
                e=costs(dict(t,status='closed'),contexts,deals);t['cost_evidence']=e
                if e['complete']:t['net_usc']=t['gross_usc']+e['commission_usc']+e['swap_usc']+e['slippage_adjustment_usc'];t['cost_known_at']=now
            own.execute('INSERT OR REPLACE INTO trials VALUES(?,?,?,?,?,?)',(t['id'],t['source_id'],t['strategy'],t['variant'],t['status'],json.dumps(t)))
        rates={}
        for symbol,side in {(r['symbol'],r['side']) for r in rows if r['status']=='OPEN'}:
            try:
                q=quotes.get(symbol);v=native.order_calc_margin(side,symbol,1.,q['ask'] if side==0 else q['bid']) if q else None
                rates[symbol,side]=v if finite(v) and v>=0 else None
            except Exception:rates[symbol,side]=None
        for key,sample in sample_portfolios(rows,quotes,rates,now).items():own.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?)',(json.dumps(key),now,json.dumps(sample)))
        own.execute("INSERT OR REPLACE INTO meta VALUES('last',?)",(str(now),));own.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('plan:'+sha,json.dumps(plan)));own.commit()


def summary(state):
    path=Path(state)/'targeted_studies.sqlite3'
    if not path.exists():return dict(comparisons=[],portfolios=[],status='WAITING_FORWARD_SIGNALS')
    with closing(connect(state)) as db:
        rows=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM trials')]
        paths=defaultdict(list)
        for k,p in db.execute('SELECT key,payload FROM samples ORDER BY time'):paths[k].append(json.loads(p))
    groups=defaultdict(list);baselines={}
    for r in rows:
        groups[portfolio_key(r)].append(r)
        if r['variant']=='original':baselines[r['plan_sha256'],r['source_id']]=r
    comparisons=[]
    for key,items in groups.items():
        sha,bot,strategy,mode,variant=key;pairs=[];net=[]
        for r in items:
            b=baselines.get((sha,r['source_id']))
            if not b or b['status']!='CLOSED' or r['status'] not in ('CLOSED',)+ZERO:continue
            delta=(r['gross_usc'] if r['status']=='CLOSED' else 0)-b['gross_usc'];pairs.append(delta)
            if b.get('net_usc') is not None and (r['status'] in ZERO or r.get('net_usc') is not None):net.append((r.get('net_usc',0) or 0)-b['net_usc'])
        comparisons.append(dict(bot=bot,strategy=strategy,mode=mode,variant=variant,plan_sha256=sha,total=len(items),closed=sum(r['status']=='CLOSED' for r in items),rejected=sum(r['status']=='REJECTED' for r in items),excluded=sum(r['status'] in ZERO for r in items),unknown=sum(r['status'] in UNKNOWN for r in items),paired=len(pairs),gross_delta_usc=sum(pairs) if pairs else None,net_pairs=len(net),net_delta_usc=sum(net) if net else None))
    portfolios=[]
    for key,path in paths.items():
        sha,bot,strategy,mode,variant=json.loads(key);item=dict(bot=bot,strategy=strategy,mode=mode,variant=variant,plan_sha256=sha)
        item.update(metrics(path));item.update({k:path[-1].get(k) for k in ('open_positions','skipped','unknown','cost_complete','cost_total')})
        n=metrics([dict(r,equity_usc=r.get('net_equity_usc')) for r in path]);item['net_equity_usc']=n['equity_usc'];item['net_drawdown_usc']=n['sampled_max_drawdown_usc'];item['net_longest_underwater_seconds']=n['longest_observed_underwater_seconds'];item['versions']=path[-1]['versions'];portfolios.append(item)
    return dict(comparisons=comparisons,portfolios=portfolios,plan=json.loads(PLAN_PATH.read_text(encoding='utf-8')),
        limitations='Predeclared forward probes only. Global chronological tick admission; source/plan/cohort identity retained. Exposure caps are shadow-only, per strategy across instruments. Filters use saved pre-signal values. Break-even stops protect entry price, not net profit after costs. Unknown paths/exposure/filter data remain unknown. Intentional filter/cap exclusions count as zero-trade outcomes against the full matched baseline signal set. Sampled portfolio drawdown/underwater are lower bounds; margin is standalone current estimation, not historical broker offsets/tiers. No funded live-rule replication or automatic promotion. Gross conversion is frozen to source risk; full net requires complete broker cost evidence.')
