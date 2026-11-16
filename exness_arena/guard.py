from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from .common import REGISTRY, STATE, event, locked, settings
from .identity import strategies, default_strategy, strategy_key, resolve, trial_magics
from .volume import entry_volume_cap


class GuardError(RuntimeError):
    pass


class DisconnectedError(GuardError):
    """Known unavailable MT5 connection; never an instruction to retry an order."""


def finite_positive(value):
    return isinstance(value, (float, int)) and math.isfinite(value) and value > 0


class Gateway:
    """All orders are serialized across bots and validated against native MT5 state.

    The guard sizes downward only. Monetary figures are account currency (USC).
    Stop-loss risk is an estimate; gaps/slippage can exceed these limits.
    """
    def __init__(self, native, bot, config=None, strategy=None):
        if bot not in REGISTRY:
            raise GuardError("Unregistered bot")
        self.native, self.bot = native, bot
        self.strategy = strategy or default_strategy(bot)
        if self.strategy not in strategies(bot):
            raise GuardError('Unregistered strategy')
        self.magic = strategies(bot)[self.strategy]['magic']
        self.key = strategy_key(bot, self.strategy)
        self.config = config

    def for_strategy(self, strategy):
        return Gateway(self.native, self.bot, self.config, strategy=strategy)

    def owns(self, position):
        return resolve(position.magic, getattr(position, 'symbol', None)) == self.key

    def cfg(self):
        return self.config if self.config is not None else settings()

    def validate_account(self):
        a = self.native.account_info()
        t = self.native.terminal_info()
        c = self.cfg()
        if a is not None and (a.login, a.server, a.currency, a.margin_mode) != (c["login"], c["server"], "USC", 2):
            raise GuardError("Terminal account does not match the pinned Exness USC hedging account")
        if a is None or t is None or not t.connected:
            raise DisconnectedError("MT5 disconnected")
        if not a.trade_allowed or not a.trade_expert or not t.trade_allowed or t.tradeapi_disabled:
            raise GuardError("Automated trading is unavailable")
        return a

    def initialize(self, *args, **kwargs):
        # Always reuse the pinned existing terminal. Never switch accounts using bot credentials.
        if not self.native.initialize(path=self.cfg()["terminal_path"], timeout=5000):
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
        return tuple(p for p in ps if self.owns(p))

    def orders_get(self, *args, **kwargs):
        if "symbol" in kwargs:
            kwargs["symbol"] = self.symbol(kwargs["symbol"])
        orders = self.native.orders_get(*args, **kwargs)
        if orders is None:
            raise GuardError("Cannot retrieve orders")
        return tuple(o for o in orders if self.owns(o))

    def trial_position_symbols(self):
        ps = self.native.positions_get()
        if ps is None:
            raise GuardError("Cannot retrieve positions for watchlist reconciliation")
        magics = trial_magics()
        return [p.symbol for p in ps if p.magic in magics]

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
        return tuple(d for d in ds if self.owns(d))

    def _positions(self):
        ps = self.native.positions_get()
        orders = self.native.orders_get()
        if ps is None or orders is None:
            raise GuardError("Account state unavailable")
        # This trial never places pending orders. An unexpected trial order blocks new risk.
        magics = trial_magics()
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

    def _entry_limits(self, db, account, ps, symbol, side):
        c = self.cfg()
        if (STATE / "STOP").exists() or db.execute("SELECT 1 FROM flags WHERE name='halt'").fetchone():
            raise GuardError("Trial is halted; new entries blocked")
        if db.execute('SELECT 1 FROM flags WHERE name=?', ('strategy_halt:' + self.key,)).fetchone():
            raise GuardError('Strategy is quarantined; new entries blocked')
        if self.bot not in c["enabled_bots"]:
            raise GuardError("Bot has not passed readiness checks")
        if self.strategy not in c.get('enabled_strategies', {}).get(self.bot, list(strategies(self.bot))):
            raise GuardError('Strategy is not enabled')
        from .promotion import live_selected, frozen_issue
        if not live_selected(c, self.bot, self.strategy):
            raise GuardError('Strategy is test-only; live entries disabled')
        if c.get('strategy_stages') is not None:
            issue = frozen_issue(self.bot, self.strategy, symbol, c.get('promotion_policy_sha256', 'MISSING'), c.get('promotion_artifact_sha256'))
            if issue:
                raise GuardError(issue)
        allowed_symbols = strategies(self.bot)[self.strategy].get('symbols')
        if allowed_symbols and symbol not in allowed_symbols:
            raise GuardError('Pair is outside this strategy parameter profile')
        # A missing/stale tracker stops all new entries; exits remain available.
        row = db.execute("SELECT time FROM health WHERE bot='tracker' AND status='running'").fetchone()
        if not row or time.time() - datetime.fromisoformat(row[0]).timestamp() > 90:
            raise GuardError("Tracker heartbeat is missing or stale")
        if account.equity <= c["initial_equity_usc"] * (1 - c["account_loss_fraction"]):
            db.execute("INSERT OR REPLACE INTO flags VALUES('halt','account drawdown')")
            raise GuardError("Account-wide drawdown limit reached")
        start = datetime.fromtimestamp(c["start_epoch"], timezone.utc)
        deals = self.native.history_deals_get(start, datetime.now(timezone.utc))
        if deals is None:
            raise GuardError("Deal history unavailable")
        same_direction = [p for p in ps if self.owns(p) and p.symbol == symbol and p.type == side]
        for p in same_direction:
            # Every existing position in this direction must be profitable individually.
            # Include swap and known opening fees, conservatively retaining all entry fees
            # after partial closes. A winning basket cannot conceal a losing add-on.
            entry_fees = sum(d.commission + getattr(d, "fee", 0.0) for d in deals
                             if d.position_id == p.identifier and d.entry == 0)
            pnl = p.profit + p.swap + entry_fees
            if not math.isfinite(pnl) or pnl <= 0:
                raise GuardError("Same-direction additions require every existing position to be profitable after swap and opening fees")
        owners = {d.position_id for d in deals if (resolve(d.magic, getattr(d, 'symbol', None)) or '').startswith(self.bot + '/')}
        owners.update(r[0] for r in db.execute("SELECT position_id FROM owners WHERE bot=?", (self.bot,)))
        net = sum(d.profit + d.swap + d.commission + getattr(d, "fee", 0) for d in deals if d.position_id in owners)
        if net <= -c["bot_loss_budget_usc"]:
            raise GuardError("Per-bot trial loss budget exhausted")
        strategy_positions = {d.position_id for d in deals if self.owns(d)}
        strategy_positions.update(r[0] for r in db.execute('SELECT position_id FROM strategy_owners WHERE bot=? AND strategy=?', (self.bot, self.strategy)))
        strategy_net = sum(d.profit + d.swap + d.commission + getattr(d, 'fee', 0) for d in deals if d.position_id in strategy_positions)
        strategy_limit = c.get('strategy_loss_budget_usc', c['bot_loss_budget_usc'])
        if strategy_net <= -strategy_limit:
            raise GuardError('Per-strategy trial loss budget exhausted')
        total = 0.0
        magics = trial_magics()
        for p in ps:
            if p.magic in magics:
                total += self.loss(p.type, p.symbol, p.volume, p.price_open, p.sl)
        headroom = min(c['bot_loss_budget_usc'] + min(net, 0), strategy_limit + min(strategy_net, 0))
        return total, headroom

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
        r["comment"] = ("trial:" + self.strategy)[:31]
        if ticket:
            p = next((p for p in ps if p.ticket == ticket), None)
            if p is None or not self.owns(p):
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
            r["symbol"] = self.symbol(r.get("symbol", ""))
            if r.get("type") not in (n.ORDER_TYPE_BUY, n.ORDER_TYPE_SELL):
                raise GuardError("Invalid order direction")
            total, loss_headroom = self._entry_limits(db, account, ps, r["symbol"], r["type"])
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
            budget = min(c["risk_budget_usc"], loss_headroom)
            if 'total_risk_budget_usc' not in c:
                raise GuardError('Combined open-risk setting is missing; use null to disable it explicitly')
            combined_cap = c['total_risk_budget_usc']
            if combined_cap is not None:
                if not finite_positive(combined_cap):
                    raise GuardError('Invalid combined open-risk cap')
                budget = min(budget, combined_cap - total)
            # Reserve 10% for normal execution drift; never round volume upward.
            requested = float(r.get("volume", 0))
            if not finite_positive(requested):
                raise GuardError("Invalid requested volume")
            try:
                volume_cap = entry_volume_cap(r['symbol'], c)
            except ValueError as exc:
                raise GuardError(str(exc)) from exc
            volume = min(requested, s.volume_max, volume_cap, budget * 0.9 / unit_loss)
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
                event(db, self.bot, "submit", r, strategy=self.strategy)
                try:
                    result = self.native.order_send(r)
                except Exception as exc:
                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','exception during order submission; reconcile manually')")
                    raise GuardError("Unknown broker submission outcome; halted without retry") from exc
                event(db, self.bot, "result", result._asdict() if result else {"error": self.native.last_error()}, strategy=self.strategy)
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
                            if self.owns(p):
                                db.execute("INSERT OR IGNORE INTO owners VALUES(?,?)", (p.identifier, self.bot))
                                db.execute('INSERT OR IGNORE INTO strategy_owners VALUES(?,?,?)', (p.identifier, self.bot, self.strategy))
                                if not p.sl or self.loss(p.type, p.symbol, p.volume, p.price_open, p.sl) > self.cfg()["risk_budget_usc"]:
                                    db.execute("INSERT OR REPLACE INTO flags VALUES('halt','post-fill protection or risk mismatch')")
                return result
            except GuardError as e:
                message = str(e)
                event(db, self.bot, "blocked", message, strategy=self.strategy)
                print(f"[{self.bot}] GUARD: {message}", flush=True)
                return SimpleNamespace(retcode=10006, comment=message, order=0, deal=0, volume=0.0,
                                       _asdict=lambda: {"retcode": 10006, "comment": message, "order": 0, "request": SimpleNamespace(_asdict=lambda: dict(request))})

    def __getattr__(self, name):
        return getattr(self.native, name)
