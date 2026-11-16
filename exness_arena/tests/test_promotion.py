import hashlib
import json
from types import SimpleNamespace as S

import pytest

from exness_arena import promotion, research, identity, runner, common
from exness_arena.tests.test_guard import setup, prepare, request
from exness_arena.shadow_plan import candidates, size
from exness_arena.tests.test_research import lab, proposal
from exness_arena.tests.test_adapters import bars


def test_unselected_and_unknown_strategies_fail_closed():
    cfg={'strategy_stages':{'Monn':{'price_action_1r':'live_candidate'}}}
    assert promotion.live_selected(cfg,'Monn','price_action_1r')
    assert not promotion.live_selected(cfg,'Monn','ma_cross')
    assert not promotion.live_selected(cfg,'aegisquant','TF01')
    assert not promotion.live_selected({'strategy_stages':{}},'Monn','ma_cross')


def test_test_only_blocks_native_entry_but_retains_existing_position_exit(setup):
    g,n=setup
    g.config['strategy_stages']={}
    with pytest.raises(RuntimeError,match='test-only'):prepare(g,request())
    n.ps=[S(ticket=8,identifier=8,magic=g.magic,symbol='EURUSDc',type=0,volume=.01,sl=1.099)]
    prepared=prepare(g,request(position=8,volume=.01))
    assert prepared['position']==8 and prepared['type']==1
    assert not n.sent


def test_selected_stage_never_overrides_account_drawdown(setup,monkeypatch):
    g,n=setup
    g.config['strategy_stages']={'Monn':{'ma_cross':'live_candidate'}}
    monkeypatch.setattr(promotion,'frozen_issue',lambda *a:None)
    n.account.equity=980
    assert 'drawdown' in g.order_send(request()).comment
    with common.locked() as db:assert db.execute("select value from flags where name='halt'").fetchone()[0]=='account drawdown'
    assert not n.sent


def test_frozen_policy_source_and_artifact_drift_block(tmp_path,monkeypatch):
    source=tmp_path/'original.py';source.write_text('original')
    policy=tmp_path/'policy.json';policy.write_text(json.dumps({'candidates':{'Monn/x':{'source_sha256':{'original.py':hashlib.sha256(source.read_bytes()).hexdigest()}}}}))
    monkeypatch.setattr(promotion,'ROOT',tmp_path);monkeypatch.setattr(promotion,'POLICY',policy)
    expected=hashlib.sha256(policy.read_bytes()).hexdigest()
    assert promotion.frozen_issue('Monn','x','EURUSDc',expected) is None
    source.write_text('changed')
    assert 'source changed' in promotion.frozen_issue('Monn','x','EURUSDc',expected)
    assert 'policy changed' in promotion.frozen_issue('Monn','x','EURUSDc','wrong')
    assert promotion.frozen_issue('Monn','unknown','EURUSDc')


@pytest.mark.parametrize('side',[0,1])
@pytest.mark.parametrize('variant',['reversed_swap','target_1r'])
def test_recipe_exactly_matches_existing_shadow_transform_and_sizing(setup,monkeypatch,side,variant):
    g,n=setup;captured=[]
    g.config['risk_budget_usc']=3.5
    monkeypatch.setattr(common,'settings',lambda:g.config)
    # Fix one quote so the same modeled inputs can be compared exactly.
    quote=n.symbol_info_tick('EURUSDc');n.symbol_info_tick=lambda name:quote
    g.order_send=lambda req:captured.append(req) or S(retcode=10006)
    req=request(type=side,symbol='EURUSDc',sl=1.0991 if side==0 else 1.1011,tp=1.1021 if side==0 else 1.0981)
    selected=next(c for c in candidates(req,vars(quote)) if c['variant']==variant)
    expected=size(selected,g,g.config,'EURUSDc',n.symbol_info('EURUSDc'),req['volume'],vars(quote))
    promotion.RecipeGateway(g,'original',variant).order_send(req)
    assert len(captured)==1
    actual=captured[0]
    assert (actual['type'],actual['sl'],actual['tp'],actual['volume'])==(expected['side'],expected['sl'],expected['tp'],expected['volume'])
    assert g.strategy=='ma_cross' and not n.sent


def test_reversal_without_fixed_target_cannot_submit(setup,monkeypatch):
    g,n=setup;monkeypatch.setattr(common,'settings',lambda:g.config)
    result=promotion.RecipeGateway(g,'original','reversed_swap').order_send(request(tp=0))
    assert result.retcode==10006 and not n.sent


