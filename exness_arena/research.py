"""Observational journal and paper-only exit experiments; never clears live gates.

The research database is separate from the execution journal. Shadow positions are
independent per-signal experiments, not a tradable portfolio or live-order replay.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
import zlib

from .common import STATE, settings
from .identity import resolve
from .shadow_plan import PLAN_ID, PLAN, VARIANTS, MFE_VARIANTS

REPOS = ('Monn', 'MT5-Python-Trading', 'tradingbot', 'mt5_live_trading_bot', 'Forex-Trading-Bot')
_sources = None


def plain(value):
    if hasattr(value, '_asdict'):
        return plain(value._asdict())
    if isinstance(value, SimpleNamespace):
        return plain(vars(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if hasattr(value, 'item'):
        return plain(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def encode(value):
    return json.dumps(plain(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


@contextmanager
def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / 'research.sqlite3', timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, time REAL, sources TEXT, config TEXT, environment TEXT);
      CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, payload BLOB NOT NULL, raw_bytes INTEGER);
      CREATE TABLE IF NOT EXISTS research_decisions(id TEXT PRIMARY KEY, time REAL, mode TEXT, bot TEXT, strategy TEXT,
        symbol TEXT, bar INTEGER, version_id TEXT, input_id TEXT, context TEXT, features TEXT, status TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS attempts(decision_id TEXT, ordinal INTEGER, request TEXT, quote TEXT, result TEXT,
        error TEXT, result_order INTEGER, result_deal INTEGER, PRIMARY KEY(decision_id,ordinal));
      CREATE INDEX IF NOT EXISTS attempt_orders ON attempts(result_order,result_deal);
      CREATE TABLE IF NOT EXISTS shadows(id TEXT PRIMARY KEY, decision_id TEXT, ordinal INTEGER, variant TEXT, bot TEXT,
        strategy TEXT, symbol TEXT, version_id TEXT, side INTEGER, volume REAL, entry REAL, sl REAL, tp REAL,
        initial_risk REAL, opened REAL, last_bar INTEGER, status TEXT, closed REAL, exit_price REAL, exit_reason TEXT,
        gross_usc REAL, r_multiple REAL, mfe_r REAL DEFAULT 0, mae_r REAL DEFAULT 0,
        UNIQUE(decision_id,ordinal,variant));
      CREATE TABLE IF NOT EXISTS shadow_events(id INTEGER PRIMARY KEY, shadow_id TEXT, time REAL, kind TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS shadow_rejections(decision_id TEXT, ordinal INTEGER, reason TEXT,
        PRIMARY KEY(decision_id,ordinal));
      CREATE TABLE IF NOT EXISTS live_deals(ticket INTEGER PRIMARY KEY, position_id INTEGER, payload TEXT);
      CREATE TABLE IF NOT EXISTS live_context(position_id INTEGER PRIMARY KEY, decision_id TEXT, version_id TEXT, attribution TEXT);
      CREATE TABLE IF NOT EXISTS live_path(position_id INTEGER, time_msc INTEGER, profit REAL, swap REAL, bid REAL,
        ask REAL, sl REAL, tp REAL, volume REAL, booked_net REAL, PRIMARY KEY(position_id,time_msc));
      CREATE TABLE IF NOT EXISTS live_initial_risk(position_id INTEGER PRIMARY KEY, risk_usc REAL, opening_sl REAL);
      CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, time REAL, kind TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS availability(id TEXT PRIMARY KEY, time REAL, bot TEXT, strategy TEXT,
        symbol TEXT, status TEXT, detail TEXT, bar INTEGER);
      CREATE TABLE IF NOT EXISTS shadow_variant_rejections(decision_id TEXT,ordinal INTEGER,variant TEXT,
        plan_id TEXT,reason TEXT,PRIMARY KEY(decision_id,ordinal,variant,plan_id));
    ''')
    # Additive migration for a research DB created by an earlier local build.
    if 'booked_net' not in {r[1] for r in db.execute('PRAGMA table_info(live_path)')}:
        db.execute('ALTER TABLE live_path ADD COLUMN booked_net REAL DEFAULT 0')
    shadow_columns={r[1] for r in db.execute('PRAGMA table_info(shadows)')}
    for name,definition in {
            'plan_id':"TEXT NOT NULL DEFAULT 'targets-v1'",'initial_sl':'REAL',
            'policy_state':"TEXT NOT NULL DEFAULT '{}'",'sl_effective_epoch':'REAL'}.items():
        if name not in shadow_columns:
            db.execute('ALTER TABLE shadows ADD COLUMN '+name+' '+definition)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def source_snapshot():
    global _sources
    if _sources is not None:
        return _sources
    root = Path(__file__).resolve().parents[2]
    manifest = {}; provenance = {}
    for repo in REPOS:
        folder = root / repo
        def git(*args):
            return subprocess.check_output(['git', '-C', str(folder), *args], stderr=subprocess.DEVNULL).decode('utf-8').strip()
        provenance[repo] = {'head': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}
        # Store hashes, never contents of .env, private settings, journals or credentials.
        paths = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
        for name in sorted(set(paths)):
            path = folder / name
            if path.is_file() and path.suffix in {'.py', '.json', '.yaml', '.yml', '.toml'} and not any(
                    part.startswith('.') or part.lower() in {'secrets', 'credentials'} for part in Path(name).parts):
                manifest[repo + '/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    _sources = {'manifest': manifest, 'git': provenance,
                'loaded_process': 'Source snapshot at first research evaluation; restart required after code edits.'}
    return _sources


def version(db, cfg, artifact_hash=None):
    sources = source_snapshot()
    # Explicit allowlist keeps account identity and credentials outside research config snapshots.
    keys = ('risk_budget_usc', 'bot_loss_budget_usc', 'strategy_loss_budget_usc', 'total_risk_budget_usc',
            'account_loss_fraction', 'initial_equity_usc', 'start_epoch', 'max_volume', 'max_volume_by_symbol',
            'max_quote_age_seconds', 'position_policy', 'enabled_bots', 'enabled_strategies', 'symbols', 'live')
    safe = {k: cfg[k] for k in keys if k in cfg}
    safe['artifact_manifest_sha256'] = artifact_hash
    safe['shadow_plan'] = PLAN_ID
    env = {'python': sys.version}
    for package in ('numpy', 'pandas', 'TA-Lib', 'MetaTrader5', 'backtrader'):
        try:
            env[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    identity = digest({'sources': sources['manifest'], 'config': safe, 'environment': env})
    db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)',
               (identity, time.time(), encode(sources), encode(safe), encode(env)))
    return identity


def features(rates):
    import pandas as pd
    import talib
    f = pd.DataFrame(rates)
    out = {'last_closed_bar': plain(f.iloc[-1].to_dict()), 'bars': len(f)}
    for n in (20, 50, 100, 200):
        out['sma_' + str(n)] = float(f.close.rolling(n).mean().iloc[-1])
    for n in (14, 48):
        out['atr_talib_' + str(n)] = float(talib.ATR(f.high, f.low, f.close, timeperiod=n).iloc[-1])
    for n in (2, 14):
        out['rsi_' + str(n)] = float(talib.RSI(f.close, timeperiod=n).iloc[-1])
    out['utc_hour'] = datetime.fromtimestamp(int(f.time.iloc[-1]), timezone.utc).hour
    return plain(out)


def observe(gateway, **values):
    """Optional feature observation; never changes strategy behavior on a log failure."""
    try:
        callback = getattr(gateway, 'record_features', None)
        if callback:
            callback(**values)
    except Exception as exc:
        research_error('features', exc)


def availability(bot, strategy, symbol, status, detail, bar):
    if status in {'ready', 'monitoring', 'evaluated'}:
        return  # Full evaluated decisions are recorded separately.
    stamp=time.time()
    with connect() as db:
        db.execute('INSERT OR IGNORE INTO availability VALUES(?,?,?,?,?,?,?,?)',
                   (digest([bot,strategy,symbol,status,str(detail),int(stamp)//900]),stamp,
                    bot,strategy,symbol,status,str(detail),bar))


def research_error(where, exc):
    # Logging failures must never resubmit a live order or modify trading halts.
    print(f'RESEARCH ERROR [{where}]: {type(exc).__name__}: {exc}', flush=True)
    try:
        with connect() as db:
            db.execute('INSERT INTO observations(time,kind,detail) VALUES(?,?,?)',
                       (time.time(), 'research_error', encode({'where': where, 'error': str(exc)})))
    except Exception:
        pass


class Recorder:
    def __init__(self, gateway, bot, strategy, symbol, rates, mode):
        self.gateway = gateway
        self.cfg = settings()
        self.bot, self.strategy, self.symbol, self.mode = bot, strategy, symbol, mode
        self.attempts = []; self.features = features(rates)
        self.bar = int(rates[-1]['time'])
        self.context = {'quote': plain(gateway.symbol_info_tick(symbol)), 'contract': plain(gateway.symbol_info(symbol)),
                        'input_kind': 'exact closed native M15 window', 'research_schema': 1}
        journal = STATE / 'trial.sqlite3'
        if journal.exists():
            with closing(sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True)) as live:
                self.context['live_flags'] = dict(live.execute('SELECT name,value FROM flags'))
        payload = encode({'columns': list(rates.dtype.names), 'rows': rates.tolist()}).encode()
        input_id = hashlib.sha256(payload).hexdigest()
        with connect() as db:
            artifact_hash = None
            if bot == 'tradingbot':
                artifact = STATE/'artifacts'/'aurum_ema'/symbol if strategy == 'ema_cross' else STATE/'artifacts'/'aurum_rules'/strategy/symbol
                manifest = artifact/'manifest.json'
                if manifest.exists():
                    artifact_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
            self.version = version(db, self.cfg, artifact_hash)
            self.id = digest([mode, bot, strategy, symbol, self.bar, self.version])
            db.execute('INSERT OR IGNORE INTO inputs VALUES(?,?,?)', (input_id, zlib.compress(payload), len(payload)))
            self.fresh = db.execute('INSERT OR IGNORE INTO research_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (self.id, time.time(), mode, bot, strategy, symbol, self.bar, self.version, input_id,
                        encode(self.context), encode(self.features), 'started', '')).rowcount == 1

    def finish(self, detail, error=None):
        # Broker reads can be slow or serialized by the terminal. Complete them
        # before acquiring the journal writer lock; persist one decision atomically.
        prepared=[]
        for attempt in self.attempts:
            try:
                prepared.append(prepare_shadows(self,attempt))
            except (ValueError,TypeError) as exc:
                prepared.append({'ready':[],'rejected':[],'reason':str(exc)})
        with connect() as db:
            db.execute('UPDATE research_decisions SET features=?,status=?,detail=? WHERE id=?',
                       (encode(self.features), 'error' if error else 'evaluated', str(detail), self.id))
            for ordinal, attempt in enumerate(self.attempts):
                result = attempt.get('result') or {}
                db.execute('INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?,?,?)',
                           (self.id, ordinal, encode(attempt['request']), encode(attempt['quote']), encode(result),
                            attempt.get('error'), result.get('order', 0), result.get('deal', 0)))
                open_shadows(db,self,ordinal,attempt,prepared[ordinal])


class ObservedGateway:
    """One evaluation, one unchanged real call per proposal; paper mode never delegates orders."""
    def __init__(self, gateway, recorder, paper=False):
        self.gateway, self.recorder, self.paper = gateway, recorder, paper

    def record_features(self, **values):
        self.recorder.features.update(plain(values))

    def order_send(self, request):
        # A failed observation cannot cause a second broker submission.
        try:
            quote = plain(self.gateway.symbol_info_tick(request.get('symbol', self.recorder.symbol)))
        except Exception:
            quote = None
        attempt = {'request': plain(dict(request)), 'quote': quote, 'observed': time.time()}
        self.recorder.attempts.append(attempt)
        try:
            if self.paper:
                from types import SimpleNamespace
                result = SimpleNamespace(retcode=10006, comment='research-only; no live order submitted', order=0, deal=0)
            else:
                result = self.gateway.order_send(request)
            try:
                attempt['result'] = plain(result._asdict() if hasattr(result, '_asdict') else vars(result))
                if isinstance(attempt['result'], dict):
                    attempt['result'].setdefault('order', getattr(result,'order',0))
                    attempt['result'].setdefault('deal', getattr(result,'deal',0))
            except Exception as exc:
                attempt['result'] = {'observation_error': str(exc)}
            return result
        except Exception as exc:
            attempt['error'] = str(exc)
            raise

    def __getattr__(self, name):
        if self.paper and not (name.isupper() or name in {
                'symbol_info','symbol_info_tick','positions_get','orders_get','history_deals_get',
                'copy_rates_from_pos','copy_rates_range','account_info','order_calc_profit','order_calc_margin',
                'bot','strategy','magic','key'}):
            raise AttributeError('Paper research gateway does not expose: ' + name)
        return getattr(self.gateway, name)


def evaluate(cycle, gateway, bot, strategy, symbol, rates, paper=False):
    recorder = None
    try:
        recorder = Recorder(gateway, bot, strategy, symbol, rates, 'paper_seed' if paper else 'live_evaluation')
    except Exception as exc:
        research_error('begin decision', exc)
    if paper and recorder is None:
        raise RuntimeError('Paper research initialization failed; no evaluation or orders performed')
    if recorder is not None and not recorder.fresh:
        if paper:
            return 'Research decision already recorded; paper seed not repeated'
        recorder = None
    observed = ObservedGateway(gateway, recorder, paper) if recorder else gateway
    try:
        detail = cycle(observed, symbol, rates)
    except Exception as exc:
        if recorder:
            try:
                recorder.finish(str(exc), error=True)
            except Exception as log_error:
                research_error('record failed evaluation', log_error)
        raise
    if recorder:
        try:
            recorder.finish(detail)
        except Exception as exc:
            research_error('finish decision', exc)
    return detail


def prepare_shadows(rec, attempt):
    from .shadow_plan import candidates, size
    r,q,c,g=attempt['request'],attempt['quote'],rec.cfg,rec.gateway
    out={'ready':[],'rejected':[],'reason':None}
    if r.get('position') or r.get('action')!=1:
        return out
    if not q or time.time()-q.get('time',0)>c['max_quote_age_seconds']:
        raise ValueError('Missing/stale entry quote')
    if not all(isinstance(q.get(k),(int,float)) and math.isfinite(q[k]) and q[k]>0 for k in ('bid','ask')) or q['ask']<q['bid']:
        raise ValueError('Invalid entry quote')
    symbol=r.get('symbol',rec.symbol)
    out['symbol']=symbol
    contract=g.symbol_info(symbol)
    for candidate in candidates(r,q):
        try:
            out['ready'].append(size(candidate,g,c,symbol,contract,r['volume'],q))
        except (ValueError,TypeError) as exc:
            out['rejected'].append((candidate['variant'],str(exc)))
    if not out['ready']:
        out['reason']=out['rejected'][0][1] if out['rejected'] else 'No supported paper candidates'
    return out


def open_shadows(db, rec, ordinal, attempt, prepared):
    for variant,reason in prepared['rejected']:
        db.execute('INSERT OR IGNORE INTO shadow_variant_rejections VALUES(?,?,?,?,?)',
                   (rec.id,ordinal,variant,PLAN_ID,reason))
    if prepared.get('reason'):
        db.execute('INSERT OR IGNORE INTO shadow_rejections VALUES(?,?,?)',(rec.id,ordinal,prepared['reason']))
    for item in prepared['ready']:
        variant=item['variant']
        ident=digest([rec.id,ordinal,PLAN_ID,variant])
        state={'armed':False,'peak':item['entry']}
        created=db.execute("""INSERT OR IGNORE INTO shadows(id,decision_id,ordinal,variant,bot,strategy,symbol,version_id,
          side,volume,entry,sl,tp,initial_risk,opened,last_bar,status,plan_id,initial_sl,policy_state,sl_effective_epoch)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (ident,rec.id,ordinal,variant,rec.bot,rec.strategy,prepared['symbol'],rec.version,item['side'],item['volume'],
           item['entry'],item['sl'],item['tp'],item['initial_risk'],attempt['observed'],rec.bar,'open',
           PLAN_ID,item['initial_sl'],encode(state),attempt['observed']))
        if created.rowcount:
            db.execute('INSERT INTO shadow_events(shadow_id,time,kind,detail) VALUES(?,?,?,?)',
                       (ident,attempt['observed'],'opened',encode({**item,'plan_id':PLAN_ID,'paper_only':True})))


