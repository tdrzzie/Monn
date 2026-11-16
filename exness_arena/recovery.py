"""Recover the terminal connection, never a broker submission or claimed signal."""
import time

from .common import health
from .guard import DisconnectedError


def connected_read(gateway, read, *args):
    """Classify a failed read as transient only if MT5 is now disconnected.

    Never wrap submissions: an interrupted order must not be replayed.
    """
    try:
        return read(*args)
    except Exception:
        gateway._gateway.validate_account()
        raise


def wait_for_connection(gateway, bot, stop_path):
    """Stay alive offline; only known connection failures get retried.

    Gateway.initialize uses only the configured terminal path, without login
    credentials or account switching. It revalidates account/permissions on success.
    No execution journal, bar claims, protection or halt flags are changed here.
    """
    delay = 5
    attempt = 0
    stopped = lambda: stop_path is not None and stop_path.exists()
    while not stopped():
        attempt += 1
        health(bot, 'reconnecting', f'MT5 connection check {attempt}; entries suspended')
        try:
            if gateway.initialize():
                if stopped():
                    return False
                health(bot, 'recovering', 'MT5 revalidated; awaiting a fresh scan with existing gates and bar claims')
                print(f'{bot}: MT5 connection restored; existing trading gates and bar claims preserved', flush=True)
                return True
        except DisconnectedError:
            pass
        except Exception as exc:
            health(bot, 'error', f'Connection recovery stopped: {type(exc).__name__}: {exc}')
            raise
        # Wrong account, disabled automation and unknown exceptions propagate.
        health(bot, 'reconnecting', f'MT5 unavailable; retry in {delay}s; entries suspended')
        print(f'{bot}: MT5 unavailable; reconnecting in {delay}s', flush=True)
        deadline = time.monotonic() + delay
        while not stopped() and time.monotonic() < deadline:
            time.sleep(min(.5, max(0., deadline - time.monotonic())))
        delay = min(30, delay * 2)
    return False
