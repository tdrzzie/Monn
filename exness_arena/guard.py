from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from .common import REGISTRY, STATE, event, locked, settings


class GuardError(RuntimeError):
    pass


def finite_positive(value):
    return isinstance(value, (float, int)) and math.isfinite(value) and value > 0


class Gateway:
    """All orders are serialized across bots and validated against native MT5 state.

    The guard sizes downward only. Monetary figures are account currency (USC).
    Stop-loss risk is an estimate; gaps/slippage can exceed these limits.
    """
    def __init__(self, native, bot, config=None):
        if bot not in REGISTRY:
            raise GuardError("Unregistered bot")
        self.native, self.bot = native, bot
        self.magic = REGISTRY[bot]["magic"]
        self.config = config

    def cfg(self):
        return self.config if self.config is not None else settings()

    def validate_account(self):
        a = self.native.account_info()
        t = self.native.terminal_info()
        c = self.cfg()
        if a is None or t is None or not t.connected:
            raise GuardError("MT5 disconnected")
        if (a.login, a.server, a.currency, a.margin_mode) != (c["login"], c["server"], "USC", 2):
            raise GuardError("Terminal account does not match the pinned Exness USC hedging account")
        if not a.trade_allowed or not a.trade_expert or not t.trade_allowed or t.tradeapi_disabled:
            raise GuardError("Automated trading is unavailable")
        return a

    def initialize(self, *args, **kwargs):
        # Always reuse the pinned existing terminal. Never switch accounts using bot credentials.
        if not self.native.initialize(path=self.cfg()["terminal_path"]):
            return False
        self.validate_account()
        return True

    def login(self, *args, **kwargs):
        self.validate_account()
        return True

    def symbol(self, name):
        c = self.cfg()
        mapped = c["symbol_map"].get(name, name)
        if mapped not in c["symbols"]:
            raise GuardError(f"Symbol {name} is outside the trial allowlist")
        return mapped

    def positions_get(self, *args, **kwargs):
        if "symbol" in kwargs:
            kwargs["symbol"] = self.symbol(kwargs["symbol"])
        ps = self.native.positions_get(*args, **kwargs)
        if ps is None:
            raise GuardError("Cannot retrieve positions; refusing to assume an empty book")
        return tuple(p for p in ps if p.magic == self.magic)

    def orders_get(self, *args, **kwargs):
        if "symbol" in kwargs:
            kwargs["symbol"] = self.symbol(kwargs["symbol"])
        orders = self.native.orders_get(*args, **kwargs)
        if orders is None:
            raise GuardError("Cannot retrieve orders")
        return tuple(o for o in orders if o.magic == self.magic)

    def symbol_info(self, name):
        return self.native.symbol_info(self.symbol(name))

    def symbol_info_tick(self, name):
        return self.native.symbol_info_tick(self.symbol(name))

    def symbol_select(self, name, enable=True):
        return self.native.symbol_select(self.symbol(name), enable)

    def copy_rates_from_pos(self, name, *args, **kwargs):
        return self.native.copy_rates_from_pos(self.symbol(name), *args, **kwargs)

    def history_deals_get(self, *args, **kwargs):
        ds = self.native.history_deals_get(*args, **kwargs)
        if ds is None:
            raise GuardError("Cannot retrieve deal history")
        return tuple(d for d in ds if d.magic == self.magic)

    def _positions(self):
        ps = self.native.positions_get()
        orders = self.native.orders_get()
        if ps is None or orders is None:
            raise GuardError("Account state unavailable")
        # This trial never places pending orders. An unexpected trial order blocks new risk.
        magics = {v["magic"] for v in REGISTRY.values()}
        if any(o.magic in magics for o in orders):
            raise GuardError("Unexpected pending trial order; reconcile before trading")
        return tuple(ps)

    def loss(self, side, symbol, volume, entry, sl):
        if not finite_positive(sl):
            raise GuardError("A server-side stop loss is required")
        pnl = self.native.order_calc_profit(side, symbol, float(volume), float(entry), float(sl))
        if pnl is None or not math.isfinite(pnl):
            raise GuardError("Broker stop-loss valuation unavailable")
        return max(0.0, -pnl)

    def _entry_limits(self, db, account, ps):
        c = self.cfg()
        if (STATE / "STOP").exists() or db.execute("SELECT 1 FROM flags WHERE name='halt'").fetchone():
            raise GuardError("Trial is halted; new entries blocked")
        if self.bot not in c["enabled_bots"]:
            raise GuardError("Bot has not passed readiness checks")
        # A missing/stale tracker stops all new entries; exits remain available.
        row = db.execute("SELECT time FROM health WHERE bot='tracker' AND status='running'").fetchone()
        if not row or time.time() - datetime.fromisoformat(row[0]).timestamp() > 90:
            raise GuardError("Tracker heartbeat is missing or stale")
        if account.equity <= c["initial_equity_usc"] * (1 - c["account_loss_fraction"]):
            db.execute("INSERT OR REPLACE INTO flags VALUES('halt','account drawdown')")
            raise GuardError("Account-wide drawdown limit reached")
        if any(p.magic == self.magic for p in ps):
            raise GuardError("One-position-per-bot limit")
        start = datetime.fromtimestamp(c["start_epoch"], timezone.utc)
        deals = self.native.history_deals_get(start, datetime.now(timezone.utc))
        if deals is None:
            raise GuardError("Deal history unavailable")
        owners = {d.position_id for d in deals if d.magic == self.magic}
        owners.update(r[0] for r in db.execute("SELECT position_id FROM owners WHERE bot=?", (self.bot,)))
        net = sum(d.profit + d.swap + d.commission + getattr(d, "fee", 0) for d in deals if d.position_id in owners)
        if net <= -c["bot_loss_budget_usc"]:
            raise GuardError("Per-bot trial loss budget exhausted")
        if sum(1 for d in deals if d.magic == self.magic and d.entry == 0) >= c["max_entries_per_bot"]:
            raise GuardError("Per-bot trial entry limit reached")
        total = 0.0
        magics = {REGISTRY[b]["magic"] for b in c["enabled_bots"]}
        for p in ps:
            if p.magic in magics:
                total += self.loss(p.type, p.symbol, p.volume, p.price_open, p.sl)
        return total, net

    def _prepare(self, db, request):
        n, c = self.native, self.cfg()
        account = self.validate_account()
        if not c.get("live", False):
            raise GuardError("Live order submission is disabled")
        ps = self._positions()
        r = dict(request)
        action = int(r.get("action", -1))
        ticket = int(r.get("position", 0) or 0)
        if action not in (n.TRADE_ACTION_DEAL, n.TRADE_ACTION_SLTP):
            raise GuardError("Only market orders and protection updates are supported in this trial")
        r["magic"] = self.magic
        r["comment"] = ("trial:" + self.bot)[:31]
        if ticket:
            p = next((p for p in ps if p.ticket == ticket), None)
            if p is None or p.magic != self.magic:
                raise GuardError("Refusing to change another bot's or manual position")
            r["symbol"] = p.symbol
            if action == n.TRADE_ACTION_SLTP:
                new_sl = float(r.get("sl", p.sl))
                if not finite_positive(new_sl) or (p.sl and ((p.type == 0 and new_sl < p.sl) or (p.type == 1 and new_sl > p.sl))):
                    raise GuardError("Removing or widening a stop loss is prohibited")
                r["sl"] = new_sl
                r.setdefault("tp", p.tp)
                return r
            r["type"] = n.ORDER_TYPE_SELL if p.type == n.ORDER_TYPE_BUY else n.ORDER_TYPE_BUY
            vol = float(r.get("volume", p.volume))
            if not finite_positive(vol) or vol > p.volume + 1e-9:
                raise GuardError("Invalid close volume")
            r["volume"] = vol
        else:
            if action != n.TRADE_ACTION_DEAL:
                raise GuardError("Protection update requires an owned position")
            if not c.get("live", False):
                raise GuardError("Live order submission is disabled")
            total, net = self._entry_limits(db, account, ps)
            r["symbol"] = self.symbol(r.get("symbol", ""))
            if r.get("type") not in (n.ORDER_TYPE_BUY, n.ORDER_TYPE_SELL):
                raise GuardError("Invalid order direction")
        s = n.symbol_info(r["symbol"])
        tick = n.symbol_info_tick(r["symbol"])
        if s is None or tick is None or not finite_positive(tick.bid) or not finite_positive(tick.ask):
            raise GuardError("Missing broker symbol or quote")
        if time.time() - tick.time > c["max_quote_age_seconds"]:
            raise GuardError("Stale broker quote")
        price = tick.ask if r["type"] == n.ORDER_TYPE_BUY else tick.bid
        r["price"] = float(price)
        r["deviation"] = 10
        r["type_time"] = n.ORDER_TIME_GTC
        r["type_filling"] = n.ORDER_FILLING_IOC if s.filling_mode & 2 else n.ORDER_FILLING_FOK
        if not ticket:
            if s.trade_mode != n.SYMBOL_TRADE_MODE_FULL:
                raise GuardError("Symbol is not fully tradeable")
            sl = float(r.get("sl", 0) or 0)
            sl = round(round(sl / s.trade_tick_size) * s.trade_tick_size, s.digits)
            r["sl"] = sl
            distance = price - sl if r["type"] == n.ORDER_TYPE_BUY else sl - price
            if distance <= max(s.trade_stops_level * s.point, 0):
                raise GuardError("Stop is on the wrong side or inside broker minimum distance")
            if tick.ask - tick.bid > distance * 0.15:
                raise GuardError("Spread exceeds 15% of stop distance")
            unit_loss = self.loss(r["type"], r["symbol"], 1.0, price, sl)
            if unit_loss <= 0:
                raise GuardError("Invalid stop-loss risk")
            budget = min(c["risk_budget_usc"], c["bot_loss_budget_usc"] + min(net, 0), c["total_risk_budget_usc"] - total)
            # Reserve 10% for normal execution drift; never round volume upward.
            requested = float(r.get("volume", 0))
            if not finite_positive(requested):
                raise GuardError("Invalid requested volume")
            volume = min(requested, s.volume_max, c["max_volume"], budget * 0.9 / unit_loss)
            volume = round(math.floor(volume / s.volume_step + 1e-9) * s.volume_step, 8)
            if volume < s.volume_min - 1e-9:
                raise GuardError("Minimum broker volume exceeds the small risk budget; skipping")
            r["volume"] = volume
            if self.loss(r["type"], r["symbol"], volume, price, sl) > budget + 1e-8:
                raise GuardError("Rounded volume exceeds budget")
            margin = n.order_calc_margin(r["type"], r["symbol"], volume, price)
            if margin is None or not math.isfinite(margin) or margin > account.margin_free * 0.1:
                raise GuardError("Insufficient margin buffer")
        return r

    def order_send(self, request):
        with locked() as db:
            try:
                r = self._prepare(db, request)
                check = self.native.order_check(r)
                if check is None or check.retcode != 0:
                    raise GuardError("Broker preflight rejected order: " + str(getattr(check, "comment", "unavailable")))
                event(db, self.bot, "submit", r)
                try:
                    result = self.native.order_send(r)
                except Exception as exc:
                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','exception during order submission; reconcile manually')")
                    raise GuardError("Unknown broker submission outcome; halted without retry") from exc
                event(db, self.bot, "result", result._asdict() if result else {"error": self.native.last_error()})
                if result is None:
                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','ambiguous order result; reconcile manually')")
                    raise GuardError("Ambiguous broker result; trial halted without retry")
                if result.retcode not in (self.native.TRADE_RETCODE_DONE, self.native.TRADE_RETCODE_DONE_PARTIAL):
                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','broker order not completed; inspect before retry')")
                if not r.get("position") and result.retcode in (self.native.TRADE_RETCODE_DONE, self.native.TRADE_RETCODE_DONE_PARTIAL):
                    updated = self.native.positions_get()
                    if updated is None:
                        db.execute("INSERT OR REPLACE INTO flags VALUES('halt','post-fill positions unavailable')")
                    else:
                        for p in updated:
                            if p.magic == self.magic:
                                db.execute("INSERT OR IGNORE INTO owners VALUES(?,?)", (p.identifier, self.bot))
                                if not p.sl or self.loss(p.type, p.symbol, p.volume, p.price_open, p.sl) > self.cfg()["risk_budget_usc"]:
                                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','post-fill protection or risk mismatch')")
                return result
            except GuardError as e:
                message = str(e)
                event(db, self.bot, "blocked", message)
                print(f"[{self.bot}] GUARD: {message}", flush=True)
                return SimpleNamespace(retcode=10006, comment=message, order=0, deal=0, volume=0.0,
                                       _asdict=lambda: {"retcode": 10006, "comment": message, "order": 0, "request": SimpleNamespace(_asdict=lambda: dict(request))})

    def __getattr__(self, name):
        return getattr(self.native, name)