def bar_exit(side, opening, high, low, sl, tp):
    """Conservative OHLC handling: stop wins if both touched; gaps fill at open."""
    if side == 0:
        if opening <= sl: return opening, 'stop_gap'
        if tp and opening >= tp: return tp, 'target_gap'
        if low <= sl: return sl, 'stop'
        if tp and high >= tp: return tp, 'target'
    else:
        if opening >= sl: return opening, 'stop_gap'
        if tp and opening <= tp: return tp, 'target_gap'
        if high >= sl: return sl, 'stop'
        if tp and low <= tp: return tp, 'target'
    return None


def advance(db, native, row, quote, bars, observed=None):
    from .shadow_plan import tighten
    observed=time.time() if observed is None else observed
    r=dict(row)
    if r['status']!='open':
        return
    risk=r['initial_risk'];state=json.loads(r.get('policy_state') or '{}')
    contract=native.symbol_info(r['symbol'])
    original_sl=r.get('initial_sl') or r['sl']
    effective=r.get('sl_effective_epoch') or r['opened']
    def pnl(price):
        result=native.order_calc_profit(r['side'],r['symbol'],r['volume'],r['entry'],float(price))
        if result is None or not math.isfinite(result):
            raise ValueError('Shadow P&L valuation unavailable')
        return result
    def mark(high,low):
        values=[pnl(high)/risk,pnl(low)/risk]
        r['mfe_r']=max(r['mfe_r'],*values)
        r['mae_r']=min(r['mae_r'],*values)
    def apply_stop(high,low,stamp):
        nonlocal effective
        new=tighten(r,state,high,low,stamp,contract)
        if new is not None:
            old=r['sl'];r['sl']=new;effective=max(effective,stamp)
            db.execute('INSERT INTO shadow_events(shadow_id,time,kind,detail) VALUES(?,?,?,?)',
                       (r['id'],stamp,'virtual_stop_tightened',encode({'from':old,'to':new,'plan_id':r.get('plan_id'),
                         'effective_after':effective,'observed_mfe_r':r['mfe_r'],'paper_only':True})))
    outcome=None;closed=None
    for b in bars:
        bt=int(b['time'])
        if bt<=r['last_bar'] or bt<r['opened'] or bt+900>observed:
            continue
        spread=float(b['spread'])*contract.point if r['side']==1 else 0.
        o,h,l,cl=(float(b[k])+spread for k in ('open','high','low','close'))
        # A stop armed within this bar cannot be checked against earlier extrema.
        usable_sl=r['sl'] if bt>=effective else original_sl
        outcome=bar_exit(r['side'],o,h,l,usable_sl,r['tp'])
        r['last_bar']=bt
        if outcome:
            mark(o,outcome[0]);closed=bt+900
            break
        mark(h,l)
        apply_stop(h,l,bt+900)
        # If retracement already happened before we learned the completed bar's
        # peak, exit at the observed CLOSE, never at an invented earlier fill.
        if r.get('plan_id')==PLAN_ID and r['variant'] in MFE_VARIANTS:
            retraced=(cl<=r['sl']) if r['side']==0 else (cl>=r['sl'])
            if state.get('armed') and retraced:
                outcome=(cl,'mfe_retrace_observed_close');closed=bt+900
                break
        if bt+900-r['opened']>=96*900:
            outcome=(cl,'timeout_96_bars');closed=bt+900
            break
    if outcome is None and quote and observed>=r['opened'] and observed-quote.time<=30:
        quote_msc=getattr(quote,'time_msc',quote.time*1000)
        quote_time=quote_msc/1000
        if quote_time>=max(r['opened'],effective,r['last_bar']+900) and quote_msc>state.get('last_quote_msc',0):
            price=quote.bid if r['side']==0 else quote.ask
            if price>0 and math.isfinite(price):
                mark(price,price)
                outcome=bar_exit(r['side'],price,price,price,r['sl'],r['tp'])
                if outcome is None:
                    apply_stop(price,price,quote_time)
                state['last_quote_msc']=quote_msc
                closed=quote_time
    if outcome:
        gross=pnl(outcome[0])
        changed=db.execute("UPDATE shadows SET status='closed',closed=?,exit_price=?,exit_reason=?,gross_usc=?,r_multiple=? WHERE id=? AND status='open'",
                   (closed,outcome[0],outcome[1],gross,gross/risk,r['id']))
        if changed.rowcount:
            db.execute('INSERT INTO shadow_events(shadow_id,time,kind,detail) VALUES(?,?,?,?)',
                       (r['id'],observed,'closed',encode({'price':outcome[0],'reason':outcome[1],'gross_usc':gross,
                        'cost_model':'spread included; fees/swap/slippage not modeled'})))
    db.execute('UPDATE shadows SET last_bar=?,mfe_r=?,mae_r=?,sl=?,policy_state=?,sl_effective_epoch=? WHERE id=?',
               (r['last_bar'],r['mfe_r'],r['mae_r'],r['sl'],encode(state),effective,r['id']))


