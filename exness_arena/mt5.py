"""Explicit guarded replacement for the MetaTrader5 Python API in trial runners."""
import os
import MetaTrader5 as _native
from .guard import Gateway

_gateway = None


def __getattr__(name):
    global _gateway
    if _gateway is None:
        _gateway = Gateway(_native, os.environ.get("EXNESS_BOT_ID", ""))
    return getattr(_gateway, name)
