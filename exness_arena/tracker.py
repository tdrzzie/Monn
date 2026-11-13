"""Read-only MT5 collector; local SQLite ledger and localhost dashboard."""
from __future__ import annotations

import csv
import json
import threading
import time
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import MetaTrader5 as native
from .common import REGISTRY, STATE, PACKAGE, health, locked, now, settings
from .guard import Gateway
from .identity import specs, resolve, default_strategy, strategy_key


def summarize(deals, positions, owners, cfg, samples=None, group_specs=None):
    """Position lifecycle attribution, including exits with manual/broker magic numbers."""
    groups = group_specs if group_specs is not None else REGISTRY
    def group_for(record):
        key = resolve(record.magic, getattr(record, 'symbol', None))
        if key is None:
            return None
        return key if group_specs is not None else specs()[key]['repo']
    for d in sorted(deals, key=lambda d: (d.time_msc, d.ticket)):
        group = group_for(d)
        if d.entry == 0 and group and d.position_id:
            owners.setdefault(d.position_id, group)
    for p in positions:
        group = group_for(p)
        if group:
            owners.setdefault(p.identifier, group)
    opened = {p.identifier for p in positions}
    grouped = {}
    unallocated = 0.0
    for d in deals:
        value = d.profit + d.swap + d.commission + getattr(d, "fee", 0.0)
        if d.position_id in owners:
            key = (owners[d.position_id], d.position_id)
            grouped[key] = grouped.get(key, 0.0) + value
        elif d.position_id == 0 and d.type not in (0, 1):
            # Deposits/credits are not bot trading returns. Unallocated fees remain explicit.
            unallocated += d.commission + getattr(d, "fee", 0.0)
    result = []
    for bot, spec in groups.items():
        closed = [v for (b, pid), v in grouped.items() if b == bot and pid not in opened]
        booked = sum(v for (b, _), v in grouped.items() if b == bot)
        floating = sum(p.profit + p.swap for p in positions if owners.get(p.identifier) == bot)
        wins = sum(v > 0 for v in closed)
        gains, losses = sum(v for v in closed if v > 0), -sum(v for v in closed if v < 0)
        series = [0.0] + list((samples or {}).get(bot, [])) + [booked + floating]
        peak, drawdown = series[0], 0.0
        for value in series:
            peak = max(peak, value)
            drawdown = max(drawdown, peak - value)
        result.append({"repo": bot, "magic": spec["magic"], "closed_positions": len(closed),
            "win_rate": wins / len(closed) if closed else None, "profit_factor": gains / losses if losses else None,
            "booked_net_usc": booked, "closed_net_usc": sum(closed), "floating_usc": floating,
            "total_net_usc": booked + floating, "total_net_usd": (booked + floating) / 100,
            "sampled_drawdown_usc": drawdown, "risk_budget_usc": cfg["risk_budget_usc"],
            "open_positions": sum(owners.get(p.identifier) == bot for p in positions),
            "api_cost_usd": 0.0 if bot in ("Monn", "MT5-Python-Trading", "tradingbot", "Forex-Trading-Bot", "mt5_live_trading_bot") else None})
    return result, unallocated


