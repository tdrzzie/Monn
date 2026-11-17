"""Broker cash costs apply only to demonstrably matching shadow executions."""
from collections import defaultdict
import math


def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def same(a,b):return finite(a) and finite(b) and abs(a-b)<1e-8


def costs(shadow,contexts,deals):
    out=dict(commission_usc=None,swap_usc=None,slippage_adjustment_usc=None,complete=False,
             source='No matching broker execution',position_id=None)
    matches=[]
    for pid in contexts.get(shadow['decision_id'],[]):
        ds=deals.get(pid,[]);opening=[d for d in ds if d.get('entry')==0];closing=[d for d in ds if d.get('entry') in (1,3)]
        if len(opening)!=1:continue
        d=opening[0]
        if (d.get('symbol')!=shadow['symbol'] or d.get('type')!=shadow['side'] or not same(d.get('volume'),shadow['volume'])
            or not same(d.get('price'),shadow['entry']) or not finite(d.get('time')) or int(d['time'])!=int(shadow['opened'])):continue
        # Restrict to the same decision's exact observed fill, never a per-lot tariff inferred from another trade.
        matches.append((pid,ds,d,closing))
    if len(matches)!=1:
        if len(matches)>1:out['source']='Ambiguous broker lifecycle matches'
        return out
    pid,ds,opening,closing=matches[0];out.update(position_id=pid,source='Exact opening fill; full lifecycle not verified')
    if finite(opening.get('commission')) and finite(opening.get('fee')):
        out['commission_usc']=opening['commission']+opening['fee']
    if shadow['status']!='closed' or len(closing)!=1 or len(ds)!=2:return out
    close=closing[0]
    if (not same(close.get('volume'),shadow['volume']) or not same(close.get('price'),shadow.get('exit_price'))
        or close.get('type')!=1-shadow['side'] or not finite(close.get('time')) or not finite(shadow.get('closed'))
        or int(close['time'])!=int(shadow['closed'])
        or not all(finite(d.get(k)) for d in ds for k in ('profit','commission','fee','swap'))):return out
    actual_gross=sum(d['profit'] for d in ds)
    if not same(actual_gross,shadow.get('gross_usc')):return out
    # Same fills and same realized monetary gross: execution delta relative to this shadow is observed zero.
    # This does not estimate slippage for any other counterfactual trade.
    out.update(commission_usc=sum(d['commission']+d['fee'] for d in ds),swap_usc=sum(d['swap'] for d in ds),
               slippage_adjustment_usc=actual_gross-shadow['gross_usc'],complete=True,
               source='Exact broker lifecycle: same decision, side, volume, prices, entry/exit seconds and gross cash P/L')
    return out


def load(db):
    import json
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    contexts=defaultdict(list);deals=defaultdict(list)
    if not {'live_context','live_deals'}<=tables:return contexts,deals
    for pid,decision,attribution in db.execute('SELECT position_id,decision_id,attribution FROM live_context'):
        if decision and attribution=='opening deal/order match':contexts[decision].append(pid)
    for pid,payload in db.execute('SELECT position_id,payload FROM live_deals'):
        deals[pid].append(json.loads(payload))
    return contexts,deals
