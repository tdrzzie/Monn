"""Closed-bar trial adapters. Existing strategy code generates the signals.

Monn: upstream MACross (10/20 SMA with 100-SMA and candle filters), M15.
MT5-Python-Trading: upstream CrossOverStrategy (20/50 SMA), M15.
Only execution, restart reconciliation and mandatory stops are adapted here.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

from .guard import DisconnectedError
from .recovery import connected_read, wait_for_connection
from .volume import entry_volume_cap
from .common import REGISTRY, STATE, claim_strategy_bar, health, settings, strategy_halt, strategy_halt_reason
from .identity import enabled_for_symbol, strategies
from .universe import refresh, coverage
from .adapters import PROFILES, prepare_profile, profile_supported, history, NoOrderGateway


def close_owned(gateway, position):
    return gateway.order_send({"action": gateway.TRADE_ACTION_DEAL, "position": position.ticket,
                               "symbol": position.symbol, "volume": position.volume})


def monn_cycle(gateway, symbol, rates):
    import pandas as pd
    from strategies.ma_cross_strategy import MACross
    from order import OrderSide
    from trader import Trader
    from .research import observe
    observe(gateway, parameters={'fast_ma':10,'slow_ma':20,'type':'SMA','sl_fix_mode':'ADJ_SL','max_sl_pct':.25})

    frame = pd.DataFrame(rates)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame.rename(columns={"time": "Open time", "open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"})
    positions = gateway.positions_get(symbol=symbol)

    class Bridge:
        fix_order = Trader.fix_order

        def create_trade(self, order, volume):
            side = 0 if order.side == OrderSide.BUY else 1
            result = gateway.order_send({"action": gateway.TRADE_ACTION_DEAL, "symbol": symbol,
                "type": side, "volume": volume,
                "sl": order.sl or 0, "tp": order.tp or 0})
            order["trade_id"] = getattr(result, "order", 0)

        def close_trade(self, order):
            for p in positions:
                if p.ticket == order["trade_id"]:
                    close_owned(gateway, p)

    strategy = MACross("ma_cross", {"fast_ma": 10, "slow_ma": 20, "type": "SMA", "sl_fix_mode": "ADJ_SL"}, {"tf": "15m"})
    strategy.set_volume(entry_volume_cap(symbol, settings()))
    strategy.set_max_sl_pct(0.25)
    strategy.attach({"15m": frame})
    strategy.attach_trader(Bridge())
    # Evaluate the entry signal independently of existing positions. Opposite signals
    # can hedge rather than liquidate. The shared guard decides whether a profitable
    # same-direction add-on is eligible; existing SL/TP remain on the server.
    strategy.check_signal()
    return "MACross evaluated closed M15 bar; owned positions=" + str(len(positions))


def crossover_cycle(gateway, symbol, rates):
    import pandas as pd
    import talib
    from mt5_trading.domain.strategies.cross_over_strategy import CrossOverStrategy
    from mt5_trading.domain.signal import Signal

    frame = pd.DataFrame(rates)

    class ClosedBars:
        def get_data(self):
            return frame.copy()

        def get_symbol(self):
            return symbol

    signal = CrossOverStrategy(ClosedBars()).signal()[1]
    from .research import observe
    observe(gateway, signal=signal.value, parameters={'fast_ma':20,'slow_ma':50})
    if signal not in (Signal.BUY, Signal.SELL):
        return "No crossover signal"
    side = 0 if signal == Signal.BUY else 1
    # Do not close opposite positions merely because the signal changes. A first
    # position in either direction is independent; profitable add-ons are guard-gated.
    atr = float(talib.ATR(frame.high, frame.low, frame.close, timeperiod=14).iloc[-1])
    if not atr > 0:
        return "ATR unavailable"
    tick = gateway.symbol_info_tick(symbol)
    entry = tick.ask if side == 0 else tick.bid
    distance = 2 * atr
    sl = entry - distance if side == 0 else entry + distance
    tp = entry + 2 * distance if side == 0 else entry - 2 * distance
    gateway.order_send({"action": gateway.TRADE_ACTION_DEAL, "symbol": symbol, "type": side,
        "volume": entry_volume_cap(symbol, settings()), "sl": sl, "tp": round(tp, gateway.symbol_info(symbol).digits)})
    return f"Crossover {signal.value}; mandatory 2 ATR stop and 2R target"


def cycle_for(bot, strategy):
    spec = strategies(bot)[strategy]
    kind = spec.get('adapter', REGISTRY[bot]['runner'])
    if spec.get('source_strategy'):
        from .promotion import recipe_cycle
        source = spec['source_strategy']
        return kind, recipe_cycle(cycle_for(bot, source)[1], source, spec['execution_variant'])
    if kind == 'monn':
        return kind, monn_cycle
    if kind == 'crossover':
        return kind, crossover_cycle
    if kind in ('monn_library', 'python_library'):
        from .expanded import monn_library_cycle, python_library_cycle
        return kind, monn_library_cycle if kind == 'monn_library' else python_library_cycle
    if kind in PROFILES:
        prepare_profile(kind)
        return kind, PROFILES[kind]
    raise RuntimeError('No reviewed adapter for strategy ' + strategy)


def strategy_rates(gateway, bot, strategy, symbol):
    spec = strategies(bot)[strategy]
    kind = spec.get('adapter', REGISTRY[bot]['runner'])
    return history(gateway, kind, symbol, count=spec.get('history_bars'))


def minimum_bars(bot, strategy):
    spec = strategies(bot)[strategy]
    kind = spec.get('adapter', REGISTRY[bot]['runner'])
    return spec.get('minimum_bars', 550 if kind in PROFILES else 150)


def prepare_runner(bot, gateway, args):
    kind = REGISTRY[bot]['runner']
    watchlist = connected_read(gateway, refresh, gateway)
    c = settings()
    if args.audit_all:
        args.preflight = True
        c = dict(c)
        c['enabled_strategies'] = dict(c.get('enabled_strategies', {}))
        c['enabled_strategies'][bot] = list(strategies(bot))
    cycles = {name: cycle_for(bot, name)[1] for name in c.get('enabled_strategies', {}).get(bot, list(strategies(bot)))}
    ready = 0
    failures = []
    audited = {}
    for symbol in watchlist:
        gateway._gateway.validate_account()
        if not profile_supported(kind, symbol):
            coverage(bot, symbol, 'unsupported_profile', 'No original Sunrise parameter profile; no substituted strategy')
            continue
        if not gateway.symbol_select(symbol, True):
            coverage(bot, symbol, "unavailable", "Broker symbol selection failed")
            continue
        for strategy in enabled_for_symbol(bot, symbol, c):
            try:
                rates = connected_read(gateway, strategy_rates, gateway, bot, strategy, symbol)
                minimum = minimum_bars(bot, strategy)
                if rates is None or len(rates) < minimum:
                    gateway._gateway.validate_account()
                    coverage(bot, symbol, 'waiting_history', f'Need {minimum} closed M15 bars', strategy=strategy)
                    if args.audit_all:
                        failures.append({'strategy': strategy, 'symbol': symbol, 'error': 'Insufficient closed-bar history'})
                    continue
                if args.preflight:
                    if args.research_once:
                        from .research import evaluate
                        evaluate(cycles[strategy], gateway.for_strategy(strategy), bot, strategy, symbol, rates, paper=True)
                    else:
                        cycles[strategy](NoOrderGateway(gateway.for_strategy(strategy)), symbol, rates)
                coverage(bot, symbol, "ready", "Closed M15 history and strategy validation passed", int(rates[-1]["time"]), strategy=strategy)
                audited[strategy] = audited.get(strategy, 0) + 1
                ready += 1
            except DisconnectedError:
                raise
            except Exception as exc:
                gateway._gateway.validate_account()
                if not args.audit_all:
                    raise
                failures.append({'strategy': strategy, 'symbol': symbol, 'error': type(exc).__name__ + ': ' + str(exc)})
    if not ready:
        raise RuntimeError("No pairs have sufficient history; no orders sent")
    return kind, watchlist, cycles, ready, audited, failures


def main(bot):
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="read-only account, data and import checks")
    parser.add_argument("--once", action="store_true")
    parser.add_argument('--audit-all', action='store_true', help='read-only validation of all registered strategies, without enabling them')
    parser.add_argument('--research-once', action='store_true', help='record one paper-only decision per strategy/instrument; never submit live orders')
    args = parser.parse_args()
    if args.research_once or args.audit_all:
        args.preflight = True
    os.environ["EXNESS_BOT_ID"] = bot
    from . import mt5 as gateway

    if not REGISTRY[bot]["runner"]:
        health(bot, "blocked", REGISTRY[bot]["blocked"])
        raise SystemExit(REGISTRY[bot]["blocked"])
    if args.preflight:
        if not gateway.initialize():
            raise SystemExit("Cannot initialize MT5")
    elif not wait_for_connection(gateway, bot, STATE / 'STOP'):
        health(bot, 'stopped', 'Stopped while awaiting MT5; protection and halts preserved')
        gateway.shutdown()
        return
    while True:
        try:
            kind, watchlist, cycles, ready, audited, failures = prepare_runner(bot, gateway, args)
            break
        except DisconnectedError:
            if args.preflight:
                raise
            if not wait_for_connection(gateway, bot, STATE / 'STOP'):
                health(bot, 'stopped', 'Stopped while awaiting MT5; protection and halts preserved')
                gateway.shutdown()
                return
    if args.preflight:
        if args.audit_all:
            import json
            report = {'repo': bot, 'passed_pairs_by_strategy': audited, 'failures': failures, 'orders_sent': 0}
            (STATE / ('strategy-audit-' + bot + '.json')).write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            if failures:
                raise SystemExit(1)
        print(f"{bot}: PASS {len(audited)} strategy profiles / {ready} pair checks; no orders sent")
        return
    run_loop(bot, gateway, kind, watchlist, cycles, once=args.once)


def run_loop(bot, gateway, kind, watchlist, cycles, once=False):
    health(bot, "running", "Waiting for a new closed bar")
    cfg = settings()
    cap = 'disabled' if cfg.get('live_per_position_cap_disabled') is True else f"{cfg['risk_budget_usc']:.4f} USC"
    print(f"{bot}: started, magic={REGISTRY[bot]['magic']}, fixed live risk cap={cap}", flush=True)
    # Single-process lock prevents accidental duplicate launch. SQLite guard also serializes orders.
    lock_path = STATE / (bot + ".lock")
    with lock_path.open("a+b") as handle:
        import msvcrt
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        last_refresh = time.monotonic()
        failed = False
        try:
            while not (STATE / "STOP").exists():
                try:
                    gateway._gateway.validate_account()
                    if time.monotonic() - last_refresh >= 60:
                        watchlist = connected_read(gateway, refresh, gateway)
                        last_refresh = time.monotonic()
                    messages = []
                    evaluated = 0
                    for symbol in watchlist:
                        gateway._gateway.validate_account()
                        if not profile_supported(kind, symbol):
                            coverage(bot, symbol, 'unsupported_profile', 'No original Sunrise parameter profile; no substituted strategy')
                            continue
                        if not gateway.symbol_select(symbol, True):
                            coverage(bot, symbol, "unavailable", "Broker symbol selection failed")
                            continue
                        for strategy in enabled_for_symbol(bot, symbol, settings()):
                            gateway._gateway.validate_account()
                            reason = strategy_halt_reason(bot, strategy)
                            if reason:
                                coverage(bot, symbol, 'quarantined', reason, strategy=strategy)
                                continue
                            rates = connected_read(gateway, strategy_rates, gateway, bot, strategy, symbol)
                            minimum = minimum_bars(bot, strategy)
                            if rates is None or len(rates) < minimum:
                                gateway._gateway.validate_account()
                                coverage(bot, symbol, 'waiting_history', f'Need {minimum} closed M15 bars', strategy=strategy)
                                continue
                            bar = int(rates[-1]['time'])
                            scoped = gateway.for_strategy(strategy)
                            if claim_strategy_bar(bot, strategy, symbol, bar):
                                try:
                                    from .research import evaluate
                                    message = evaluate(cycles[strategy], scoped, bot, strategy, symbol, rates)
                                except DisconnectedError:
                                    raise
                                except Exception:
                                    detail = traceback.format_exc()
                                    # Only a confirmed disconnection avoids strategy quarantine.
                                    # The bar is already claimed and will not be replayed.
                                    gateway._gateway.validate_account()
                                    strategy_halt(bot, strategy, detail)
                                    coverage(bot, symbol, 'quarantined', detail, bar, strategy=strategy)
                                    print(f'[{strategy}] quarantined: {detail}', flush=True)
                                    continue
                                gateway._gateway.validate_account()
                                coverage(bot, symbol, "evaluated", message, bar, strategy=strategy)
                                evaluated += 1
                                messages.append(symbol + " [" + strategy + "]: " + message)
                                print(messages[-1], flush=True)
                            else:
                                coverage(bot, symbol, "monitoring", "Waiting for next closed M15 bar", bar, strategy=strategy)
                    supported = sum(profile_supported(kind, s) for s in watchlist)
                    health(bot, "running", f"Monitoring {supported}/{len(watchlist)} supported pair profiles; evaluated {evaluated} new closed M15 bars this pass")
                except DisconnectedError:
                    # A security/unknown failure during recovery must stay fatal.
                    failed = True
                    recovered = wait_for_connection(gateway, bot, STATE / 'STOP')
                    failed = False
                    if not recovered:
                        break
                    # Refresh current inventory/history on the next pass. Never
                    # retry the interrupted cycle or erase its durable bar claim.
                    last_refresh = float('-inf')
                    continue
                except Exception:
                    failed = True
                    detail = traceback.format_exc()
                    health(bot, "error", detail)
                    print(detail, flush=True)
                    # Never resume after an unknown execution/strategy exception without inspection.
                    raise
                if once:
                    break
                time.sleep(10)
        finally:
            if not failed:
                health(bot, "stopped", "Runner exited; existing server-side stops remain active")
            gateway.shutdown()


if __name__ == "__main__":
    main(os.environ["EXNESS_BOT_ID"])