def collect():
    cfg = settings()
    Gateway(native, "Monn").validate_account()
    ds = native.history_deals_get(datetime.fromtimestamp(cfg["start_epoch"], timezone.utc), datetime.now(timezone.utc))
    ps = native.positions_get()
    if ds is None or ps is None:
        raise RuntimeError("Native MT5 history/positions unavailable")
    account = native.account_info()
    if account is None:
        raise RuntimeError("Account information unavailable")
    timestamp = now()
    with locked() as db:
        owners = dict(db.execute("SELECT position_id,bot FROM owners"))
        samples = {b: [r[0] for r in db.execute("SELECT net+floating FROM samples WHERE bot=? ORDER BY time", (b,))] for b in REGISTRY}
        rows, unallocated = summarize(ds, ps, owners, cfg, samples)
        strategy_specs = specs()
        strategy_owners = {pid: strategy_key(bot, strategy) for pid, bot, strategy in db.execute('SELECT * FROM strategy_owners')}
        strategy_samples = {}
        for key, spec in strategy_specs.items():
            old = [r[0] for r in db.execute('SELECT net+floating FROM strategy_samples WHERE bot=? AND strategy=? ORDER BY time', (spec['repo'], spec['strategy']))]
            # Keep the pre-migration drawdown samples for each repo's original strategy.
            if spec['strategy'] == default_strategy(spec['repo']):
                first = db.execute('SELECT MIN(time) FROM strategy_samples WHERE bot=? AND strategy=?', (spec['repo'], spec['strategy'])).fetchone()[0]
                prefix = [r[0] for r in db.execute('SELECT net+floating FROM samples WHERE bot=? AND (? IS NULL OR time<?) ORDER BY time', (spec['repo'], first, first))]
                old = prefix + old
            strategy_samples[key] = old
        strategy_rows, _ = summarize(ds, ps, strategy_owners, cfg, strategy_samples, group_specs=strategy_specs)
        for pid, key in strategy_owners.items():
            spec = strategy_specs[key]
            db.execute('INSERT OR IGNORE INTO strategy_owners VALUES(?,?,?)', (pid, spec['repo'], spec['strategy']))
        for row in strategy_rows:
            key = row['repo']
            spec = strategy_specs[key]
            row['strategy'] = spec['strategy']
            row['repo'] = spec['repo']
            row['strategy_key'] = key
            row['api_cost_usd'] = 0.0 if spec.get('runner') else None
            row['loss_budget_usc'] = cfg.get('strategy_loss_budget_usc', cfg['bot_loss_budget_usc'])
            db.execute('INSERT OR REPLACE INTO strategy_samples VALUES(?,?,?,?,?)', (timestamp, row['repo'], row['strategy'], row['booked_net_usc'], row['floating_usc']))
        position_rows = []
        for p in ps:
            key = strategy_owners.get(p.identifier)
            if key:
                spec = strategy_specs[key]
                position_rows.append({'repo': spec['repo'], 'strategy': spec['strategy'], 'position_id': p.identifier,
                    'ticket': p.ticket, 'magic': p.magic, 'symbol': p.symbol, 'direction': 'buy' if p.type == 0 else 'sell',
                    'volume': p.volume, 'stop_loss': p.sl, 'take_profit': p.tp, 'profit_usc': p.profit, 'swap_usc': p.swap, 'entry_price': p.price_open, 'current_price': p.price_current, 'opened_utc': datetime.fromtimestamp(p.time, timezone.utc).isoformat()})
        for pid, bot in owners.items():
            db.execute("INSERT OR IGNORE INTO owners VALUES(?,?)", (pid, bot))
        for d in ds:
            if d.position_id in owners:
                db.execute("INSERT OR REPLACE INTO deals VALUES(?,?,?,?)", (d.ticket, d.position_id, owners[d.position_id], json.dumps(d._asdict())))
        for row in rows:
            db.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?)", (timestamp, row["repo"], row["booked_net_usc"], row["floating_usc"]))
        statuses = {b: {"time": t, "status": s, "detail": detail} for b, t, s, detail in db.execute("SELECT * FROM health")}
        events = [{"time": t, "repo": b, "strategy": strategy, "kind": k, "detail": json.loads(d)} for t, b, strategy, k, d in db.execute("SELECT e.time,e.bot,se.strategy,e.kind,e.detail FROM events e LEFT JOIN strategy_events se ON se.event_id=e.id ORDER BY e.id DESC LIMIT 30")]
        halt = db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()
        coverage = [{"repo": b, "symbol": s, "time": t, "status": status, "detail": detail, "last_bar": bar}
                    for b, s, t, status, detail, bar in db.execute("SELECT * FROM coverage ORDER BY bot,symbol")]
        strategy_coverage = [{'repo': b, 'strategy': strategy, 'symbol': s, 'time': t, 'status': status, 'detail': detail, 'last_bar': bar}
                             for b, strategy, s, t, status, detail, bar in db.execute('SELECT * FROM strategy_coverage ORDER BY bot,strategy,symbol')]
        strategy_health = {strategy_key(b, s): {'time': t, 'status': status, 'detail': detail}
                           for b, s, t, status, detail in db.execute('SELECT * FROM strategy_health')}
    for row in rows:
        row["status"] = statuses.get(row["repo"], {"status": "blocked", "detail": REGISTRY[row["repo"]].get("blocked", "not started")})
    for row in strategy_rows:
        row['status'] = strategy_health.get(row['strategy_key'], statuses.get(row['repo'], {'status': 'not_started'}))
        if row['repo'] in cfg['enabled_bots'] and row['strategy'] not in cfg.get('enabled_strategies', {}).get(row['repo'], [s['strategy'] for s in strategy_specs.values() if s['repo'] == row['repo']]):
            row['status'] = {'status': 'disabled', 'detail': 'Strategy is registered but not enabled'}
    data = {"updated_utc": timestamp, "currency": "USC", "rows": rows,
            "enabled_repos": cfg['enabled_bots'], "enabled_strategies": cfg.get('enabled_strategies', {}),
            "account": {"balance_usc": account.balance, "equity_usc": account.equity,
                        "free_margin_usc": account.margin_free, "margin_usc": account.margin,
                        "initial_equity_usc": cfg['initial_equity_usc'],
                        "drawdown_threshold_usc": cfg['initial_equity_usc']*(1-cfg['account_loss_fraction']),
                        "equity_decline_fraction": max(0,1-account.equity/cfg['initial_equity_usc'])},
            "halt": halt[0] if halt else None, "events": events, "unallocated_fees_usc": unallocated,
            "watchlist": cfg["symbols"], "coverage": coverage,
            "strategies": strategy_rows, "positions": position_rows, "strategy_coverage": strategy_coverage,
            "compatibility": json.loads((PACKAGE / 'compatibility.json').read_text()) if (PACKAGE / 'compatibility.json').exists() else {},
            "risk_limits": {"default_max_volume": cfg["max_volume"], "max_volume_by_symbol": cfg.get("max_volume_by_symbol", {}), "per_position_usc": cfg['risk_budget_usc'], "combined_open_risk_cap_usc": cfg.get('total_risk_budget_usc'),
                            "strategy_loss_usc": cfg.get('strategy_loss_budget_usc', cfg['bot_loss_budget_usc']),
                            "repo_loss_usc": cfg['bot_loss_budget_usc'], "account_drawdown_fraction": cfg['account_loss_fraction']},
            "position_policy": {"mode": "hedge_with_profitable_additions", "max_positions_per_pair_per_bot": None,
                                "max_positions_per_bot": None, "scope": "repo + strategy + pair + direction",
                                "additions_require": "every same-strategy same-direction position positive after swap and opening fees"},
            "note": "USC / 100 = USD. Net includes deal fees and swap. Drawdown is sampled every 10 seconds. Shared equity/margin and slippage affect comparisons."}
    target = STATE / "results.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(target)
    with (STATE / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        flat = [{k: v for k, v in r.items() if k != "status"} for r in rows]
        writer = csv.DictWriter(handle, fieldnames=flat[0].keys())
        writer.writeheader()
        writer.writerows(flat)
    for filename, records in [('strategies.csv', [{k: v for k, v in r.items() if k != 'status'} for r in strategy_rows]), ('positions.csv', position_rows)]:
        with (STATE / filename).open('w', newline='', encoding='utf-8') as handle:
            fields = list(records[0]) if records else ['repo', 'strategy', 'position_id']
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    health("tracker", "running", timestamp)
    # Research uses a separate DB and never owns the live heartbeat or halt flags.
    try:
        from .research import collect as collect_research
        collect_research(native, ps, ds)
    except Exception as exc:
        from .research import research_error
        research_error('tracker sampling', exc)
    return data


DASHBOARD = PACKAGE / 'dashboard'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlsplit(self.path)
        path = route.path
        content_type = 'application/json; charset=utf-8'
        try:
            if path == '/api/results':
                payload = (STATE / 'results.json').read_bytes()
            elif path == '/api/research':
                target = STATE / 'research-summary.json'
                payload = target.read_bytes() if target.exists() else b'{"status":"initializing","paper_only":true}'
            elif path == '/api/history':
                from .dashboard_api import history
                q = parse_qs(route.query)
                payload = json.dumps(history(q.get('repo',[''])[0], q.get('strategy',[''])[0], q.get('range',['all'])[0])).encode()
            elif path == '/api/trades':
                from .dashboard_api import closed_trades
                payload = json.dumps(closed_trades()).encode()
            elif path == '/api/protection':
                payload = (PACKAGE / 'protection.json').read_bytes()
            else:
                assets = {'/': ('index.html','text/html; charset=utf-8'),
                          '/dashboard.css': ('dashboard.css','text/css; charset=utf-8'),
                          '/dashboard.js': ('dashboard.js','text/javascript; charset=utf-8'),
                          '/dashboard-core.mjs': ('dashboard-core.mjs','text/javascript; charset=utf-8')}
                if path not in assets:
                    self.send_error(404); return
                filename, content_type = assets[path]
                payload = (DASHBOARD / filename).read_bytes()
        except ValueError as exc:
            self.send_error(400,str(exc)); return
        except Exception:
            self.send_error(503,'Dashboard data temporarily unavailable'); return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def main():
    if not native.initialize(path=settings()["terminal_path"]):
        raise RuntimeError("MT5 connection failed")
    collect()
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Read-only tracker: http://127.0.0.1:8765", flush=True)
    try:
        while True:
            try:
                collect()
            except Exception as e:
                health("tracker", "error", str(e))
                print("Tracker error:", e, flush=True)
            time.sleep(10)
    finally:
        server.shutdown()
        native.shutdown()


if __name__ == "__main__":
    main()
