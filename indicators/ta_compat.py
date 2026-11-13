"""Scalar last-value helpers for the legacy strategy streaming API.

Recent TA-Lib stream functions return stream objects. Legacy Monn expects the
last scalar (or MACD tuple). Use the same full-series indicator and take its tail.
"""
import talib


def last(value):
    if isinstance(value, tuple):
        return tuple(last(v) for v in value)
    return value.iloc[-1] if hasattr(value, 'iloc') else value[-1]


def __getattr__(name):
    function = getattr(talib, name)
    return lambda *args, **kwargs: last(function(*args, **kwargs))
