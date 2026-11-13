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


def summarize(deals, positions, owners, cfg, samples=None):
    """Position lifecycle attribution, including exits with manual/broker magic numbers."""
    by_magic = {v["magic"]: b for b, v in REGISTRY.items()}
    for d in sorted(deals, key=lambda d: (d.time_msc, d.ticket)):
        if d.entry == 0 and d.magic in by_magic and d.position_id:
            owners.setdefault(d.position_id, by_magic[d.magic])
    for p in positions:
        if p.magic in by_magic:
            owners.setdefault(p.identifier, by_magic[p.magic])
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
    for bot, spec in REGISTRY.items():
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
            "api_cost_usd": 0.0 if bot in ("Monn", "MT5-Python-Trading") else None})
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
        for pid, bot in owners.items():
            db.execute("INSERT OR IGNORE INTO owners VALUES(?,?)", (pid, bot))
        for d in ds:
            if d.position_id in owners:
                db.execute("INSERT OR REPLACE INTO deals VALUES(?,?,?,?)", (d.ticket, d.position_id, owners[d.position_id], json.dumps(d._asdict())))
        for row in rows:
            db.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?)", (timestamp, row["repo"], row["booked_net_usc"], row["floating_usc"]))
        statuses = {b: {"time": t, "status": s, "detail": detail} for b, t, s, detail in db.execute("SELECT * FROM health")}
        events = [{"time": t, "repo": b, "kind": k, "detail": json.loads(d)} for t, b, k, d in db.execute("SELECT time,bot,kind,detail FROM events ORDER BY id DESC LIMIT 30")]
        halt = db.execute("SELECT value FROM flags WHERE name='halt'").fetchone()
        coverage = [{"repo": b, "symbol": s, "time": t, "status": status, "detail": detail, "last_bar": bar}
                    for b, s, t, status, detail, bar in db.execute("SELECT * FROM coverage ORDER BY bot,symbol")]
    for row in rows:
        row["status"] = statuses.get(row["repo"], {"status": "blocked", "detail": REGISTRY[row["repo"]].get("blocked", "not started")})
    data = {"updated_utc": timestamp, "currency": "USC", "rows": rows,
            "halt": halt[0] if halt else None, "events": events, "unallocated_fees_usc": unallocated,
            "watchlist": cfg["symbols"], "coverage": coverage,
            "position_policy": {"mode": "hedge_with_profitable_additions", "max_positions_per_pair_per_bot": None,
                                "max_positions_per_bot": None, "additions_require": "every same-direction position positive after swap and opening fees"},
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
    health("tracker", "running", timestamp)
    return data


HTML = """<!doctype html><meta charset=utf-8><title>Exness cent bot trial</title>
<style>body{font:15px system-ui;background:#10151d;color:#e3eaf4;padding:32px}h1{font-size:25px}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #344052}.muted{color:#9eb0c8}pre{white-space:pre-wrap}a{color:#85c4ff}</style>
<h1>Exness cent bot trial</h1><p id=info></p><p class=muted>USC = US cents · 100 USC = $1 · Each ready bot has the same risk cap. Pending bots are excluded from the comparison.</p>
<table><thead><tr><th>Repository</th><th>Status</th><th>Magic</th><th>Closed / open</th><th>Closed net USC</th><th>Floating USC</th><th>Total USD</th><th>Win rate</th><th>Sampled DD USC</th></tr></thead><tbody id=rows></tbody></table>
<p class=muted>Net includes commission, fees and swap. API costs are separate. A profitable short trial does not establish a lasting edge. Limits cannot guarantee maximum losses through gaps.</p>
<h2>Pair monitoring</h2><p id=universe></p><pre id=coverage></pre>
<h2>Latest execution events</h2><pre id=events></pre><script>
async function refresh(){try{const d=await(await fetch('/api/results')).json();document.querySelector('#info').textContent=d.updated_utc+(d.halt?' · HALTED: '+d.halt:'');const b=document.querySelector('#rows');b.replaceChildren();for(const r of d.rows){const tr=document.createElement('tr');for(const v of [r.repo,r.status.status,r.magic,r.closed_positions+' / '+r.open_positions,r.closed_net_usc.toFixed(3),r.floating_usc.toFixed(3),r.total_net_usd.toFixed(4),r.win_rate===null?'—':(100*r.win_rate).toFixed(1)+'%',r.sampled_drawdown_usc.toFixed(3)]){const td=document.createElement('td');td.textContent=v;tr.append(td)}tr.title=r.status.detail||'';b.append(tr)}document.querySelector('#events').textContent=d.events.map(e=>e.time+' '+e.repo+' '+e.kind+' '+JSON.stringify(e.detail)).join('\\n')}catch(e){document.querySelector('#info').textContent='Tracker unavailable: '+e}}refresh();setInterval(refresh,10000)
async function pairCoverage(){try{const d=await(await fetch('/api/results')).json();document.querySelector('#universe').textContent=(d.watchlist||[]).length+' pairs · M15 · broker inventory refreshed every minute';document.querySelector('#coverage').textContent=(d.coverage||[]).map(x=>x.repo+' | '+x.symbol+' | '+x.status+' | '+x.detail).join('\\n')}catch(e){document.querySelector('#universe').textContent='Pair status unavailable'}}pairCoverage();setInterval(pairCoverage,10000)
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
