"""Read-only MT5 collector; local SQLite ledger and localhost dashboard."""
from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import MetaTrader5 as native
from .common import REGISTRY, STATE, health, locked, now, settings
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
                    'volume': p.volume, 'stop_loss': p.sl, 'take_profit': p.tp, 'profit_usc': p.profit, 'swap_usc': p.swap})
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
    for row in rows:
        row["status"] = statuses.get(row["repo"], {"status": "blocked", "detail": REGISTRY[row["repo"]].get("blocked", "not started")})
    for row in strategy_rows:
        row['status'] = statuses.get(row['repo'], {'status': 'not_started'})
        if row['repo'] in cfg['enabled_bots'] and row['strategy'] not in cfg.get('enabled_strategies', {}).get(row['repo'], [s['strategy'] for s in strategy_specs.values() if s['repo'] == row['repo']]):
            row['status'] = {'status': 'disabled', 'detail': 'Strategy is registered but not enabled'}
    data = {"updated_utc": timestamp, "currency": "USC", "rows": rows,
            "halt": halt[0] if halt else None, "events": events, "unallocated_fees_usc": unallocated,
            "watchlist": cfg["symbols"], "coverage": coverage,
            "strategies": strategy_rows, "positions": position_rows, "strategy_coverage": strategy_coverage,
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
    return data


HTML = """<!doctype html><meta charset=utf-8><title>Exness cent bot trial</title>
<style>body{font:15px system-ui;background:#10151d;color:#e3eaf4;padding:32px}h1{font-size:25px}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #344052}.muted{color:#9eb0c8}pre{white-space:pre-wrap}a{color:#85c4ff}</style>
<h1>Exness cent bot trial</h1><p id=info></p><p class=muted>USC = US cents Â· 100 USC = $1 Â· Each ready bot has the same risk cap. Pending bots are excluded from the comparison.</p>
<table><thead><tr><th>Repository</th><th>Status</th><th>Magic</th><th>Closed / open</th><th>Closed net USC</th><th>Floating USC</th><th>Total USD</th><th>Win rate</th><th>Sampled DD USC</th></tr></thead><tbody id=rows></tbody></table>
<h2>Strategy results</h2><p class=muted>Ownership and profitable additions are isolated by strategy. Shared account risk and existing repo loss backstops remain in force.</p>
<table><thead><tr><th>Repository</th><th>Strategy</th><th>Status</th><th>Magic</th><th>Closed / open</th><th>Closed net USC</th><th>Floating USC</th><th>Total USD</th><th>Sampled DD USC</th></tr></thead><tbody id=strategyrows></tbody></table>
<h2>Open positions by strategy</h2><table><thead><tr><th>Repository</th><th>Strategy</th><th>Ticket</th><th>Pair</th><th>Direction</th><th>Volume</th><th>SL</th><th>TP</th><th>Floating USC</th></tr></thead><tbody id=positionrows></tbody></table>
<p class=muted>Net includes commission, fees and swap. API costs are separate. A profitable short trial does not establish a lasting edge. Limits cannot guarantee maximum losses through gaps.</p>
<h2>Pair monitoring</h2><p id=universe></p><pre id=coverage></pre>
<h2>Latest execution events</h2><pre id=events></pre><script>
async function refresh(){try{const d=await(await fetch('/api/results')).json();document.querySelector('#info').textContent=d.updated_utc+(d.halt?' Â· HALTED: '+d.halt:'');const b=document.querySelector('#rows');b.replaceChildren();for(const r of d.rows){const tr=document.createElement('tr');for(const v of [r.repo,r.status.status,r.magic,r.closed_positions+' / '+r.open_positions,r.closed_net_usc.toFixed(3),r.floating_usc.toFixed(3),r.total_net_usd.toFixed(4),r.win_rate===null?'â€”':(100*r.win_rate).toFixed(1)+'%',r.sampled_drawdown_usc.toFixed(3)]){const td=document.createElement('td');td.textContent=v;tr.append(td)}tr.title=r.status.detail||'';b.append(tr)}document.querySelector('#events').textContent=d.events.map(e=>e.time+' '+e.repo+(e.strategy?'/'+e.strategy:'')+' '+e.kind+' '+JSON.stringify(e.detail)).join('\\n')}catch(e){document.querySelector('#info').textContent='Tracker unavailable: '+e}}refresh();setInterval(refresh,10000)
async function pairCoverage(){try{const d=await(await fetch('/api/results')).json();document.querySelector('#universe').textContent=(d.watchlist||[]).length+' pairs Â· M15 Â· broker inventory refreshed every minute';document.querySelector('#coverage').textContent=(d.strategy_coverage||d.coverage||[]).map(x=>x.repo+' / '+(x.strategy||'legacy')+' | '+x.symbol+' | '+x.status+' | '+x.detail).join('\\n')}catch(e){document.querySelector('#universe').textContent='Pair status unavailable'}}pairCoverage();setInterval(pairCoverage,10000)
function fillTable(id,rows){const b=document.getElementById(id);b.replaceChildren();for(const values of rows){const tr=document.createElement('tr');for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td)}b.append(tr)}}
async function strategies(){try{const d=await(await fetch('/api/results')).json();fillTable('strategyrows',(d.strategies||[]).map(r=>[r.repo,r.strategy,r.status.status,r.magic,r.closed_positions+' / '+r.open_positions,r.closed_net_usc.toFixed(3),r.floating_usc.toFixed(3),r.total_net_usd.toFixed(4),r.sampled_drawdown_usc.toFixed(3)]));fillTable('positionrows',(d.positions||[]).map(p=>[p.repo,p.strategy,p.ticket,p.symbol,p.direction,p.volume,p.stop_loss,p.take_profit,(p.profit_usc+p.swap_usc).toFixed(3)]))}catch(e){document.querySelector('#info').textContent='Strategy data unavailable: '+e}}strategies();setInterval(strategies,10000)
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/results":
            payload = (STATE / "results.json").read_bytes()
            content_type = "application/json"
        elif self.path == "/":
            payload = HTML.encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
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
