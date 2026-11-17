from exness_arena.excursion_tests import fit,new_trial,replay,VARIANTS
import pytest


def training():
 return [dict(id=str(i),opened=(i%2)*86400,closed=180000+i,known_at=190000+i,mae_r=-.4,mfe_r=1.5,gross_usc=1 if i%2 else -1) for i in range(30)]


def source():
 return dict(id='s',decision_id='d',bot='x',strategy='rule',symbol='X',side=0,volume=1.,entry=100.,sl=90.,tp=120.,initial_risk=1.,opened=1000.,status='open')


def trial(variant='original',side=0):
 s=source()
 if side:s.update(side=1,sl=110.,tp=80.)
 p=dict(stop_r=.4,target_r=1.5,cutoff=900.)
 c=dict(trade_tick_size=.01,volume_step=.01,volume_min=.01,volume_max=10,digits=2,point=.01,trade_stops_level=0)
 t=new_trial(s,p,variant,c,dict(risk_budget_usc=3.5,max_volume=1.));t['risk_limit']=3.5
 return t


def q(at,bid,ask):return dict(time_msc=int(at*1000),bid=bid,ask=ask)


def test_fit_frozen_cutoff_and_support():
 r=fit(training(),200000)
 assert r['status']=='FROZEN' and r['stop_r']==.4 and r['target_r']==1.5
 rows=training()+[dict(training()[0],id='future',known_at=300000,mae_r=-100,mfe_r=100)]
 assert fit(rows,200000)['training_sha256']==r['training_sha256']
 assert fit(training()[:10],200000)['status']=='WAITING_SUPPORT'
 rows=training()
 for row in rows:row['opened']=0
 assert fit(rows,200000)['status']=='WAITING_SUPPORT'


@pytest.mark.parametrize('side',[0,1])
@pytest.mark.parametrize('variant',VARIANTS)
def test_same_ordered_tick_path_for_each_frozen_variant(side,variant):
 t=trial(variant,side)
 quotes=[q(1000.25,99.9,100),q(1000.3,99.9,100),q(1001,79 if side else 121,79.1 if side else 121.1)]
 r=replay(t,quotes,1001)
 assert r['status']=='CLOSED' and r['reason']=='target'
 assert r['opened']==1000.3 and r['initial_risk']<=3.5*.9
 assert r['gross_usc']>0


def test_first_eligible_bad_fill_is_not_retried():
 t=trial()
 r=replay(t,[q(1000.3,150,150.1),q(1001,99.9,100)],1001)
 assert r['status']=='REJECTED'


def test_stop_gap_fills_at_actual_quote_not_protective_price():
 r=replay(trial(),[q(1000.3,99.9,100),q(1001,85,85.1)],1001)
 assert r['status']=='CLOSED' and r['exit_price']==85 and r['gross_usc']==-1.5


def test_missing_stale_and_unordered_paths_never_invent_results():
 assert replay(trial(),[],1400)['status']=='CENSORED'
 assert replay(trial(),[q(1000.3,99.9,100)],1100)['status']=='CENSORED'
 assert replay(trial(),[q(1000.3,99.9,100),q(1000.2,99.9,100)],1001)['status']=='CENSORED'
 assert replay(trial(),[q(1000.3,99.9,100),q(1150,85,85.1)],1150)['status']=='CENSORED'


def test_minimum_volume_is_never_rounded_up():
 s=source();s['initial_risk']=10000
 with pytest.raises(ValueError):new_trial(s,dict(stop_r=.4,target_r=1.5),'original',dict(trade_tick_size=.01,volume_step=.01,volume_min=.01,volume_max=10,digits=2,point=.01,trade_stops_level=0),dict(risk_budget_usc=3.5,max_volume=1))

def test_dashboard_pairs_same_source_and_preserves_unknown_net(tmp_path):
 import json
 from exness_arena.excursion_tests import connect,summary
 db=connect(tmp_path)
 for variant,pnl in [('original',1),('mae_stop',2)]:
  t=dict(id=variant,source_id='s',bot='bot',strategy='rule',symbol='X',mode='test_only',signal_context='ctx',variant=variant,status='CLOSED',gross_usc=pnl,r_multiple=pnl,net_usc=None)
  db.execute('INSERT INTO trials VALUES(?,?,?,?,?,?)',(variant,'s','ctx',variant,'CLOSED',json.dumps(t)))
 db.commit();db.close()
 r=summary(tmp_path,{'bot':{'strategies':{'rule':{}}}})
 alt=next(x for x in r['comparisons'] if x['variant']=='mae_stop')
 assert alt['paired']==1 and alt['gross_delta_usc']==1 and alt['delta_mean_r']==1
 assert alt['net_pairs']==0 and alt['net_delta_usc'] is None
 assert r['catalog'][0]['status']=='WAITING_SUPPORT'