def collect(native, positions, deals):
    observed = time.time()
    with connect() as db:
        owners = {d.position_id:resolve(d.magic,d.symbol) for d in deals if d.entry == 0 and resolve(d.magic,d.symbol)}
        for d in deals:
            if d.position_id not in owners: continue
            db.execute('INSERT OR IGNORE INTO live_deals VALUES(?,?,?)', (d.ticket,d.position_id,encode(d)))
            if d.entry == 0:
                context = db.execute('''SELECT a.decision_id,r.version_id,a.request,a.result FROM attempts a JOIN research_decisions r
                  ON r.id=a.decision_id WHERE r.mode='live_evaluation' AND (a.result_deal=? OR a.result_order=?) LIMIT 1''',
                                     (d.ticket,d.order)).fetchone()
                db.execute('''INSERT INTO live_context VALUES(?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET
                  decision_id=excluded.decision_id,version_id=excluded.version_id,attribution=excluded.attribution
                  WHERE live_context.decision_id IS NULL AND excluded.decision_id IS NOT NULL''',
                           (d.position_id,context[0] if context else None,context[1] if context else None,
                            'opening deal/order match' if context else 'unknown: opened before research or no recorded match'))
                if context and all(hasattr(d,k) for k in ('type','volume','price')):
                    result=json.loads(context[3]); proposal=json.loads(context[2])
                    prepared=result.get('request') or proposal
                    sl=prepared.get('sl') if isinstance(prepared,dict) else proposal.get('sl')
                    if sl and sl>0:
                        pnl=native.order_calc_profit(d.type,d.symbol,d.volume,d.price,sl)
                        if pnl is not None and math.isfinite(pnl) and pnl<0:
                            db.execute('INSERT OR IGNORE INTO live_initial_risk VALUES(?,?,?)',(d.position_id,-pnl,sl))
        for p in positions:
            if not resolve(p.magic,p.symbol): continue
            tick = native.symbol_info_tick(p.symbol)
            booked=sum(getattr(d,'profit',0)+getattr(d,'swap',0)+getattr(d,'commission',0)+getattr(d,'fee',0)
                       for d in deals if d.position_id==p.identifier)
            db.execute('INSERT OR IGNORE INTO live_path VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (p.identifier,int(observed*1000),p.profit,p.swap,tick.bid if tick else None,
                        tick.ask if tick else None,p.sl,p.tp,p.volume,booked))
        rows = db.execute("SELECT * FROM shadows WHERE status='open'").fetchall()
        # Do not hold the live-path write lock while fetching market data and
        # valuing thousands of independent counterfactuals. Each shadow's exit,
        # stop state and events remain one atomic durable transaction.
        db.commit()
        market = {}
        for r in rows:
            symbol = r['symbol']
            if symbol not in market:
                oldest = min(x['opened'] for x in rows if x['symbol']==symbol)
                count = min(10000, max(3,math.ceil((observed-oldest)/900)+2))
                bars = native.copy_rates_from_pos(symbol,native.TIMEFRAME_M15,1,count)
                market[symbol] = (native.symbol_info_tick(symbol), bars if bars is not None else [])
            try:
                db.execute('SAVEPOINT shadow_mark')
                advance(db,native,r,*market[symbol],observed)
                db.execute('RELEASE shadow_mark')
            except Exception as exc:
                db.execute('ROLLBACK TO shadow_mark')
                db.execute('RELEASE shadow_mark')
                db.execute('INSERT INTO observations(time,kind,detail) VALUES(?,?,?)',
                           (observed,'shadow_mark_error',encode({'shadow':r['id'],'error':str(exc)})))
            db.commit()
    report = summary()
    temp = STATE / 'research-summary.next.json'
    temp.write_text(json.dumps(report,indent=2),encoding='utf-8')
    temp.replace(STATE / 'research-summary.json')
    return report


