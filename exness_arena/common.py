from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REGISTRY = json.loads((PACKAGE / "registry.json").read_text())
STATE = Path(os.environ.get("EXNESS_ARENA_STATE", PACKAGE.parent / ".exness-arena"))


def now():
    return datetime.now(timezone.utc).isoformat()


def settings():
    return json.loads((STATE / "settings.json").read_text())


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE / "trial.sqlite3", timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, time TEXT, bot TEXT, kind TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS decisions(bot TEXT, symbol TEXT, bar INTEGER, PRIMARY KEY(bot,symbol,bar));
      CREATE TABLE IF NOT EXISTS health(bot TEXT PRIMARY KEY, time TEXT, status TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS owners(position_id INTEGER PRIMARY KEY, bot TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS deals(ticket INTEGER PRIMARY KEY, position_id INTEGER, bot TEXT, payload TEXT);
      CREATE TABLE IF NOT EXISTS samples(time TEXT, bot TEXT, net REAL, floating REAL, PRIMARY KEY(time,bot));
      CREATE TABLE IF NOT EXISTS flags(name TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS coverage(bot TEXT, symbol TEXT, time TEXT, status TEXT, detail TEXT, bar INTEGER, PRIMARY KEY(bot,symbol));
      CREATE TABLE IF NOT EXISTS strategy_owners(position_id INTEGER PRIMARY KEY, bot TEXT NOT NULL, strategy TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS strategy_events(event_id INTEGER PRIMARY KEY, bot TEXT, strategy TEXT);
      CREATE TABLE IF NOT EXISTS strategy_samples(time TEXT, bot TEXT, strategy TEXT, net REAL, floating REAL, PRIMARY KEY(time,bot,strategy));
      CREATE TABLE IF NOT EXISTS strategy_decisions(bot TEXT, strategy TEXT, symbol TEXT, bar INTEGER, PRIMARY KEY(bot,strategy,symbol,bar));
      CREATE TABLE IF NOT EXISTS strategy_coverage(bot TEXT, strategy TEXT, symbol TEXT, time TEXT, status TEXT, detail TEXT, bar INTEGER, PRIMARY KEY(bot,strategy,symbol));
    """)
    return db


@contextmanager
def locked():
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def event(db, bot, kind, detail, strategy=None):
    result = db.execute("INSERT INTO events(time,bot,kind,detail) VALUES(?,?,?,?)", (now(), bot, kind, json.dumps(detail, default=str)))
    if strategy:
        db.execute('INSERT INTO strategy_events VALUES(?,?,?)', (result.lastrowid, bot, strategy))


def health(bot, status, detail=""):
    with locked() as db:
        db.execute("INSERT OR REPLACE INTO health VALUES(?,?,?,?)", (bot, now(), status, str(detail)))


def claim_bar(bot, symbol, bar):
    with locked() as db:
        return db.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?)", (bot, symbol, int(bar))).rowcount == 1


def claim_strategy_bar(bot, strategy, symbol, bar):
    from .identity import strategy_for_symbol
    with locked() as db:
        # The current deployed profiles already claimed these bars before migration.
        # Only the original strategy inherits them; another strategy gets its own decision.
        if strategy == strategy_for_symbol(bot, symbol) and db.execute(
                'SELECT 1 FROM decisions WHERE bot=? AND symbol=? AND bar=?', (bot, symbol, int(bar))).fetchone():
            db.execute('INSERT OR IGNORE INTO strategy_decisions VALUES(?,?,?,?)', (bot, strategy, symbol, int(bar)))
            return False
        return db.execute('INSERT OR IGNORE INTO strategy_decisions VALUES(?,?,?,?)', (bot, strategy, symbol, int(bar))).rowcount == 1
