import json
from types import SimpleNamespace as S
import pytest

from exness_arena import common, control, identity
from exness_arena.guard import Gateway, GuardError
from exness_arena.tests.test_guard import setup, prepare, request


NEW = [(bot,name) for bot in ['Monn','MT5-Python-Trading','tradingbot']
       for name,spec in identity.strategies(bot).items() if spec.get('adapter')]


@pytest.mark.parametrize('bot,strategy',NEW)
def test_disabled_combined_cap_allows_entry_above_former_total_but_preserves_position_cap(setup,bot,strategy):
    primary,n=setup
    cfg=dict(primary.config)
    cfg.update(enabled_bots=['Monn','MT5-Python-Trading','tradingbot'],risk_budget_usc=3.5,total_risk_budget_usc=None,max_volume=.01)
    cfg['symbols']=['EURUSDc','XAUUSDc']
    n.ps=[S(magic=26111302,type=0,symbol='EURUSDc',volume=.08,price_open=1.1,sl=1.099)]
    g=Gateway(n,bot,cfg,strategy=strategy)
    symbol=identity.strategies(bot)[strategy].get('symbols',['EURUSDc'])[0]
    prepared=prepare(g,request(symbol=symbol))
    assert prepared['volume']==.01 and prepared['magic']==g.magic
    assert g.loss(0,symbol,prepared['volume'],prepared['price'],prepared['sl'])<=3.5
    assert not n.sent  # Preparation does not submit a real or simulated broker order.


@pytest.mark.parametrize('cap',[0,-1,float('nan'),float('inf'),'disabled'])
def test_invalid_cap_is_not_silently_interpreted_as_disabled(setup,cap):
    g,n=setup;g.config['total_risk_budget_usc']=cap
    with pytest.raises(GuardError,match='Invalid combined'):prepare(g,request())


def test_missing_cap_setting_requires_explicit_null(setup):
    g,n=setup;del g.config['total_risk_budget_usc']
    with pytest.raises(GuardError,match='missing'):prepare(g,request())


def test_profit_gate_and_drawdown_halt_remain_with_no_combined_cap(setup):
    g,n=setup;g.config['total_risk_budget_usc']=None
    n.ps=[S(magic=g.magic,symbol='EURUSDc',type=0,identifier=1,profit=-1,swap=0)]
    with pytest.raises(GuardError,match='profitable'):prepare(g,request())
    n.ps=[];n.account.equity=980
    with pytest.raises(GuardError,match='drawdown'):prepare(g,request())


def test_strategy_and_repo_loss_limits_remain_with_no_combined_cap(setup):
    g,n=setup;g.config.update(total_risk_budget_usc=None,strategy_loss_budget_usc=2)
    n.ds=[S(magic=g.magic,position_id=10,entry=0,profit=0,swap=0,commission=0,fee=0),
          S(magic=0,position_id=10,entry=1,profit=-2,swap=0,commission=0,fee=0)]
    with pytest.raises(GuardError,match='Per-strategy'):prepare(g,request())
    n.ds[-1].profit=-11
    with pytest.raises(GuardError,match='Per-bot'):prepare(g,request())


def test_config_transition_changes_only_cap_and_does_not_reset_halts(setup,tmp_path,monkeypatch):
    g,n=setup
    monkeypatch.setattr(control,'STATE',tmp_path)
    before=dict(g.config)
    (tmp_path/'settings.json').write_text(json.dumps(before))
    (tmp_path/'STOP').touch()
    with common.locked() as db:db.execute("INSERT INTO flags VALUES('halt','existing loss halt')")
    control.remove_combined_cap()
    after=common.settings()
    assert after.pop('total_risk_budget_usc') is None
    before.pop('total_risk_budget_usc')
    assert before==after
    with common.locked() as db:assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()[0]=='existing loss halt'


