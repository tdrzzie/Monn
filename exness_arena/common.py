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


def event(db, bot, kind, detail):
    db.execute("INSERT INTO events(time,bot,kind,detail) VALUES(?,?,?,?)", (now(), bot, kind, json.dumps(detail, default=str)))


def health(bot, status, detail=""):
    with locked() as db:
        db.execute("INSERT OR REPLACE INTO health VALUES(?,?,?,?)", (bot, now(), status, str(detail)))


def claim_bar(bot, symbol, bar):
    with locked() as db:
        return db.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?)", (bot, symbol, int(bar))).rowcount == 1
