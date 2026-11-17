"""Short research transactions; broker reads never run under a writer lock.

Uses the frozen exit engine unchanged. This collector has no order interface and
never edits execution settings, position ownership, strategy stages or halts.
"""
import json
import math
import time
from types import SimpleNamespace
from . import research as engine


class PreparedWrites:
    """Buffer advance's write-only SQL while it performs broker valuation."""
    def __init__(self):
        self.commands = []

    def execute(self, sql, params=()):
        if not sql.lstrip().upper().startswith(('INSERT ', 'UPDATE ')):
            raise ValueError('Shadow preparation accepts only write statements')
        self.commands.append((sql, params))
        return SimpleNamespace(rowcount=1)


def advance(native, row, quote, bars, observed):
    prepared = PreparedWrites()
    engine.advance(prepared, native, row, quote, bars, observed)
    with engine.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT * FROM shadows WHERE id=?', (row['id'],)).fetchone()
        # Do not overwrite a state advanced by another sampler in the meantime.
        if current is None or dict(current) != dict(row):
            return False
        for sql, params in prepared.commands:
            db.execute(sql, params)
    return True


def collect(native, positions, deals):
    observed = time.time()
    owners = {d.position_id: engine.resolve(d.magic, d.symbol) for d in deals
              if d.entry == 0 and engine.resolve(d.magic, d.symbol)}
    for deal in deals:
        if deal.position_id not in owners:
            continue
        context = None
        with engine.connect() as db:
            if deal.entry == 0:
                context = db.execute('''SELECT a.decision_id,r.version_id,a.request,a.result
                  FROM attempts a JOIN research_decisions r ON r.id=a.decision_id
                  WHERE r.mode='live_evaluation' AND (a.result_deal=? OR a.result_order=?) LIMIT 1''',
                  (deal.ticket, deal.order)).fetchone()
            known_risk = db.execute('SELECT 1 FROM live_initial_risk WHERE position_id=?',
                                   (deal.position_id,)).fetchone()
        risk = None
        if context and not known_risk and all(hasattr(deal,k) for k in ('type','volume','price')):
            result = json.loads(context[3]); proposal = json.loads(context[2])
            request = result.get('request') or proposal
            sl = request.get('sl') if isinstance(request, dict) else proposal.get('sl')
            if sl and sl > 0:
                pnl = native.order_calc_profit(deal.type,deal.symbol,deal.volume,deal.price,sl)
                if pnl is not None and math.isfinite(pnl) and pnl < 0:
                    risk = (deal.position_id,-pnl,sl)
        with engine.connect() as db:
            db.execute('INSERT OR IGNORE INTO live_deals VALUES(?,?,?)',
                       (deal.ticket,deal.position_id,engine.encode(deal)))
            if deal.entry == 0:
                db.execute('''INSERT INTO live_context VALUES(?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET
                  decision_id=excluded.decision_id,version_id=excluded.version_id,attribution=excluded.attribution
                  WHERE live_context.decision_id IS NULL AND excluded.decision_id IS NOT NULL''',
                  (deal.position_id,context[0] if context else None,context[1] if context else None,
                   'opening deal/order match' if context else 'unknown: opened before research or no recorded match'))
            if risk:
                db.execute('INSERT OR IGNORE INTO live_initial_risk VALUES(?,?,?)',risk)
    for position in positions:
        if not engine.resolve(position.magic,position.symbol):
            continue
        tick = native.symbol_info_tick(position.symbol)
        booked = sum(getattr(d,'profit',0)+getattr(d,'swap',0)+getattr(d,'commission',0)+getattr(d,'fee',0)
                     for d in deals if d.position_id == position.identifier)
        with engine.connect() as db:
            db.execute('INSERT OR IGNORE INTO live_path VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (position.identifier,int(observed*1000),position.profit,position.swap,
                        tick.bid if tick else None,tick.ask if tick else None,
                        position.sl,position.tp,position.volume,booked))
    with engine.connect() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM shadows WHERE status='open'")]
    market = {}
    for row in rows:
        symbol = row['symbol']
        try:
            if symbol not in market:
                oldest = min(r['opened'] for r in rows if r['symbol'] == symbol)
                count = min(10000,max(3,math.ceil((observed-oldest)/900)+2))
                bars = native.copy_rates_from_pos(symbol,native.TIMEFRAME_M15,1,count)
                market[symbol] = (native.symbol_info_tick(symbol),bars if bars is not None else [])
            advance(native,row,*market[symbol],observed)
        except Exception as exc:
            with engine.connect() as db:
                db.execute('INSERT INTO observations(time,kind,detail) VALUES(?,?,?)',
                           (observed,'shadow_mark_error',engine.encode({'shadow':row['id'],'error':str(exc)})))
    report = engine.summary()
    temp = engine.STATE / 'research-summary.next.json'
    temp.write_text(json.dumps(report,indent=2),encoding='utf-8')
    temp.replace(engine.STATE / 'research-summary.json')
    return report
