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

from .common import REGISTRY, STATE, claim_strategy_bar, health, settings
from .identity import enabled_for_symbol
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
    strategy.set_volume(settings()["max_volume"])
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
        "volume": settings()["max_volume"], "sl": sl, "tp": round(tp, gateway.symbol_info(symbol).digits)})
    return f"Crossover {signal.value}; mandatory 2 ATR stop and 2R target"


def main(bot):
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="read-only account, data and import checks")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.environ["EXNESS_BOT_ID"] = bot
    from . import mt5 as gateway

    if not REGISTRY[bot]["runner"]:
        health(bot, "blocked", REGISTRY[bot]["blocked"])
        raise SystemExit(REGISTRY[bot]["blocked"])
    if not gateway.initialize():
        raise SystemExit("Cannot initialize MT5")
    kind = REGISTRY[bot]['runner']
    if kind == "monn":
        from strategies.ma_cross_strategy import MACross  # noqa: F401
        cycle = monn_cycle
    elif kind == 'crossover':
        from mt5_trading.domain.strategies.cross_over_strategy import CrossOverStrategy  # noqa: F401
        cycle = crossover_cycle
    elif kind in PROFILES:
        prepare_profile(kind)
        cycle = PROFILES[kind]
    else:
        raise RuntimeError('Unknown trial strategy profile: ' + str(kind))
    watchlist = refresh(gateway)
    c = settings()
    ready = 0
    for symbol in watchlist:
        if not profile_supported(kind, symbol):
            coverage(bot, symbol, 'unsupported_profile', 'No original Sunrise parameter profile; no substituted strategy')
            continue
        if not gateway.symbol_select(symbol, True):
            coverage(bot, symbol, "unavailable", "Broker symbol selection failed")
            continue
        rates = history(gateway, kind, symbol)
        minimum = 550 if kind in PROFILES else 150
        if rates is None or len(rates) < minimum:
            coverage(bot, symbol, "waiting_history", f"Need at least {minimum} closed M15 bars")
            continue
        for strategy in enabled_for_symbol(bot, symbol, c):
            if args.preflight and kind in PROFILES:
                cycle(NoOrderGateway(gateway.for_strategy(strategy)), symbol, rates)
            coverage(bot, symbol, "ready", "Closed M15 history available", int(rates[-1]["time"]), strategy=strategy)
        ready += 1
    if not ready:
        raise RuntimeError("No pairs have sufficient history; no orders sent")
    if args.preflight:
        print(f"{bot}: PASS account pin, strategy imports and M15 history on {ready}/{len(watchlist)} pairs; no orders sent")
        return
    health(bot, "running", "Waiting for a new closed bar")
    print(f"{bot}: started, magic={REGISTRY[bot]['magic']}, risk cap={c['risk_budget_usc']:.4f} USC", flush=True)
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
                        watchlist = refresh(gateway)
                        last_refresh = time.monotonic()
                    messages = []
                    evaluated = 0
                    for symbol in watchlist:
                        if not profile_supported(kind, symbol):
                            coverage(bot, symbol, 'unsupported_profile', 'No original Sunrise parameter profile; no substituted strategy')
                            continue
                        if not gateway.symbol_select(symbol, True):
                            coverage(bot, symbol, "unavailable", "Broker symbol selection failed")
                            continue
                        rates = history(gateway, kind, symbol)
                        minimum = 550 if kind in PROFILES else 150
                        if rates is None or len(rates) < minimum:
                            coverage(bot, symbol, "waiting_history", f"Need at least {minimum} closed M15 bars")
                            continue
                        bar = int(rates[-1]["time"])
                        for strategy in enabled_for_symbol(bot, symbol, settings()):
                            scoped = gateway.for_strategy(strategy)
                            if claim_strategy_bar(bot, strategy, symbol, bar):
                                message = cycle(scoped, symbol, rates)
                                coverage(bot, symbol, "evaluated", message, bar, strategy=strategy)
                                evaluated += 1
                                messages.append(symbol + " [" + strategy + "]: " + message)
                                print(messages[-1], flush=True)
                            else:
                                coverage(bot, symbol, "monitoring", "Waiting for next closed M15 bar", bar, strategy=strategy)
                    supported = sum(profile_supported(kind, s) for s in watchlist)
                    health(bot, "running", f"Monitoring {supported}/{len(watchlist)} supported pair profiles; evaluated {evaluated} new closed M15 bars this pass")
                except Exception:
                    failed = True
                    detail = traceback.format_exc()
                    health(bot, "error", detail)
                    print(detail, flush=True)
                    # Never resume after an unknown execution/strategy exception without inspection.
                    raise
                if args.once:
                    break
                time.sleep(10)
        finally:
            if not failed:
                health(bot, "stopped", "Runner exited; existing server-side stops remain active")
            gateway.shutdown()


if __name__ == "__main__":
    main(os.environ["EXNESS_BOT_ID"])
