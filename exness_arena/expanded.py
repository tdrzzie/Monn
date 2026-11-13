"""Additional original library strategies, with explicit reviewed trial adapters."""
import json
import math
from pathlib import Path

import pandas as pd
import talib

from .volume import entry_volume_cap
from .common import REGISTRY, settings
from .adapters import submit, WORKSPACE
from .research import observe


def monn_definition(strategy):
    spec = REGISTRY['Monn']['strategies'][strategy]
    path = WORKSPACE/'Monn'/'configs'/spec['config']
    definitions = json.loads(path.read_text())
    matches = [d for item in definitions for d in item.get('strategies', []) if d['name'] == strategy]
    if len(matches) != 1 or set(matches[0]['tfs'].values()) != {'15m'}:
        raise RuntimeError('Expected one original single-timeframe M15 strategy definition')
    return matches[0]


def monn_library_cycle(gateway, symbol, rates):
    from strategy_utils import load_strategy
    from trader import Trader
    from order import OrderType, OrderSide
    import indicators as mta

    definition = monn_definition(gateway.strategy)
    observe(gateway, original_definition=definition)
    frame = pd.DataFrame(rates)
    frame['time'] = pd.to_datetime(frame.time, unit='s', utc=True)
    frame = frame.rename(columns={'time': 'Open time', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'tick_volume': 'Volume'})
    seed = frame.iloc[:-1].copy()
    params = definition['params']
    if 'min_zz_pct' in params:
        points = mta.zigzag_conv(seed, params['zz_conv_size'], .01*params['min_zz_pct']) if params.get('zz_type') == 'ZZ_CONV' else mta.zigzag(seed, .01*params['min_zz_pct'])
        if len(points) < 4:
            return 'Waiting for at least four confirmed ZigZag structure points'
    results = []

    class Bridge:
        fix_order = Trader.fix_order
        def create_trade(self, order, volume):
            if order.type != OrderType.MARKET:
                results.append('Pending-order signal skipped; never converted to market')
                return
            side = 0 if order.side == OrderSide.BUY else 1
            if order.sl is None:
                results.append('Signal without server-side stop skipped')
                return
            request = {'action': gateway.TRADE_ACTION_DEAL, 'symbol': symbol, 'type': side,
                'volume': entry_volume_cap(symbol, settings()), 'sl': float(order.sl)}
            if order.tp:
                request['tp'] = float(order.tp)
            result = gateway.order_send(request)
            results.append(f'Original {gateway.strategy} signal: {getattr(result,"retcode",None)} {getattr(result,"comment","")}')
        def close_trade(self, order):
            pass  # Historical/native signal exits never liquidate this hedged trial book.
        def adjust_sl(self, order, sl):
            pass

    rule = load_strategy(definition)
    if not rule.is_params_valid():
        raise RuntimeError('Original strategy parameters failed validation')
    rule.set_volume(entry_volume_cap(symbol, settings()))
    rule.set_max_sl_pct(definition.get('max_sl_pct'))
    frames = {'15m': seed}
    rule.attach(frames)
    rule.attach_trader(Bridge())
    frames['15m'] = frame
    rule.update('15m')
    return '; '.join(results) or f'Original {gateway.strategy}: no new closed-bar market signal'


def python_library_cycle(gateway, symbol, rates):
    from mt5_trading.domain.strategies.smc_strategy import SMCStrategy
    from mt5_trading.domain.strategies.trend_breakout_strategy import TrendBreakoutStrategy
    from mt5_trading.domain.strategies.combined_strategy import CombinedStrategy
    from mt5_trading.domain.signal import Signal
    frame = pd.DataFrame(rates)
    class ClosedBars:
        def get_data(self):return frame.copy()
        def get_symbol(self):return symbol
    classes = {'smc': SMCStrategy, 'trend_breakout': TrendBreakoutStrategy, 'combined': CombinedStrategy}
    signal = classes[gateway.strategy](ClosedBars()).signal()[1]
    observe(gateway, signal=signal.value, rule_name=gateway.strategy)
    if signal not in (Signal.BUY, Signal.SELL):
        return f'Original {gateway.strategy}: no signal'
    atr = float(talib.ATR(frame.high, frame.low, frame.close, timeperiod=14).iloc[-1])
    if not math.isfinite(atr) or atr <= 0:
        return 'ATR unavailable'
    side = 0 if signal == Signal.BUY else 1
    tick = gateway.symbol_info_tick(symbol)
    entry = tick.ask if side == 0 else tick.bid
    return submit(gateway, symbol, side, entry + (-2 if side == 0 else 2)*atr,
                  entry + (4 if side == 0 else -4)*atr)