def test_live_fixed_cap_can_be_disabled_without_removing_headroom_or_halt(setup):
    g, n = setup
    g.config.update(total_risk_budget_usc=None, risk_budget_usc=3.5,
                    bot_loss_budget_usc=5.24, strategy_loss_budget_usc=5.24, max_volume=.01)
    order = request(sl=1.0961)
    with pytest.raises(GuardError, match='Minimum broker volume'):
        prepare(g, order)
    g.config['live_per_position_cap_disabled'] = True
    assert prepare(g, order)['volume'] == .01  # 4 USC stop, above the old 3.50 ceiling.
    with pytest.raises(GuardError, match='Minimum broker volume'):
        prepare(g, request(sl=1.0941))
    with common.locked() as db:
        db.execute("INSERT INTO flags VALUES('halt','account drawdown')")
    with pytest.raises(GuardError, match='Trial is halted'):
        prepare(g, order)
    assert not n.sent


def test_live_reversal_preflight_uses_flag_and_paper_keeps_fixed_ceiling(setup):
    from exness_arena.shadow_plan import size
    g, n = setup
    g.config.update(risk_budget_usc=3.5, live_per_position_cap_disabled=True, max_volume=.01)
    quote = {'bid': 1.1, 'ask': 1.1001}
    candidate = {'side': 0, 'entry': 1.1001, 'sl': 1.0961, 'tp': 1.1041}
    args = (candidate, n, g.config, 'EURUSDc', n.symbol_info('EURUSDc'), .01, quote)
    with pytest.raises(ValueError, match='Minimum volume'):
        size(*args)
    assert size(*args, for_live_recipe=True)['volume'] == .01


@pytest.mark.parametrize(('repo_limit', 'strategy_limit', 'blocked_by'),
                         [(5.24, 10, 'Per-bot'), (10, 5.24, 'Per-strategy')])
def test_gold_only_exemption_preserves_other_symbols_and_drawdown(setup, repo_limit, strategy_limit, blocked_by):
    g, n = setup
    g.config.update(total_risk_budget_usc=None, live_per_position_cap_disabled=True,
                    gold_live_loss_limits_disabled=True, bot_loss_budget_usc=repo_limit,
                    strategy_loss_budget_usc=strategy_limit, max_volume=.01)
    g.config['symbols'] += ['XAUUSDc', 'XAGUSDc']
    # Booked Gold losses still count when sizing any non-Gold entry.
    n.ds = [S(magic=g.magic, symbol='XAUUSDc', position_id=9, entry=0,
              profit=0, swap=0, commission=0, fee=0),
            S(magic=0, symbol='XAUUSDc', position_id=9, entry=1,
              profit=-6, swap=0, commission=0, fee=0)]
    gold = request(symbol='XAUUSDc', sl=1.0626)  # 37.50 USC at the fixture's minimum lot.
    assert prepare(g, gold)['volume'] == .01
    for symbol in ('EURUSDc', 'XAGUSDc'):
        with pytest.raises(GuardError, match=blocked_by):
            prepare(g, request(symbol=symbol))
    n.account.equity = 980
    assert 'drawdown' in g.order_send(gold).comment
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone() == ('account drawdown',)
    n.account.equity = 1000
    with pytest.raises(GuardError, match='Trial is halted'):
        prepare(g, gold)
    assert not n.sent


def test_gold_exemption_does_not_override_enabled_fixed_or_combined_limits(setup):
    g, n = setup
    g.config.update(gold_live_loss_limits_disabled=True, max_volume=.01)
    g.config['symbols'].append('XAUUSDc')
    order = request(symbol='XAUUSDc', sl=1.0961)
    with pytest.raises(GuardError, match='Minimum broker volume'):
        prepare(g, order)
    g.config.update(live_per_position_cap_disabled=True, total_risk_budget_usc=3.5)
    with pytest.raises(GuardError, match='Minimum broker volume'):
        prepare(g, order)
    assert not n.sent