def test_derived_owners_do_not_relabel_original_positions(monkeypatch):
    monkeypatch.setattr(runner,'prepare_profile',lambda kind:None)
    for b,s,source in [('tradingbot','bollinger_reversed','bollinger_revert'),('tradingbot','zscore_reversed','zscore_fade'),('Monn','price_action_1r','price_action')]:
        assert identity.strategies(b)[s]['source_strategy']==source
        assert identity.resolve(identity.strategies(b)[s]['magic'])==b+'/'+s
        assert identity.strategies(b)[s]['magic']!=identity.strategies(b)[source]['magic']
        assert callable(runner.cycle_for(b,s)[1])


def test_test_only_observed_gateway_never_delegates_orders():
    class Denied:
        def symbol_info_tick(self,s):return S(bid=1.,ask=1.01)
        def order_send(self,r):raise AssertionError('native submission')
    rec=S(cfg={'strategy_stages':{}},bot='Monn',strategy='ma_cross',symbol='EURUSDc',attempts=[])
    gateway=research.ObservedGateway(Denied(),rec,paper=False)
    result=gateway.order_send({'symbol':'EURUSDc'})
    assert result.retcode==10006 and len(rec.attempts)==1


def test_test_only_is_prospective_cohort_and_logging_failure_cannot_go_live(lab,monkeypatch):
    g,n,path=lab
    cfg=json.loads((path/'settings.json').read_text());cfg['strategy_stages']={}
    (path/'settings.json').write_text(json.dumps(cfg))
    research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    with research.connect() as db:
        assert db.execute('select mode from research_decisions').fetchone()[0]=='test_only'
    monkeypatch.setattr(research,'Recorder',lambda *a:(_ for _ in ()).throw(RuntimeError('unavailable journal')))
    with pytest.raises(RuntimeError,match='Paper research initialization'):
        research.evaluate(proposal,g,'Monn','ma_cross','EURUSDc',bars())
    assert not n.sent


def test_legacy_price_action_target_uses_tick_rounded_original_stop(setup,monkeypatch):
    g,n=setup;sent=[]
    monkeypatch.setattr(common,'settings',lambda:g.config)
    g.order_send=lambda req:sent.append(req) or S(retcode=10006)
    promotion.RecipeGateway(g,'price_action','target_1r').order_send(request(symbol='EURUSDc',sl=1.099113,tp=1.2))
    assert sent[0]['sl']==1.09911 and sent[0]['tp']==1.10109
    assert not n.sent


def test_missing_and_changed_private_artifact_pins_block(tmp_path,monkeypatch):
    policy=tmp_path/'policy.json';policy.write_text(json.dumps({'candidates':{'tradingbot/x':{'source_strategy':'base','source_sha256':{}}}}))
    monkeypatch.setattr(promotion,'POLICY',policy);monkeypatch.setattr(common,'STATE',tmp_path)
    folder=tmp_path/'artifacts/aurum_rules/base/EURUSDc';folder.mkdir(parents=True)
    file=folder/'model.pkl';file.write_bytes(b'fixture')
    pins={'base':{'EURUSDc':{'model.pkl':hashlib.sha256(file.read_bytes()).hexdigest()}}}
    assert promotion.frozen_issue('tradingbot','x','EURUSDc',artifact_pins=pins) is None
    assert 'No frozen' in promotion.frozen_issue('tradingbot','x','USDJPYc',artifact_pins=pins)
    file.write_bytes(b'changed')
    assert 'artifact changed' in promotion.frozen_issue('tradingbot','x','EURUSDc',artifact_pins=pins)


def test_test_only_keeps_guarded_owned_position_management_available():
    sent=[]
    class Managed:
        def symbol_info_tick(self,s):return S(bid=1.,ask=1.01)
        def order_send(self,r):sent.append(r);return S(retcode=10009)
    rec=S(cfg={'strategy_stages':{}},bot='Monn',strategy='ma_cross',symbol='EURUSDc',attempts=[],mode='test_only')
    wrapped=research.ObservedGateway(Managed(),rec)
    assert wrapped.order_send({'symbol':'EURUSDc','position':8}).retcode==10009
    assert len(sent)==1
    wrapped.order_send({'symbol':'EURUSDc','type':0})
    assert len(sent)==1
