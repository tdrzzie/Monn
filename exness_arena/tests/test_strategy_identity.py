from types import SimpleNamespace as S
import pytest

from exness_arena import common, identity
from exness_arena.guard import Gateway, GuardError
from exness_arena.tracker import summarize
from exness_arena.universe import coverage
from exness_arena.tests.test_guard import setup, prepare, request


@pytest.fixture
def alternate(setup, monkeypatch):
    primary, native = setup
    mapping = common.REGISTRY['Monn']['strategies']
    monkeypatch.setitem(mapping, 'test_alternate', {'magic': 26111501})
    cfg = dict(primary.config)
    cfg['enabled_strategies'] = {'Monn': ['ma_cross', 'test_alternate']}
    return Gateway(native, 'Monn', cfg, strategy='test_alternate'), native


def pos(magic, profit=-1, symbol='EURUSDc'):
    return S(ticket=1,identifier=1,magic=magic,symbol=symbol,type=0,volume=.01,
             price_open=1.1,sl=1.0995,tp=1.12,profit=profit,swap=0)


def deal(ticket, magic, pid, entry=0, profit=0):
    return S(ticket=ticket,time_msc=ticket,magic=magic,position_id=pid,entry=entry,type=0 if entry==0 else 1,
             symbol='EURUSDc',profit=profit,swap=0,commission=0,fee=0)


def test_all_strategy_magics_are_unique_and_legacy_positions_keep_identity():
    mapping=identity.specs()
    assert len({s['magic'] for s in mapping.values()})==len(mapping)
    assert identity.resolve(26111302,'EURUSDc')=='MT5-Python-Trading/crossover'
    assert identity.resolve(26111303,'GBPUSDc')=='mt5_live_trading_bot/sunrise_gbpusd'
    assert identity.resolve(26111402,'GBPUSDc')=='mt5_live_trading_bot/sunrise_gbpusd'


def test_losing_position_in_other_strategy_does_not_block_first_entry(alternate):
    g,n=alternate
    n.ps=[pos(26111301,profit=-2)]
    prepared=prepare(g,request())
    assert prepared['magic']==26111501 and prepared['comment']=='trial:test_alternate'


def test_same_strategy_losing_position_blocks_addition(alternate):
    g,n=alternate
    n.ps=[pos(g.magic,profit=-2)]
    with pytest.raises(GuardError,match='profitable'):prepare(g,request())


def test_cannot_query_close_or_modify_sibling_strategy_positions(alternate):
    g,n=alternate
    n.ps=[pos(26111301)]
    assert g.positions_get()==()
    for action in [1,6]:
        with pytest.raises(GuardError,match='another bot'):
            prepare(g,{'action':action,'position':1})


def test_strategy_loss_ledger_is_separate_but_repo_backstop_remains(alternate):
    g,n=alternate
    g.config['strategy_loss_budget_usc']=2
    n.ds=[deal(1,26111301,10),deal(2,0,10,entry=1,profit=-2)]
    assert prepare(g,request())['magic']==g.magic
    other=Gateway(n,'Monn',g.config,strategy='ma_cross')
    with pytest.raises(GuardError,match='Per-strategy'):prepare(other,request())
    n.ds[-1].profit=-11
    with pytest.raises(GuardError,match='Per-bot'):prepare(g,request())


def test_manual_exit_and_partial_closes_reconcile_per_strategy_and_repo(alternate):
    g,n=alternate
    ds=[deal(1,26111301,10),deal(2,0,10,1,-1),deal(3,g.magic,20),deal(4,0,20,1,2),deal(5,0,20,1,1)]
    repo_owners={};strategy_owners={}
    repo_rows,_=summarize(ds,[],repo_owners,g.config)
    strategy_rows,_=summarize(ds,[],strategy_owners,g.config,group_specs=identity.specs())
    scoped={r['repo']:r for r in strategy_rows}
    assert scoped['Monn/ma_cross']['closed_net_usc']==-1
    assert scoped['Monn/test_alternate']['closed_net_usc']==3
    assert scoped['Monn/test_alternate']['closed_positions']==1
    assert next(r for r in repo_rows if r['repo']=='Monn')['closed_net_usc']==2
    assert repo_owners=={10:'Monn',20:'Monn'}


def test_strategy_bar_dedup_does_not_replay_legacy_or_block_sibling(alternate):
    assert common.claim_bar('Monn','EURUSDc',123)
    assert not common.claim_strategy_bar('Monn','ma_cross','EURUSDc',123)
    assert common.claim_strategy_bar('Monn','test_alternate','EURUSDc',123)
    assert not common.claim_strategy_bar('Monn','test_alternate','EURUSDc',123)


def test_strategy_coverage_keeps_both_strategies_on_same_pair(alternate):
    coverage('Monn','EURUSDc','monitoring',strategy='ma_cross')
    coverage('Monn','EURUSDc','monitoring',strategy='test_alternate')
    with common.locked() as db:
        assert db.execute('SELECT COUNT(*) FROM strategy_coverage').fetchone()[0]==2


def test_disabled_strategy_cannot_open_even_with_enabled_parent(alternate):
    g,n=alternate
    g.config['enabled_strategies']['Monn']=['ma_cross']
    with pytest.raises(GuardError,match='not enabled'):prepare(g,request())


def test_legacy_sunrise_position_is_manageable_without_retagging_broker_magic(setup):
    primary,n=setup
    g=Gateway(n,'mt5_live_trading_bot',primary.config,strategy='sunrise_gbpusd')
    n.ps=[pos(26111303,symbol='GBPUSDc')]
    assert len(g.positions_get())==1 and n.ps[0].magic==26111303
    closing=prepare(g,{'action':1,'position':1})
    assert closing['position']==1 and closing['magic']==g.magic
