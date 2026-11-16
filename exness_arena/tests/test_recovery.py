import sys
from types import SimpleNamespace as S

import pytest

from exness_arena import common, recovery, runner, research
from exness_arena.guard import DisconnectedError, GuardError
from exness_arena.tests.test_guard import setup, request
from exness_arena.tests.test_research import lab
from exness_arena.tests.test_adapters import bars


class Clock:
    def __init__(self, after_sleep=None):
        self.now=0.
        self.after_sleep=after_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now+=seconds
        if self.after_sleep:
            self.after_sleep()


@pytest.fixture
def offline_clock(monkeypatch):
    clock=Clock()
    monkeypatch.setattr(recovery,'time',clock)
    monkeypatch.setattr(recovery,'health',lambda *a:None)
    return clock


def test_backoff_is_capped_and_resumes_only_after_validation(tmp_path,offline_clock):
    attempts=[]
    def initialize():
        attempts.append(offline_clock.now)
        if len(attempts)==2:
            raise DisconnectedError('MT5 disconnected')
        return len(attempts)==6
    assert recovery.wait_for_connection(S(initialize=initialize),'Monn',tmp_path/'STOP')
    assert attempts==[0,5,15,35,65,95]


def test_pause_interrupts_backoff_without_another_connection_attempt(tmp_path,offline_clock):
    stop=tmp_path/'STOP';calls=[]
    offline_clock.after_sleep=lambda:stop.write_text('pause')
    def initialize():
        calls.append(1)
        return False
    assert not recovery.wait_for_connection(S(initialize=initialize),'Monn',stop)
    assert calls==[1] and offline_clock.now==.5


def test_collector_can_recover_independently_of_strategy_stop_marker(tmp_path,offline_clock):
    (tmp_path/'STOP').write_text('strategy pause')
    calls=[]
    def initialize():
        calls.append(1)
        return len(calls)==2
    assert recovery.wait_for_connection(S(initialize=initialize),'tracker',None)
    assert len(calls)==2 and (tmp_path/'STOP').exists()


@pytest.mark.parametrize('error',[GuardError('wrong pinned account'),GuardError('automation disabled'),RuntimeError('unknown failure')])
def test_security_and_unknown_failures_are_not_retried(tmp_path,offline_clock,error):
    calls=[]
    def initialize():
        calls.append(1)
        raise error
    with pytest.raises(type(error),match=str(error)):
        recovery.wait_for_connection(S(initialize=initialize),'Monn',tmp_path/'STOP')
    assert calls==[1] and offline_clock.now==0


def test_connection_initialization_keeps_terminal_pin_and_rechecks_account(setup):
    g,n=setup;g.config['terminal_path']='configured-terminal.exe'
    seen=[]
    n.initialize=lambda **kw:seen.append(kw) or True
    assert g.initialize()
    assert seen==[{'path':'configured-terminal.exe','timeout':5000}]
    n.account.login=999
    with pytest.raises(GuardError,match='pinned'):
        g.initialize()
    assert len(seen)==2


def test_wrong_account_is_fatal_even_when_terminal_disconnected(setup):
    g,n=setup
    n.account.login=999
    n.terminal_info=lambda:S(connected=False)
    with pytest.raises(GuardError,match='pinned') as caught:
        g.validate_account()
    assert not isinstance(caught.value,DisconnectedError)


def test_failed_read_recovers_only_with_confirmed_disconnect(setup):
    g,n=setup;module=S(_gateway=g)
    def missing():
        raise RuntimeError('missing inventory')
    with pytest.raises(RuntimeError,match='inventory'):
        recovery.connected_read(module,missing)
    n.terminal_info=lambda:S(connected=False)
    with pytest.raises(DisconnectedError):
        recovery.connected_read(module,missing)


def loop_lab(lab,monkeypatch):
    g,n,p=lab
    n.connected=True
    n.terminal_info=lambda:S(connected=n.connected,trade_allowed=True,tradeapi_disabled=False)
    initialized=[];shutdown=[]
    def initialize():
        initialized.append(1)
        n.connected=True
        g.validate_account()
        return True
    module=S(_gateway=g,initialize=initialize,shutdown=lambda:shutdown.append(1),
             for_strategy=g.for_strategy,symbol_select=lambda *a:True)
    monkeypatch.setattr(runner,'STATE',p)
    monkeypatch.setattr(runner,'refresh',lambda gateway:['EURUSDc'])
    monkeypatch.setattr(runner,'enabled_for_symbol',lambda *a:['ma_cross'])
    monkeypatch.setattr(runner,'strategy_rates',lambda *a:bars())
    monkeypatch.setattr(runner,'minimum_bars',lambda *a:2)
    monkeypatch.setattr(runner,'coverage',lambda *a,**kw:None)
    return g,n,p,module,initialized,shutdown


