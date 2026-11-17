"""Read-only Aegis signal bridge for independent protective-exit research.
Native live readiness gates remain untouched. This bridge never submits orders.
"""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import zlib


def sources(state,risk,native,now,start):
    from .excursion_tests import digest,signal_context
    root=Path(state).parent.parent
    folder=root/'aegisquant';journal=folder/'.runtime/journal.sqlite3'
    if not journal.exists():return [],{'status':'MISSING_JOURNAL'}
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
    from aegisquant_bot.runtime import levels
    from aegisquant_bot.qualified_replay import revalidate
    from aegisquant_bot.library import version
    output=[];reasons={}
    def reject(reason):reasons[reason]=reasons.get(reason,0)+1
    with closing(sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        statuses={r['strategy']:dict(status=r['status'],reason=r['reason'],time=r['time'],observations=r['observations']) for r in db.execute('SELECT o.strategy,o.status,o.reason,o.time,latest.observations FROM observations o JOIN (SELECT strategy,MAX(id) id,COUNT(*) observations FROM observations GROUP BY strategy) latest ON o.id=latest.id')}
        observations=db.execute("SELECT * FROM observations WHERE status='SIGNAL' AND time>? AND time<=? ORDER BY id",(max(start,now-300),now)).fetchall()
        for row in observations:
            try:
                raw=zlib.decompress(db.execute('SELECT payload FROM inputs WHERE id=?',(row['input_id'],)).fetchone()[0])
                if hashlib.sha256(raw).hexdigest()!=row['input_id']:raise ValueError('INPUT_CHECKSUM')
                snap=json.loads(raw);result=json.loads(row['result'])
                prior=db.execute('SELECT result FROM observations WHERE strategy=? AND symbol=? AND version=? AND id<? ORDER BY id DESC LIMIT 1',(row['strategy'],row['symbol'],row['version'],row['id'])).fetchone()
                if prior is None or result.get('trace',{}).get('reconstructed_initial_state'):raise ValueError('STARTUP_RECONSTRUCTION')
                if row['version']!=version(row['strategy']):raise ValueError('SPEC_VERSION_DRIFT')
                c=snap['contract'];quote=snap['quote'];clock=snap['now']
                if not 0<=clock-quote['event_at']<=5:raise ValueError('STALE_SIGNAL_QUOTE')
                unit=c.get('trade_tick_value_loss',0)/c['trade_tick_size']
                if not math.isfinite(unit) or unit<=0:raise ValueError('MISSING_RECORDED_TICK_VALUE')
                account=native.account_info()
                if not account or not math.isfinite(account.equity) or account.equity<=0:raise ValueError('UNAVAILABLE_EQUITY_FOR_SHADOW_CEILING')
                ceiling=min(risk['risk_budget_usc'],account.equity*.0025);budget=ceiling*.9
                for proposal in result.get('signals',[]):
                    if proposal.get('legs') or 'stop' not in proposal or proposal.get('side') not in ('long','short'):
                        reject('UNSUPPORTED_MULTI_LEG_OR_CONTROLLER');continue
                    if proposal.get('execution_eligible') is False or proposal.get('not_before',clock)>clock:
                        reject('ORIGINAL_ELIGIBILITY_OR_DEFERRED_ENTRY');continue
                    job=dict(strategy=row['strategy'],snapshot=snap,state_before=json.loads(prior[0])['state'],proposal=proposal)
                    _,error=revalidate(job)
                    if error:reject(error);continue
                    request=levels(proposal,quote,SimpleNamespace(**c),proposal.get('spread_at_signal',quote['ask']-quote['bid']))
                    entry=request['price'];distance=abs(entry-request['sl'])
                    if quote['ask']-quote['bid']>.1*distance:raise ValueError('ORIGINAL_SPREAD_FILTER')
                    cap=risk.get('max_volume_by_symbol',{}).get(row['symbol'],risk['max_volume'])
                    volume=min(cap,c['volume_max'],budget/(distance*unit))
                    volume=round(math.floor(volume/c['volume_step']+1e-9)*c['volume_step'],8)
                    if volume<c['volume_min']:raise ValueError('MINIMUM_LOT_RISK_GATE')
                    sid='aegis-shadow-'+digest([row['strategy'],row['symbol'],row['input_id'],proposal])
                    features={'parameters':{'proposal_stop_kind':proposal['stop']['kind'],'timeframe':proposal.get('timeframe')}}
                    r=dict(id=sid,decision_id=sid,bot='aegisquant',strategy=row['strategy'],symbol=row['symbol'],side=request['type'],volume=volume,
                        entry=entry,sl=request['sl'],tp=request['tp'],initial_risk=distance*unit*volume,opened=clock,closed=None,status='open',
                        mode='test_only',features=json.dumps(features),context=json.dumps({'contract':c}),version_id=row['version'],known_at=None,
                        risk_ceiling_usc=ceiling,input_id=row['input_id'],source_origin='AEGIS_REVALIDATED_PROTECTIVE_COUNTERFACTUAL',mae_r=0.,mfe_r=0.)
                    files={str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder/'aegisquant_bot').glob('*.py')}
                    code={'signal_sha256':digest({'spec':row['version'],'files':files,'bridge_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})}
                    if proposal.get('gap_filter'):
                        atr=proposal.get('atr');tf=proposal.get('timeframe')
                        if not isinstance(atr,(int,float)) or atr<=0:raise ValueError('MISSING_ORIGINAL_ATR_GAP_LIMIT')
                        r['original_gap_limit']=.5*atr;r['signal_bid_close']=snap['bars'][tf][-1]['close']
                    r['signal_context']=signal_context(r,features,code);r['alternate_context']=None
                    output.append(r)
            except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError) as exc:reject(str(exc))
    return output,dict(status='READ_ONLY_SIGNAL_BRIDGE',recent_signals=len(observations),compatible=len(output),reasons=reasons,strategy_statuses=statuses,
        limitation='Static protective-exit counterfactual only; original controllers, pending/two-leg flows and dynamic exits are not emulated. Recorded tick-value gross valuation, current conservative equity cap, unknown costs; no live qualification or halt override.')
