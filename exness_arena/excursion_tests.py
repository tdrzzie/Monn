"""Frozen MAE/MFE hypotheses, forward tick replay, independent cost coverage.
No broker submission or live configuration writes exist in this module.
"""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

RECIPE='excursion-forward-v1'
MIN_TRAIN=30
MIN_DATES=2
MIN_WINNERS=5
VARIANTS=('original','mae_stop','mfe_target','mae_mfe')
BOOTSTRAP_VARIANTS=('original','bootstrap_stop','bootstrap_target','bootstrap_both')
STATELESS_TRANSFER_BOTS={'Monn','MT5-Python-Trading','tradingbot','Forex-Trading-Bot'}
MAX_GAP=300
HORIZON=86400


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,allow_nan=False).encode()).hexdigest()
def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def quantile(x,p):
    x=sorted(x);at=(len(x)-1)*p;low=int(at)
    return x[low]+(x[min(low+1,len(x)-1)]-x[low])*(at-low)


def fit(rows,cutoff):
    eligible=[r for r in rows if r['known_at']<cutoff and r['closed']<cutoff and all(finite(r.get(k)) for k in ('mae_r','mfe_r','gross_usc')) and r['mae_r']<=0<=r['mfe_r']]
    dates={int(r['opened']//86400) for r in eligible}
    winners=[abs(r['mae_r']) for r in eligible if r['gross_usc']>0]
    if len(eligible)<MIN_TRAIN or len(dates)<MIN_DATES or len(winners)<MIN_WINNERS:
        return dict(status='WAITING_SUPPORT',count=len(eligible),dates=len(dates),winners=len(winners))
    return dict(status='FROZEN',count=len(eligible),dates=len(dates),winners=len(winners),cutoff=cutoff,
        stop_r=max(.25,min(1.,quantile(winners,.8))),target_r=max(.25,min(3.,quantile([r['mfe_r'] for r in eligible],.6))),
        training_ids=sorted(r['id'] for r in eligible),training_sha256=digest(sorted((r['id'],r['mae_r'],r['mfe_r'],r['gross_usc'],r['known_at']) for r in eligible)),
        recipe=RECIPE,method_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),selection='80th percentile winning MAE; 60th percentile all-trade MFE. Gross, censored exploratory calibration; no parameter search.')


def source_identity(bot,sources):
    manifest=sources.get('manifest',{})
    selected={p:h for p,h in manifest.items() if (p.startswith(bot+'/') and p!='Monn/sync_trading_docs.py' and not any(s in p for s in ('/tests/','/exness_arena/','tools/','model_training','qualified_replay','replay_pipeline')))
              or p in ('Monn/exness_arena/adapters.py','Monn/exness_arena/expanded.py')}
    return digest(selected)


def signal_context(row,features,sources):
    # Keep the original strategy implementation and adapter identity; exclude unrelated docs/research changes.
    bot=row['bot']
    selected=sources.get('signal_sha256') or source_identity(bot,sources)
    return digest(dict(bot=bot,strategy=row['strategy'],symbol=row['symbol'],sources=selected,
        parameters=features.get('parameters'),definition=features.get('original_definition'),mode=row['mode']))


def choose_profile(source,profiles,now,bootstrap_start):
    key=source['signal_context']
    if key in profiles and profiles[key]['cutoff']<source['opened']:
        return profiles[key],key,'CALIBRATED'
    other=source.get('alternate_context')
    if (source['bot'] in STATELESS_TRANSFER_BOTS and other in profiles
            and profiles[other]['cutoff']<source['opened']):
        return profiles[other],other,'CALIBRATED_COHORT_TRANSFER'
    # Predeclared probes collect excursions, not statistically fitted thresholds.
    # They start only after this phase was enabled, never on an already observed signal.
    profile=dict(status='BOOTSTRAP_FIXED',cutoff=bootstrap_start,stop_r=.75,target_r=1.,
        count=0,dates=0,winners=0,training_ids=[],recipe='excursion-bootstrap-fixed-v1',
        selection='Predeclared 0.75 original stop-distance and 1R target; not MAE/MFE calibrated',
        method_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        bot=source['bot'],strategy=source['strategy'],symbol=source['symbol'],mode=source['mode'])
    if source['opened']<=bootstrap_start:return None,None,'WAITING_FUTURE_SIGNAL'
    return profile,key,'BOOTSTRAP_FIXED'


def connect(state):
    db=sqlite3.connect(Path(state)/'excursion_tests.sqlite3',timeout=3);db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS profiles(context TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS bootstrap_profiles(context TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,source_id TEXT,context TEXT,variant TEXT,status TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS skips(source_id TEXT PRIMARY KEY,reason TEXT);
    CREATE TABLE IF NOT EXISTS tapes(symbol TEXT,start REAL,end REAL,sha256 TEXT,payload BLOB,PRIMARY KEY(symbol,start,end));''')
    return db


def new_trial(source,profile,variant,contract,risk):
    requested_variant=variant
    variant={'bootstrap_stop':'mae_stop','bootstrap_target':'mfe_target','bootstrap_both':'mae_mfe'}.get(variant,variant)
    sign=1 if source['side']==0 else -1;distance=abs(source['entry']-source['sl'])
    if not distance>0 or not source['initial_risk']>0:raise ValueError('Missing original stop/risk')
    if not all(finite(contract.get(k)) and contract[k]>0 for k in ('trade_tick_size','volume_step','volume_min','volume_max','point')):raise ValueError('Invalid recorded contract grid')
    tick=contract['trade_tick_size'];step=contract['volume_step']
    sl=source['sl'] if variant in ('original','mfe_target') else source['entry']-sign*distance*profile['stop_r']
    tp=source['tp'] if variant in ('original','mae_stop') else source['entry']+sign*distance*profile['target_r']
    sl=round(round(sl/tick)*tick,contract['digits']);tp=round(round(tp/tick)*tick,contract['digits']) if tp else 0.
    cash=source['initial_risk']/(distance*source['volume'])
    cap=risk.get('max_volume_by_symbol',{}).get(source['symbol'],risk['max_volume'])
    volume=min(source['volume'],cap,contract['volume_max'],min(risk['risk_budget_usc'],source.get('risk_ceiling_usc',risk['risk_budget_usc']))*.9/(abs(source['entry']-sl)*cash))
    volume=round(math.floor(volume/step+1e-9)*step,8)
    if volume<contract['volume_min'] or abs(source['entry']-sl)<=contract.get('trade_stops_level',0)*contract['point']:raise ValueError('Minimum lot or stop does not fit unchanged risk policy')
    return dict(source,variant=requested_variant,sl=sl,tp=tp,volume=volume,cash_per_unit=cash,
        mae_r=0.,mfe_r=0.,initial_risk=abs(source['entry']-sl)*cash*volume,signal_at=source['opened'],deadline=source['opened']+HORIZON,
        min_stop_distance=contract.get('trade_stops_level',0)*contract['point'],last_at=source['opened'],last_msc=0,status='PENDING',profile_sha256=digest(profile),recipe=RECIPE,
        replay_method_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),execution='Observed first bid/ask after 250ms; original protection anchored at signal. Gross conversion frozen to recorded signal stop-risk valuation.')


def replay(trial,ticks,until):
    r=dict(trial);sign=1 if r['side']==0 else -1
    if r['status'] not in ('PENDING','OPEN'):return r
    if until-r['last_at']>MAX_GAP:r.update(status='CENSORED',reason='Missing collector/tick window');return r
    previous=None
    for q in ticks:
        msc=int(q['time_msc']);at=msc/1000;bid=float(q['bid']);ask=float(q['ask'])
        if (previous is not None and msc<previous) or not all(finite(v) for v in (bid,ask)) or bid<=0 or ask<bid:
            r.update(status='CENSORED',reason='Invalid or unordered tick path');return r
        previous=msc
        if at>until or msc<=r['last_msc']:continue
        last_tick=r['last_msc']/1000 if r['last_msc'] else r['signal_at']
        if at-last_tick>120:r.update(status='CENSORED',reason='Unobserved intrawindow tick gap');return r
        if r['status']=='PENDING':
            if at<=r['signal_at']+.25:continue
            entry=ask if sign==1 else bid
            if r.get('original_gap_limit') is not None and abs(entry-r['signal_bid_close'])>r['original_gap_limit']:
                r.update(status='REJECTED',reason='Original signal ATR gap filter');return r
            if abs(entry-r['entry'])>.5*abs(r['entry']-r['sl']):r.update(status='REJECTED',reason='First eligible fill gap');return r
            if (entry-r['sl'])*sign<=r['min_stop_distance'] or ask-bid>.15*abs(entry-r['sl']) or (r['tp'] and (r['tp']-entry)*sign<=0):r.update(status='REJECTED',reason='Invalid protection at first fill');return r
            risk=abs(entry-r['sl'])*r['cash_per_unit']*r['volume']
            if risk>r['risk_limit']*.9+1e-8:r.update(status='REJECTED',reason='Fill exceeds reserved per-position risk');return r
            r.update(entry=entry,opened=at,initial_risk=risk,status='OPEN')
        price=bid if sign==1 else ask
        # First actual quote crossing SL fills at quote; TP at limit, never improved by later information.
        stop=(price-r['sl'])*sign<=0;target=r['tp'] and (price-r['tp'])*sign>=0
        if stop or target or at>=r['deadline']:
            exit_price=r['tp'] if target and not stop else price
            gross=(exit_price-r['entry'])*sign*r['cash_per_unit']*r['volume']
            excursion=gross/r['initial_risk'];r['mae_r']=min(r.get('mae_r',0.),excursion);r['mfe_r']=max(r.get('mfe_r',0.),excursion)
            r.update(status='CLOSED',closed=at,exit_price=exit_price,gross_usc=gross,r_multiple=gross/r['initial_risk'],
                     reason='stop' if stop else 'target' if target else 'time',last_msc=msc,last_at=at)
            return r
        excursion=(price-r['entry'])*sign*r['cash_per_unit']*r['volume']/r['initial_risk']
        r['mae_r']=min(r.get('mae_r',0.),excursion);r['mfe_r']=max(r.get('mfe_r',0.),excursion)
        r['last_msc']=msc
    if r['last_msc']/1000<until-30:r.update(status='CENSORED',reason='Tick window ends with stale observations')
    r['last_at']=until
    return r


def collect(native,state,risk):
    import zlib
    from .shadow_costs import load,costs
    state=Path(state);now=time.time()
    with closing(connect(state)) as own:
        last=own.execute("SELECT value FROM meta WHERE key='last_collect'").fetchone()
        if last and now-float(last[0])<60:return
        own.execute("INSERT OR IGNORE INTO meta VALUES('bootstrap_start',?)",(str(now),));bootstrap_start=float(own.execute("SELECT value FROM meta WHERE key='bootstrap_start'").fetchone()[0])
        own.execute("INSERT OR IGNORE INTO meta VALUES('start',?)",(str(now),));start=float(own.execute("SELECT value FROM meta WHERE key='start'").fetchone()[0]);own.commit()
    with closing(sqlite3.connect((state/'research.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute("SELECT s.*,d.mode,d.features,d.context,e.known_at FROM shadows s JOIN research_decisions d ON d.id=s.decision_id LEFT JOIN (SELECT shadow_id,MAX(time) known_at FROM shadow_events WHERE kind='closed' GROUP BY shadow_id) e ON e.shadow_id=s.id WHERE s.variant='proposed' AND d.mode IN ('live_evaluation','test_only')")]
        contexts,deals=load(db)
        activity={(r[0],r[1]):dict(decisions=r[2],last_decision=r[3]) for r in db.execute('SELECT bot,strategy,COUNT(*),MAX(time) FROM research_decisions GROUP BY bot,strategy')}
        for r in rows:
            a=activity.setdefault((r['bot'],r['strategy']),dict(decisions=0,last_decision=None));a['baselines']=a.get('baselines',0)+1;a['last_signal']=max(a.get('last_signal',0),r['opened'])
        codes={}
        for r in rows:
            code=(r['bot'],r['version_id'])
            if code not in codes:
                payload=db.execute('SELECT sources FROM versions WHERE id=?',(r['version_id'],)).fetchone()
                codes[code]=source_identity(r['bot'],json.loads(payload[0]))
            features=json.loads(r['features']);fingerprint={'signal_sha256':codes[code]}
            r['signal_context']=signal_context(r,features,fingerprint)
            r['alternate_context']=signal_context(dict(r,mode='test_only' if r['mode']=='live_evaluation' else 'live_evaluation'),features,fingerprint)
    from .excursion_aegis import sources as aegis_sources
    bridge,bridge_report=aegis_sources(state,risk,native,now,bootstrap_start)
    rows.extend(bridge)
    for r in bridge:
        a=activity.setdefault(('aegisquant',r['strategy']),dict(decisions=0,last_decision=r['opened']));a['baselines']=a.get('baselines',0)+1;a['last_signal']=r['opened']
    with closing(connect(state)) as prior:
        present={r['id'] for r in rows}
        for recorded in prior.execute("SELECT payload FROM trials WHERE variant='original'"):
            t=json.loads(recorded['payload'])
            if t.get('source_origin')=='AEGIS_REVALIDATED_PROTECTIVE_COUNTERFACTUAL':
                a=activity.setdefault(('aegisquant',t['strategy']),dict(decisions=0,last_decision=t['signal_at']))
                a['bridge_baselines']=a.get('bridge_baselines',0)+1;a['baselines']=max(a.get('baselines',0),a['bridge_baselines'])
        for record in prior.execute("SELECT payload FROM trials WHERE variant='original' AND status='CLOSED'"):
            r=json.loads(record['payload'])
            if r['source_id'] in present:continue
            r['id']=r['source_id'];r['status']='closed';r['known_at']=r.get('observed_close_at')
            if r['known_at'] is not None:rows.append(r);present.add(r['id'])
    grouped=defaultdict(list)
    for r in rows:
        key=r['signal_context']
        if r['status']=='closed' and r['known_at'] is not None:grouped[key].append(r)
    with closing(connect(state)) as own:
        support=[]
        for key,training in grouped.items():
            report=fit(training,now);ref=training[0]
            support.append(dict(bot=ref['bot'],strategy=ref['strategy'],symbol=ref['symbol'],mode=ref['mode'],context=key,count=report['count'],dates=report['dates'],winners=report['winners'],status=report['status']))
            if own.execute('SELECT 1 FROM profiles WHERE context=?',(key,)).fetchone():continue
            profile=fit(training,now)
            if profile['status']=='FROZEN':
                ref=training[0];profile.update(bot=ref['bot'],strategy=ref['strategy'],symbol=ref['symbol'],mode=ref['mode'])
                own.execute('INSERT INTO profiles VALUES(?,?)',(key,json.dumps(profile)))
        own.execute("INSERT OR REPLACE INTO meta VALUES('support',?)",(json.dumps(support),))
        own.execute("INSERT OR REPLACE INTO meta VALUES('aegis_bridge',?)",(json.dumps(bridge_report),))
        own.execute("INSERT OR REPLACE INTO meta VALUES('activity',?)",(json.dumps({b+'/'+s:a for (b,s),a in activity.items()}),))
        profiles={r['context']:json.loads(r['payload']) for r in own.execute('SELECT * FROM profiles')}
        for source in rows:
            if source['opened']<start or source['opened']>now:continue
            if own.execute('SELECT 1 FROM trials WHERE source_id=? UNION SELECT 1 FROM skips WHERE source_id=?',(source['id'],source['id'])).fetchone():continue
            profile,calibration_context,kind=choose_profile(source,profiles,now,bootstrap_start)
            if not profile:
                own.execute('INSERT OR IGNORE INTO skips VALUES(?,?)',(source['id'],'No frozen pre-signal profile; never replay calibration trades as tests'));continue
            try:
                if kind=='BOOTSTRAP_FIXED':own.execute('INSERT OR IGNORE INTO bootstrap_profiles VALUES(?,?)',(source['signal_context'],json.dumps(profile)))
                contract=json.loads(source['context'])['contract'];bundle=[]
                for variant in (BOOTSTRAP_VARIANTS if kind=='BOOTSTRAP_FIXED' else VARIANTS):
                    try:t=new_trial(source,profile,variant,contract,risk)
                    except (KeyError,TypeError,ValueError,ZeroDivisionError,OverflowError) as exc:t=dict(source,variant=variant,status='REJECTED',reason=str(exc))
                    t['profile_kind']=kind;t['calibration_context']=calibration_context;t['calibration_mode']=profile['mode']
                    t['risk_limit']=min(risk['risk_budget_usc'],source.get('risk_ceiling_usc',risk['risk_budget_usc']));t['source_id']=source['id'];t['id']=digest([RECIPE,source['id'],variant,digest(profile)])
                    bundle.append(t)
                for t in bundle:own.execute('INSERT INTO trials VALUES(?,?,?,?,?,?)',(t['id'],source['id'],source['signal_context'],t['variant'],t['status'],json.dumps(t)))
            except (KeyError,TypeError,ValueError,ZeroDivisionError) as exc:own.execute('INSERT OR IGNORE INTO skips VALUES(?,?)',(source['id'],str(exc)))
        pending=[json.loads(r['payload']) for r in own.execute("SELECT payload FROM trials WHERE status IN ('PENDING','OPEN')")];own.commit()
        for symbol in {r['symbol'] for r in pending}:
            all_group=[r for r in pending if r['symbol']==symbol];group=[]
            for stale in all_group:
                if now-stale['last_at']>MAX_GAP:
                    stale.update(status='CENSORED',reason='Missing collector/tick window')
                    own.execute('UPDATE trials SET status=?,payload=? WHERE id=?',(stale['status'],json.dumps(stale),stale['id']))
                else:group.append(stale)
            if not group:continue
            begin=min(r['last_at'] for r in group)
            raw=None;ticks=[]
            if now-begin<=MAX_GAP:
                try:raw=native.copy_ticks_range(symbol,datetime.fromtimestamp(begin,timezone.utc),datetime.fromtimestamp(now,timezone.utc),native.COPY_TICKS_ALL)
                except Exception:raw=None
            if raw is not None and len(raw):
                # Exact provider response retained; no synthetic intrabar ordering.
                blob=zlib.compress(raw.tobytes());sha=hashlib.sha256(blob).hexdigest()
                own.execute('INSERT OR IGNORE INTO tapes VALUES(?,?,?,?,?)',(symbol,begin,now,sha,blob));ticks=raw
                own.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('tape_dtype:'+sha,json.dumps(raw.dtype.descr)))
            for t in group:
                if raw is None or not len(ticks):t.update(status='CENSORED',reason='Unavailable or empty tick window')
                else:t=replay(t,ticks,now)
                if t['status']=='CLOSED':
                    t['observed_close_at']=now
                    check=dict(t,status='closed');e=costs(check,contexts,deals);t['cost_evidence']=e
                    t['net_usc']=t['gross_usc']+e['commission_usc']+e['swap_usc']+e['slippage_adjustment_usc'] if e['complete'] else None
                own.execute('UPDATE trials SET status=?,payload=? WHERE id=?',(t['status'],json.dumps(t),t['id']))
        # Cost records may arrive after the modeled close; update coverage without replaying or changing gross outcomes.
        for row in own.execute("SELECT id,payload FROM trials WHERE status='CLOSED'").fetchall():
            t=json.loads(row['payload'])
            if t.get('net_usc') is not None:continue
            e=costs(dict(t,status='closed'),contexts,deals)
            if e['complete']:
                t['cost_evidence']=e;t['cost_known_at']=now
                t['net_usc']=t['gross_usc']+e['commission_usc']+e['swap_usc']+e['slippage_adjustment_usc']
                own.execute('UPDATE trials SET payload=? WHERE id=?',(json.dumps(t),row['id']))
        own.execute("INSERT OR REPLACE INTO meta VALUES('last_collect',?)",(str(now),));own.commit()


def summary(state,registry):
    path=Path(state)/'excursion_tests.sqlite3'
    if not path.exists():return dict(profiles=[],comparisons=[],catalog=[],status='INITIALIZING')
    with closing(connect(state)) as db:
        profiles=[dict(json.loads(r['payload']),context=r['context']) for r in db.execute('SELECT context,payload FROM profiles')]
        bootstrap=[dict(json.loads(r['payload']),context=r['context']) for r in db.execute('SELECT context,payload FROM bootstrap_profiles')]
        audit=db.execute("SELECT value FROM meta WHERE key='support'").fetchone();support=json.loads(audit[0]) if audit else []
        audit=db.execute("SELECT value FROM meta WHERE key='activity'").fetchone();activity=json.loads(audit[0]) if audit else {}
        audit=db.execute("SELECT value FROM meta WHERE key='aegis_bridge'").fetchone();bridge=json.loads(audit[0]) if audit else {}
        rows=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM trials')]
        skips=[dict(reason=r[0],count=r[1]) for r in db.execute('SELECT reason,COUNT(*) FROM skips GROUP BY reason')]
    groups=defaultdict(list)
    by_source=defaultdict(dict)
    for r in rows:by_source[r['source_id']][r['variant']]=r;groups[(r['bot'],r['strategy'],r['symbol'],r['signal_context'],r.get('profile_sha256'),r.get('profile_kind','CALIBRATED'),r['variant'])].append(r)
    comparisons=[]
    for (bot,strategy,symbol,context,profile_sha,profile_kind,variant),items in groups.items():
        paired=[(r,by_source[r['source_id']]['original']) for r in items if r['status']=='CLOSED' and by_source[r['source_id']]['original']['status']=='CLOSED']
        net=[(a,b) for a,b in paired if a.get('net_usc') is not None and b.get('net_usc') is not None]
        comparisons.append(dict(bot=bot,strategy=strategy,symbol=symbol,mode=items[0]['mode'],context=context,profile_sha256=profile_sha,profile_kind=profile_kind,calibration_mode=items[0].get('calibration_mode',items[0]['mode']),variant=variant,total=len(items),closed=sum(r['status']=='CLOSED' for r in items),censored=sum(r['status']=='CENSORED' for r in items),rejected=sum(r['status']=='REJECTED' for r in items),paired=len(paired),reasons=sorted({r.get('reason','') for r in items if r['status'] in ('REJECTED','CENSORED')}),
            delta_mean_r=sum(a['r_multiple']-b['r_multiple'] for a,b in paired)/len(paired) if paired else None,
            gross_delta_usc=sum(a['gross_usc']-b['gross_usc'] for a,b in paired) if paired else None,net_pairs=len(net),net_delta_usc=sum(a['net_usc']-b['net_usc'] for a,b in net) if net else None))
    catalog=[]
    for bot,spec in registry.items():
        for strategy in spec['strategies']:
            a=activity.get(bot+'/'+strategy,{})
            learned=sum(p['bot']==bot and p['strategy']==strategy for p in profiles)
            boot=sum(p['bot']==bot and p['strategy']==strategy for p in bootstrap)
            ready=learned or boot
            status='CALIBRATED_AVAILABLE' if learned else 'BOOTSTRAP_ACTIVE' if boot else 'BOOTSTRAP_READY_WAITING_SIGNAL' if a.get('baselines') else 'NO_COMPATIBLE_BASELINE' if a.get('decisions') else 'NO_OBSERVATIONS'
            native=bridge.get('strategy_statuses',{}).get(strategy,{}) if bot=='aegisquant' else {}
            if native and not ready and not a.get('baselines'):status='WAITING_ORIGINAL_SIGNAL'
            if native.get('status') in ('BLOCKED_DATA','BLOCKED_MODEL') and not ready:status='WAITING_ORIGINAL_DATA_OR_MODEL'
            if not spec.get('runner',True):status='INACTIVE_EXTERNAL_BLOCKER'
            if not a and not native and not profiles and not bootstrap:status='WAITING_SUPPORT'
            catalog.append(dict(bot=bot,strategy=strategy,status=status,profiles=learned,bootstrap_profiles=boot,decisions=max(a.get('decisions',0),native.get('observations',0)),baselines=a.get('baselines',0),last_signal=a.get('last_signal'),
                reason=(native.get('status','')+': '+str(native.get('reason',''))) if native and not a.get('baselines') else 'No compatible recorded single-leg entry; do not manufacture a trade' if not a.get('baselines') else 'Frozen identical-context calibration or fixed bootstrap; current risk/lot/tick checks still apply'))
    return dict(recipe=RECIPE,profiles=profiles,bootstrap_profiles=bootstrap,support=support,aegis_bridge=bridge,comparisons=comparisons,catalog=catalog,skips=skips,minimum_training=MIN_TRAIN,minimum_dates=MIN_DATES,
        limitations='Low-data fixed bootstrap probes are explicitly not learned MAE/MFE thresholds; calibrated gates remain 30 observations/two dates/five winners. Identical stateless signal contexts may reuse a frozen profile across prospective live/test logging modes; evaluation cohorts stay separate and calibration lineage is retained. No transfer for Sunrise or AegisQuant. Independent static protection / 24h forward bid/ask tick tests, not a replay of every strategy dynamic/session exit or funded portfolio validation. Freeze before evaluation; no calibration-trade results reused. MAE stop is capped at original distance because exits censor excursions. Tick gaps/empty windows censor outcomes; first eligible entry is never retried. Spread and tick-crossing stop gaps are modeled. Gross conversion uses recorded signal risk valuation; full net requires exact broker lifecycle cost evidence. Unknown commissions, financing, conversion and execution costs stay unknown. No promotions, live settings, limits or halt resets.')