def test_disconnect_after_accepted_order_never_replays_claimed_signal(lab,monkeypatch):
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    with common.locked() as db:db.execute("DELETE FROM flags WHERE name='halt'")
    calls=[]
    def evaluate(cycle,scoped,bot,strategy,symbol,rates):
        calls.append(1)
        scoped.order_send(request(symbol=symbol,volume=.01))
        n.connected=False
        raise AttributeError('quote disappeared after submission')
    monkeypatch.setattr(research,'evaluate',evaluate)
    runner.run_loop('Monn',module,'monn',['EURUSDc'],{'ma_cross':lambda *a:None},once=True)
    assert len(calls)==len(n.sent)==len(initialized)==len(shutdown)==1
    with common.locked() as db:
        assert db.execute('SELECT COUNT(*) FROM strategy_decisions').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM flags WHERE name LIKE 'strategy_halt:%'").fetchone()[0]==0


def test_recovery_preserves_drawdown_halt_and_shadow_tracking(lab,monkeypatch):
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    original=(p/'settings.json').read_bytes()
    def cycle(scoped,symbol,rates):
        scoped.order_send(request(symbol=symbol,volume=.01))
        n.connected=False
        return 'qualifying signal interrupted by disconnect'
    runner.run_loop('Monn',module,'monn',['EURUSDc'],{'ma_cross':cycle},once=True)
    assert not n.sent and initialized==[1] and shutdown==[1]
    assert (p/'settings.json').read_bytes()==original
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()[0]=='account drawdown'
        assert db.execute('SELECT COUNT(*) FROM strategy_decisions').fetchone()[0]==1
    with research.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM research_decisions').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM shadows').fetchone()[0]==5


def test_known_disconnect_does_not_quarantine_if_connection_already_returned(lab,monkeypatch):
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    def interrupted(*args):
        # The bridge can reconnect between the failed read and error handling.
        assert n.connected
        raise DisconnectedError('temporary data outage')
    monkeypatch.setattr(research,'evaluate',interrupted)
    runner.run_loop('Monn',module,'monn',['EURUSDc'],{'ma_cross':lambda *a:None},once=True)
    assert initialized==[1] and not n.sent
    with common.locked() as db:
        assert db.execute("SELECT COUNT(*) FROM flags WHERE name LIKE 'strategy_halt:%'").fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM strategy_decisions').fetchone()[0]==1


def test_online_strategy_failure_still_quarantines(lab,monkeypatch):
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    def broken(*a):
        raise RuntimeError('strategy bug')
    runner.run_loop('Monn',module,'monn',['EURUSDc'],{'ma_cross':broken},once=True)
    assert initialized==[] and not n.sent
    with common.locked() as db:
        assert db.execute("SELECT value FROM flags WHERE name LIKE 'strategy_halt:%'").fetchone()


def test_account_mismatch_during_recovery_stays_fatal(lab,monkeypatch):
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    n.connected=False
    def wrong():
        n.connected=True
        n.account.login=999
        return g.validate_account()
    module.initialize=wrong
    with pytest.raises(GuardError,match='pinned'):
        runner.run_loop('Monn',module,'monn',['EURUSDc'],{},once=True)
    with common.locked() as db:
        assert db.execute("SELECT status FROM health WHERE bot='Monn'").fetchone()[0]=='error'
        assert db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()[0]=='account drawdown'
    assert shutdown==[1] and not n.sent


def test_normal_startup_waits_for_connection_and_retries_read_only_preparation(lab,monkeypatch,offline_clock):
    import exness_arena
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    attempts=[];prepared=[];loops=[]
    def initialize():
        attempts.append(1)
        return len(attempts)>1
    def prepare(*args):
        prepared.append(1)
        if len(prepared)==1:
            raise DisconnectedError('lost while fetching history')
        return 'monn',['EURUSDc'],{},1,{'ma_cross':1},[]
    module.initialize=initialize
    monkeypatch.setitem(sys.modules,'exness_arena.mt5',module)
    monkeypatch.setattr(exness_arena,'mt5',module,raising=False)
    monkeypatch.setattr(sys,'argv',['exness_trial.py','--once'])
    monkeypatch.setattr(runner,'prepare_runner',prepare)
    monkeypatch.setattr(runner,'run_loop',lambda *a,**kw:loops.append(kw))
    runner.main('Monn')
    assert len(attempts)==3 and len(prepared)==2 and loops==[{'once':True}]
    assert not n.sent and offline_clock.now==5


def test_preflight_fails_without_waiting_offline(lab,monkeypatch,offline_clock):
    import exness_arena
    g,n,p,module,initialized,shutdown=loop_lab(lab,monkeypatch)
    module.initialize=lambda:False
    monkeypatch.setitem(sys.modules,'exness_arena.mt5',module)
    monkeypatch.setattr(exness_arena,'mt5',module,raising=False)
    monkeypatch.setattr(sys,'argv',['exness_trial.py','--preflight'])
    with pytest.raises(SystemExit,match='Cannot initialize'):
        runner.main('Monn')
    assert offline_clock.now==0 and not n.sent
