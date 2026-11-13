"""Explicit trial profiles using original repository strategies, never placeholder signals.

All execution is routed through Gateway. Replay brokers only reconstruct strategy
signals; their balances, orders and position sizing never reach the real account.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import talib

from .common import STATE, settings, locked

WORKSPACE = Path(__file__).resolve().parents[2]
_modules = {}
_artifacts = {}
SUNRISE_SYMBOLS = {'EURUSDc', 'GBPUSDc', 'XAUUSDc', 'AUDUSDc', 'XAGUSDc', 'USDCHFc', 'EURJPYc', 'USDJPYc'}


def source_module(name, path):
    if name not in _modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        # Some upstream scripts print emoji at import time even outside __main__.
        # Keep library imports silent and avoid Windows console encoding failures.
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
        _modules[name] = module
    return _modules[name]


def submit(gateway, symbol, side, sl, tp):
    if not all(math.isfinite(float(v)) and float(v) > 0 for v in (sl, tp)):
        return 'No valid protective price levels'
    info = gateway.symbol_info(symbol)
    result = gateway.order_send({'action': gateway.TRADE_ACTION_DEAL, 'symbol': symbol, 'type': side,
        'volume': settings()['max_volume'], 'sl': float(sl), 'tp': round(float(tp), info.digits)})
    return f"{'buy' if side == 0 else 'sell'} signal; execution retcode={getattr(result, 'retcode', None)}: {getattr(result, 'comment', '')}"


def forex_cycle(gateway, symbol, rates):
    """Original 18-period / 5x ATR SuperTrend lines and entry/SL/TP rules."""
    strategy = source_module('trial_forex_indicator', WORKSPACE/'Forex-Trading-Bot'/'DataProcessing.py')
    frame = pd.DataFrame(rates)
    forming = gateway.copy_rates_from_pos(symbol, gateway.TIMEFRAME_M15, 0, 1)
    if forming is None or not len(forming):
        return 'Waiting for current bar open'
    if int(forming[0]['time']) <= int(rates[-1]['time']):
        return 'Current bar has not advanced past the closed signal bar'
    # Original Indicator deliberately excludes its last row. Only the forming OPEN
    # is appended, so its high/low/close cannot leak into the signal computation.
    opening = float(forming[0]['open'])
    last = dict(frame.iloc[-1]); last.update(time=int(forming[0]['time']), open=opening, high=opening, low=opening, close=opening)
    maximum, minimum, average = strategy.Indicator(pd.concat([frame, pd.DataFrame([last])], ignore_index=True))
    if not all(math.isfinite(float(v)) for v in (maximum, minimum, average)):
        return 'No finite SuperTrend levels'
    previous = frame.iloc[-1]
    if previous.low <= minimum and opening < average:
        side = 0
    elif previous.high >= maximum and opening > average:
        side = 1
    else:
        return 'No SuperTrend entry signal'
    # Original get_sl formula: reward / 1.2, using the strategy's average line as TP.
    sl = opening - (float(average) - opening) / 1.2
    return submit(gateway, symbol, side, sl, average)


def aurum_data(gateway, symbol, rates):
    from aurum.core.types import MarketData
    from aurum.data.schema import make_bars
    frame = pd.DataFrame(rates)
    frame.index = pd.to_datetime(frame.pop('time'), unit='s', utc=True)
    frame['volume'] = frame.tick_volume
    frame['spread'] = frame.spread * gateway.symbol_info(symbol).point
    # MT5 bars are bid prices; canonical Aurum bars are mid prices.
    for key in ('open', 'high', 'low', 'close'):
        frame[key] = frame[key] + frame['spread'] / 2
    return MarketData(make_bars(frame, 'M15'))


def aurum_artifact(gateway, symbol, rates):
    """Verified native Aurum artifact for the deterministic, non-trainable EMA rule."""
    from aurum.strategies.base import get_strategy
    from aurum.live.runner import save_artifact, load_artifact
    rule_name = getattr(gateway, 'strategy', 'ema_cross')
    key = symbol if rule_name == 'ema_cross' else (rule_name, symbol)
    if key not in _artifacts:
        path = STATE/'artifacts'/'aurum_ema'/symbol if rule_name == 'ema_cross' else STATE/'artifacts'/'aurum_rules'/rule_name/symbol
        if not (path/'manifest.json').exists():
            md = aurum_data(gateway, symbol, rates)
            rule = get_strategy(rule_name)
            if rule.trainable:
                raise RuntimeError('Training-dependent rules require a separately validated artifact')
            rule.fit(md)
            save_artifact(path, strategies={rule_name: rule}, symbol=symbol, timeframe='M15',
                training={'cutoff': md.bars.available_at.iloc[-1].isoformat(), 'profile': 'cent_trial_deterministic_'+rule_name},
                notes='Original non-trainable rule; shared guard uses broker-native USC valuation. Not the full Aurum portfolio or LLM desk.')
        artifact = load_artifact(path)
        if artifact.symbol != symbol or artifact.timeframe != 'M15' or rule_name not in artifact.strategies:
            raise RuntimeError('Aurum artifact does not match the selected trial profile')
        if len(rates) <= artifact.max_lookback:
            raise RuntimeError('Insufficient Aurum strategy warmup')
        _artifacts[key] = artifact
    return _artifacts[key]


def aurum_cycle(gateway, symbol, rates):
    artifact = aurum_artifact(gateway, symbol, rates)
    md = aurum_data(gateway, symbol, rates)
    rule_name = getattr(gateway, 'strategy', 'ema_cross')
    forecast = float(artifact.strategies[rule_name].generate(md).iloc[-1])
    if not math.isfinite(forecast) or abs(forecast) < 0.25:
        return f'Aurum {rule_name} forecast {forecast:.4f}: below 0.25 trial entry threshold'
    frame = pd.DataFrame(rates)
    atr = float(talib.ATR(frame.high, frame.low, frame.close, timeperiod=14).iloc[-1])
    if not math.isfinite(atr) or atr <= 0:
        return 'Aurum ATR unavailable'
    side = 0 if forecast > 0 else 1
    tick = gateway.symbol_info_tick(symbol)
    entry = tick.ask if side == 0 else tick.bid
    sl = entry - 2 * atr if side == 0 else entry + 2 * atr
    tp = entry + 4 * atr if side == 0 else entry - 4 * atr
    return f'Aurum {rule_name} forecast {forecast:.4f}; ' + submit(gateway, symbol, side, sl, tp)


def sunrise_signals(symbol, rates):
    """Replay original per-symbol Sunrise state machine and capture LAST-bar entries.

    No hypothetical historical fill is ever sent to MT5. Each replay starts from
    the same initial 600-bar anchor, persisted per pair by the live runner profile.
    """
    import backtrader as bt
    if symbol not in SUNRISE_SYMBOLS:
        return [], 'No original Sunrise parameter profile for this pair'
    module = source_module('trial_sunrise_' + symbol, WORKSPACE/'mt5_live_trading_bot'/'strategies'/('sunrise_ogle_' + symbol[:-1].lower() + '.py'))
    module.EXPORT_TRADE_REPORTS = False
    module.TRADE_REPORT_ENABLED = False
    frame = pd.DataFrame(rates)
    frame.index = pd.to_datetime(frame.pop('time'), unit='s')
    frame['volume'] = frame.tick_volume
    captured = []

    strategy_class = getattr(module, 'SunriseOgle', None) or getattr(module, 'SunriseOgleUSDJPY', None)
    if strategy_class is None:
        raise RuntimeError('Original Sunrise strategy class unavailable: ' + symbol)

    class Capture(strategy_class):
        def buy(self, *args, **kwargs):
            if len(self) == len(frame) and not self.position and kwargs.get('exectype', bt.Order.Market) == bt.Order.Market:
                captured.append({'side': 0, 'sl': self.stop_level, 'tp': self.take_level})
            return super().buy(*args, **kwargs)

        def sell(self, *args, **kwargs):
            if len(self) == len(frame) and not self.position and kwargs.get('exectype', bt.Order.Market) == bt.Order.Market:
                captured.append({'side': 1, 'sl': self.stop_level, 'tp': self.take_level})
            return super().sell(*args, **kwargs)

    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(bt.feeds.PandasData(dataname=frame), name=symbol)
    # Paper reconstruction uses unit sizing; real sizing always belongs to Gateway.
    cerebro.addstrategy(Capture, plot_result=False, use_forex_position_calc=False,
        enable_risk_sizing=False, contract_size=1, size=1, print_signals=False)
    cerebro.broker.setcash(100000)
    with contextlib.redirect_stdout(io.StringIO()):
        result = cerebro.run()
    return captured, f'Original Sunrise state={result[0].entry_state}; latest-bar entries={len(captured)}'


def sunrise_cycle(gateway, symbol, rates):
    signals, detail = sunrise_signals(symbol, rates)
    for signal in signals:
        detail += '; ' + submit(gateway, symbol, signal['side'], signal['sl'], signal['tp'])
    return detail


def prepare_profile(kind):
    if kind == 'forex':
        source_module('trial_forex_indicator', WORKSPACE/'Forex-Trading-Bot'/'DataProcessing.py')
    elif kind == 'aurum':
        from aurum.strategies.trend import EMACrossover
        from aurum.live.runner import save_artifact, load_artifact
    elif kind == 'sunrise':
        import backtrader
        for symbol in SUNRISE_SYMBOLS:
            source_module('trial_sunrise_' + symbol, WORKSPACE/'mt5_live_trading_bot'/'strategies'/('sunrise_ogle_' + symbol[:-1].lower() + '.py'))


PROFILES = {'forex': forex_cycle, 'aurum': aurum_cycle, 'sunrise': sunrise_cycle}


def profile_supported(kind, symbol):
    return kind != 'sunrise' or symbol in SUNRISE_SYMBOLS


def history(gateway, kind, symbol, count=None):
    count = count or (600 if kind in PROFILES else 300)
    rates = gateway.copy_rates_from_pos(symbol, gateway.TIMEFRAME_M15, 1, count)
    if kind != 'sunrise' or rates is None or not len(rates):
        return rates
    with locked() as db:
        key = 'sunrise_anchor:' + symbol
        db.execute('INSERT OR IGNORE INTO flags VALUES(?,?)', (key, str(int(rates[0]['time']))))
        anchor = int(db.execute('SELECT value FROM flags WHERE name=?', (key,)).fetchone()[0])
    # Fixed replay origin makes entry-state reconstruction deterministic on restart.
    full = gateway.copy_rates_range(symbol, gateway.TIMEFRAME_M15,
        datetime.fromtimestamp(anchor, timezone.utc), datetime.now(timezone.utc))
    if full is None:
        return None
    return full[full['time'] <= rates[-1]['time']]


class NoOrderGateway:
    def __init__(self, gateway):
        self.gateway = gateway
        self.proposals = []

    def order_send(self, request):
        from types import SimpleNamespace
        self.proposals.append(dict(request))
        return SimpleNamespace(retcode=10006, comment='read-only profile validation; no orders sent', order=0)

    def __getattr__(self, name):
        return getattr(self.gateway, name)
