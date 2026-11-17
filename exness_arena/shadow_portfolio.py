"""Prospective independent-scenario portfolios; no orders or live policy writes."""
from collections import defaultdict
from contextlib import closing
import json
import math
import sqlite3
import time
from pathlib import Path

KEYS=('bot','strategy','mode','plan_id','variant')
MAX_GAP=120


def valid(x):
    return isinstance(x,(int,float)) and math.isfinite(x)


def metrics(rows):
    rows=sorted(rows,key=lambda x:x['time'])
    if not rows:return {}
    peak=None;dd=0.;start=None;longest=0.;observed_under=0.;covered=0.;gaps=0;last=None;incomplete=0
    margins=[];last_valid=None
    for r in rows:
        if valid(r.get('margin_usc')):margins.append(r['margin_usc'])
        if r.get('equity_usc') is None:
            incomplete+=1;gaps+=1;last=None;start=None;continue
        t=r['time'];v=r['equity_usc'];last_valid=r
        gap=last is not None and t-last['time']>MAX_GAP
        if gap:gaps+=1;start=None
        if last is not None and not gap:
            elapsed=t-last['time'];covered+=elapsed
            if last['equity_usc']<last['peak'] and v<max(peak,v):observed_under+=elapsed
        peak=v if peak is None else max(peak,v);dd=max(dd,peak-v)
        if v<peak:
            if start is None:start=t
            longest=max(longest,t-start)
        else:start=None
        last=dict(r,peak=peak)
    return dict(samples=len(rows),complete_equity_samples=len(rows)-incomplete,incomplete_samples=incomplete,
        first_sample=rows[0]['time'],last_sample=rows[-1]['time'],equity_usc=rows[-1].get('equity_usc'),last_valid_equity_usc=last_valid['equity_usc'] if last_valid else None,
        window_return_usc=last_valid['equity_usc']-next(r['equity_usc'] for r in rows if r.get('equity_usc') is not None) if last_valid else None,
        sampled_max_drawdown_usc=dd if peak is not None else None,
        current_underwater_seconds=(rows[-1]['time']-start if start is not None else 0) if last else None,
        longest_observed_underwater_seconds=longest if peak is not None else None,observed_underwater_seconds=observed_under if peak is not None else None,
        covered_seconds=covered,gaps=gaps,peak_margin_usc=max(margins) if margins else None,
        margin_usc=rows[-1].get('margin_usc'),margin_missing_samples=sum(r.get('margin_usc') is None for r in rows),
        historical_equity_available=False,method='Sampled prospective gross scenario portfolio; not a funded replay')


def connect(path):
    db=sqlite3.connect(path,timeout=3)
    db.execute('CREATE TABLE IF NOT EXISTS samples (key TEXT,time REAL,payload TEXT,PRIMARY KEY(key,time))')
    return db


def collect(native,state,now=None):
    injected_time=now is not None
    state=Path(state);now=time.time() if now is None else now
    if (state/'shadow_portfolios.sqlite3').exists():
        with closing(connect(state/'shadow_portfolios.sqlite3')) as recent:
            last=recent.execute('SELECT MAX(time) FROM samples').fetchone()[0]
        if last is not None and now-last<60:return summary(state)
    with closing(sqlite3.connect((state/'research.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute('SELECT s.*,d.mode FROM shadows s JOIN research_decisions d ON d.id=s.decision_id')]
    groups=defaultdict(list)
    for r in rows:groups[tuple(r[k] for k in KEYS)].append(r)
    symbols={r['symbol'] for r in rows if r['status']=='open'}
    quotes={symbol:native.symbol_info_tick(symbol) for symbol in symbols}
    # Wait once for a small broker clock lead; never backdate a quote.
    if not injected_time:
        lead=max([q.time-time.time() for q in quotes.values() if q is not None]+[0.])
        if 0<lead<=3:time.sleep(lead+.01)
        now=time.time()
    marks={}
    for symbol,side in {(r['symbol'],r['side']) for r in rows if r['status']=='open'}:
        try:
            q=quotes[symbol];s=native.symbol_info(symbol)
            if not q or not s or not valid(q.bid) or not valid(q.ask) or q.bid<=0 or q.ask<q.bid or not 0<=now-q.time<=30:raise ValueError('unavailable/stale quote')
            close=q.bid if side==0 else q.ask;entry=q.ask if side==0 else q.bid
            step=s.trade_tick_size
            unit=native.order_calc_profit(side,symbol,1.,close-step if side==0 else close+step,close)/step
            margin=native.order_calc_margin(side,symbol,1.,entry)
            if not valid(unit) or unit<=0:raise ValueError('unknown valuation')
            marks[symbol,side]=(close,unit,margin if valid(margin) and margin>=0 else None)
        except Exception:marks[symbol,side]=None
    with closing(connect(state/'shadow_portfolios.sqlite3')) as db:
        for key,items in groups.items():
            closed=[r for r in items if r['status']=='closed'];opened=[r for r in items if r['status']=='open']
            realized=sum(r['gross_usc'] for r in closed if valid(r['gross_usc']))
            missing=sum(marks.get((r['symbol'],r['side'])) is None for r in opened)
            bad_closed=sum(not valid(r['gross_usc']) for r in closed)
            floating=0.;margin=0.;missing_margin=0
            for r in opened:
                m=marks.get((r['symbol'],r['side']))
                if m is None:missing_margin+=1;continue
                price,unit,rate=m
                floating+=(price-r['entry'])*(1 if r['side']==0 else -1)*r['volume']*unit
                if rate is None:missing_margin+=1
                else:margin+=r['volume']*rate
            sample=dict(time=now,realized_usc=realized,floating_usc=None if missing else floating,
                equity_usc=None if missing or bad_closed else realized+floating,
                margin_usc=None if missing_margin else margin,open_positions=len(opened),closed_positions=len(closed),
                missing_marks=missing,missing_closed_values=bad_closed,missing_margin=missing_margin,
                recipe='shadow-portfolio-v1',versions=sorted({r['version_id'] for r in items}),symbols=sorted({r['symbol'] for r in items}))
            db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?)',(json.dumps(key),now,json.dumps(sample)))
        db.commit()
    return summary(state)


def summary(state):
    path=Path(state)/'shadow_portfolios.sqlite3'
    if not path.exists():return {'portfolios':[],'status':'WAITING_FOR_PROSPECTIVE_SAMPLES'}
    groups=defaultdict(list)
    with closing(connect(path)) as db:
        for key,raw in db.execute('SELECT key,payload FROM samples ORDER BY time'):groups[key].append(json.loads(raw))
    result=[]
    for key,rows in groups.items():
        item=dict(zip(KEYS,json.loads(key)));item.update(metrics(rows));item['versions']=rows[-1]['versions'];item['version_count']=len(item['versions']);item['open_positions']=rows[-1]['open_positions'];result.append(item)
    return dict(portfolios=result,updated_at=time.time(),paper_only=True,
        limitations='Prospective sampled gross P/L only. No historical equity backfill. Versions are pooled explicitly within strategy/cohort/plan/variant; not independent validation. Simultaneous signals are hypothetical and live loss/addition gates are not simulated. Margin sums current standalone linear per-lot estimates; broker hedging offsets, tiers, historical financing/conversion, fees and extra slippage are excluded. No capital baseline is assumed: percentage drawdown and margin utilization are unknown. Missing marks remain null. Gaps over 120 seconds break underwater episodes; durations are observed lower bounds. Flat or one-sample paths do not establish safety.')