def summary():
    with connect() as db:
        counts = {table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
                  ('versions','inputs','research_decisions','attempts','shadow_rejections','shadow_variant_rejections','live_path','availability')}
        groups = [dict(r) for r in db.execute('''SELECT s.bot,s.strategy,s.version_id,r.mode,s.plan_id,s.variant,s.status,COUNT(*) AS count,
          SUM(s.gross_usc) AS gross_usc,AVG(s.r_multiple) AS mean_r,AVG(s.mfe_r) AS mean_mfe_r,AVG(s.mae_r) AS mean_mae_r FROM shadows s JOIN research_decisions r
          ON r.id=s.decision_id GROUP BY s.bot,s.strategy,s.version_id,r.mode,s.plan_id,s.variant,s.status''')]
        comparisons = [dict(r) for r in db.execute("""SELECT a.bot,a.strategy,a.version_id,d.mode,a.plan_id,a.variant,
          COUNT(*) AS paired_total,
          SUM(b.id IS NOT NULL) AS baseline_present,
          SUM(b.id IS NULL) AS baseline_missing,
          SUM(CASE WHEN a.status='closed' AND b.status='closed' THEN 1 ELSE 0 END) AS paired_closed,
          SUM(b.id IS NOT NULL AND (a.status<>'closed' OR b.status<>'closed')) AS pending_pairs,
          AVG(CASE WHEN a.status='closed' AND b.status='closed' THEN b.r_multiple END) AS baseline_mean_r,
          AVG(CASE WHEN a.status='closed' AND b.status='closed' THEN a.r_multiple END) AS variant_mean_r,
          AVG(CASE WHEN a.status='closed' AND b.status='closed' THEN a.r_multiple-b.r_multiple END) AS delta_mean_r,
          SUM(CASE WHEN a.status='closed' AND b.status='closed' THEN b.gross_usc END) AS baseline_gross_usc,
          SUM(CASE WHEN a.status='closed' AND b.status='closed' THEN a.gross_usc END) AS variant_gross_usc
          FROM shadows a JOIN research_decisions d ON d.id=a.decision_id
          LEFT JOIN shadows b ON b.decision_id=a.decision_id AND b.ordinal=a.ordinal AND b.variant='proposed'
            AND b.plan_id=a.plan_id AND b.version_id=a.version_id
          WHERE a.variant<>'proposed' GROUP BY a.bot,a.strategy,a.version_id,d.mode,a.plan_id,a.variant""")]
        rejections = [dict(r) for r in db.execute("""SELECT d.bot,d.strategy,d.version_id,d.mode,v.plan_id,v.variant,
          v.reason,COUNT(*) AS count FROM shadow_variant_rejections v JOIN research_decisions d ON d.id=v.decision_id
          GROUP BY d.bot,d.strategy,d.version_id,d.mode,v.plan_id,v.variant,v.reason""")]
        errors = [dict(r) for r in db.execute('SELECT * FROM observations ORDER BY id DESC LIMIT 5')]
        modes = {r[0]:r[1] for r in db.execute('SELECT mode,COUNT(*) FROM research_decisions GROUP BY mode')}
        incomplete = db.execute("SELECT COUNT(*) FROM research_decisions WHERE status<>'evaluated'").fetchone()[0]
        live_bounds = [dict(r) for r in db.execute('''SELECT p.position_id,COUNT(*) AS samples,MIN(p.time_msc) AS first_sample,
          MAX(p.time_msc) AS last_sample,MAX(0,MAX(p.profit+p.swap+p.booked_net)) AS observed_mfe_usc,
          MIN(0,MIN(p.profit+p.swap+p.booked_net)) AS observed_mae_usc,MAX(i.risk_usc) AS initial_risk_usc,
          CASE WHEN MAX(i.risk_usc)>0 THEN MAX(0,MAX(p.profit+p.swap+p.booked_net))/MAX(i.risk_usc) END AS observed_mfe_r,
          CASE WHEN MAX(i.risk_usc)>0 THEN MIN(0,MIN(p.profit+p.swap+p.booked_net))/MAX(i.risk_usc) END AS observed_mae_r
          FROM live_path p LEFT JOIN live_initial_risk i ON i.position_id=p.position_id GROUP BY p.position_id''')]
    return {'updated_utc':datetime.now(timezone.utc).isoformat(),'counts':counts,'decision_modes':modes,
            'incomplete_decisions':incomplete,'live_observed_excursions':live_bounds,'shadows':groups,'observations':errors,
            'comparisons':comparisons,'variant_rejections':rejections,'plan_id':PLAN_ID,'experiment_plan':PLAN,
            'paper_only':True,'variants':VARIANTS,'max_holding_bars':96,
            'limitations':'Independent per-signal counterfactuals, not a live portfolio. Live halt/loss/addition gates are not simulated. '
            'Spread included; commissions, swap and extra slippage excluded. OHLC stop-first ambiguity, sampled ticks, '
            'short-side historical spread approximation and missing partial-entry-bar extremes limit precision. '
            'MFE/MAE are observed bounds. Legacy trades have unknown versions; no historical outcomes fabricated.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','export'])
    args=parser.parse_args()
    if args.action=='status':
        print(json.dumps(summary(),indent=2)); return
    folder=STATE/'research-export';folder.mkdir(exist_ok=True)
    with connect() as db:
        for table in ('versions','research_decisions','attempts','shadows','shadow_events','shadow_rejections','shadow_variant_rejections','live_deals','live_context','live_initial_risk','live_path','availability','observations'):
            cursor=db.execute('SELECT * FROM '+table)
            with (folder/(table+'.csv')).open('w',newline='',encoding='utf-8') as f:
                writer=csv.writer(f);writer.writerow([c[0] for c in cursor.description]);writer.writerows(cursor)
        with (folder/'inputs.jsonl').open('w',encoding='utf-8') as f:
            for r in db.execute('SELECT * FROM inputs'):
                f.write(encode({'id':r['id'],'input':json.loads(zlib.decompress(r['payload']))})+'\n')
    print('Exported local research CSVs and exact input windows to '+str(folder))


if __name__=='__main__':
    main()
