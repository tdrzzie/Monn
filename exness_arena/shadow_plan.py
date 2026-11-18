"""Predeclared paper experiments. This module has no order-submission capability."""
import math
from .volume import entry_volume_cap

LEGACY_PLAN = 'targets-v1'
PLAN_ID = 'mfe-inversion-v2'
PLAN = (
    {'key':'proposed','family':'baseline','label':'Proposed','description':'Original direction, stop and target.'},
    {'key':'target_1r','family':'target','label':'1R target','description':'Original stop; target one initial price-risk distance.'},
    {'key':'target_3r','family':'target','label':'3R target','description':'Original stop; target three initial price-risk distances.'},
    {'key':'mfe_lock_05r','family':'mfe','label':'MFE +0.5R → entry stop','description':'After observing +0.5 price-R, protect entry price; keep original target. Not net break-even after costs.'},
    {'key':'mfe_trail_1r','family':'mfe','label':'MFE +1R → 0.5R trail','description':'After observing +1 price-R, trail 0.5 initial price-R behind the observed peak; never widen. Keep original target.'},
    {'key':'reversed_swap','family':'reversal','label':'Reversed · swap SL/TP','description':'Enter opposite side on the same quote; old TP becomes SL and old SL becomes TP. Resize independently within existing limits. A fixed original TP is required.'},
)
VARIANTS = tuple(x['key'] for x in PLAN)
MFE_VARIANTS = frozenset({'mfe_lock_05r','mfe_trail_1r'})


def candidates(request, quote):
    side=request.get('type')
    if side not in (0,1):
        raise ValueError('Invalid side')
    entry=quote['ask'] if side==0 else quote['bid']
    sl=float(request.get('sl') or 0)
    tp=float(request.get('tp') or 0)
    distance=(entry-sl)*(1 if side==0 else -1)
    for variant in VARIANTS:
        candidate={'variant':variant,'side':side,'entry':entry,'sl':sl,'tp':tp}
        if variant in ('target_1r','target_3r'):
            candidate['tp']=entry+(1 if side==0 else -1)*distance*(1 if variant=='target_1r' else 3)
        elif variant=='reversed_swap':
            if not math.isfinite(tp) or tp<=0:
                candidate['error']='Unsupported reversal: original signal has no valid fixed TP to swap'
            candidate.update(side=1-side,entry=quote['bid'] if side==0 else quote['ask'],sl=tp,tp=sl)
        yield candidate


def size(candidate, native, config, symbol, contract, requested, quote, *, for_live_recipe=False):
    """Broker-valued sizing for a paper scenario or guarded live recipe."""
    if candidate.get('error'):
        raise ValueError(candidate['error'])
    r=dict(candidate);s=contract
    if s is None or s.trade_mode!=4:
        raise ValueError('Instrument is not fully tradable')
    if not all(isinstance(r[k],(int,float)) and math.isfinite(r[k]) for k in ('entry','sl','tp')) or r['entry']<=0 or r['sl']<=0:
        raise ValueError('No valid proposed stop or target')
    for key in ('sl','tp'):
        r[key]=round(round(r[key]/s.trade_tick_size)*s.trade_tick_size,s.digits) if r[key] else 0.
    direction=1 if r['side']==0 else -1
    distance=(r['entry']-r['sl'])*direction
    minimum=max(s.trade_stops_level*s.point,0)
    if distance<=minimum or quote['ask']-quote['bid']>.15*distance:
        raise ValueError('Stop side/distance or spread check failed')
    if r['tp']<0 or (r['tp'] and (r['tp']-r['entry'])*direction<=minimum):
        raise ValueError('Invalid target')
    unit=native.order_calc_profit(r['side'],symbol,1.,r['entry'],r['sl'])
    if unit is None or not math.isfinite(unit) or unit>=0:
        raise ValueError('Stop valuation unavailable')
    requested=float(requested)
    if not math.isfinite(requested) or requested<=0:
        raise ValueError('Invalid requested paper volume')
    ceiling = None if for_live_recipe and config.get('live_per_position_cap_disabled') is True else config['risk_budget_usc']
    volume=min(requested,entry_volume_cap(symbol,config),s.volume_max)
    if ceiling is not None:
        volume=min(volume,ceiling*.9/-unit)
    volume=round(math.floor(volume/s.volume_step+1e-9)*s.volume_step,8)
    if volume<s.volume_min-1e-9:
        raise ValueError('Minimum volume does not fit paper risk ceiling')
    pnl=native.order_calc_profit(r['side'],symbol,volume,r['entry'],r['sl'])
    margin=native.order_calc_margin(r['side'],symbol,volume,r['entry'])
    account=native.account_info()
    if pnl is None or not math.isfinite(pnl) or pnl>=0 or (ceiling is not None and -pnl>ceiling):
        raise ValueError('Paper risk check failed')
    if (margin is None or not math.isfinite(margin) or margin<0 or account is None
            or not math.isfinite(account.margin_free) or account.margin_free<=0 or margin>account.margin_free*.1):
        raise ValueError('Paper margin check failed')
    r.update(volume=volume,initial_risk=-pnl,initial_sl=r['sl'])
    return r


def tighten(row, state, high, low, stamp, contract):
    """Use only newly observed favorable prices; return a virtual stop update.

    The caller checks old protection first and activates this stop prospectively.
    A completed bar's earlier extrema never fill a stop that did not yet exist.
    """
    if row['variant'] not in MFE_VARIANTS or row.get('plan_id')!=PLAN_ID:
        return None
    side=row['side'];sign=1 if side==0 else -1
    original=row.get('initial_sl') or row['sl']
    distance=abs(row['entry']-original)
    if distance<=0:
        return None
    peak=max(state.get('peak',row['entry']),high) if side==0 else min(state.get('peak',row['entry']),low)
    state['peak']=peak
    trigger=.5 if row['variant']=='mfe_lock_05r' else 1.
    if (peak-row['entry'])*sign+distance*1e-10<trigger*distance:
        return None
    candidate=row['entry'] if row['variant']=='mfe_lock_05r' else peak-sign*.5*distance
    # Round toward the less aggressive protection boundary, without ever loosening.
    tick=contract.trade_tick_size
    candidate=(math.floor(candidate/tick+1e-8) if side==0 else math.ceil(candidate/tick-1e-8))*tick
    candidate=round(candidate,contract.digits)
    new=max(row['sl'],candidate) if side==0 else min(row['sl'],candidate)
    if abs(new-row['sl'])<tick*.5:
        return None
    state.update(armed=True,armed_at=state.get('armed_at',stamp))
    return new
