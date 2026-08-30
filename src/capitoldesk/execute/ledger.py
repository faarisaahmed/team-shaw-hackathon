"""Append-only journal of everything the desk sees, decides, and does.

Also the dedup key: a filing is mirrored at most once, so restarting the loop
(or a filing reappearing in the daily-republished bulk index) never double-trades.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from ..config import DATA
from ..strategy.plan import Plan

DB = DATA / "desk.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    doc_id TEXT PRIMARY KEY,
    member TEXT,
    filing_date TEXT,
    n_txns INTEGER,
    seen_at TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    contract_symbol TEXT NOT NULL,
    member TEXT,
    mode TEXT,
    contracts INTEGER,
    limit_price REAL,
    notional REAL,
    conviction REAL,
    rationale TEXT,
    signal_decayed INTEGER,
    order_id TEXT,
    status TEXT,
    placed_at TEXT,
    plan_json TEXT,
    UNIQUE (doc_id, contract_symbol)
);
CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT, ticker TEXT, reason TEXT, at TEXT
);
"""


# Columns added after the first version shipped. sqlite has no "ADD COLUMN IF
# NOT EXISTS", so we check the table description and add what is missing.
_MIGRATIONS = [
    ("trades", "filled_qty", "INTEGER"),
    ("trades", "filled_price", "REAL"),
    ("trades", "filled_at", "TEXT"),
    ("trades", "last_synced", "TEXT"),
]


def _conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for table, col, typ in _MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return c


def mark_seen(doc_id: str, member: str, filing_date: dt.date, n_txns: int) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?)",
            (doc_id, member, filing_date.isoformat(), n_txns, dt.datetime.now().isoformat()),
        )


def already_seen(doc_id: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM seen WHERE doc_id=?", (doc_id,)).fetchone() is not None


def already_traded(doc_id: str, contract_symbol: str) -> bool:
    with _conn() as c:
        return (
            c.execute(
                "SELECT 1 FROM trades WHERE doc_id=? AND contract_symbol=?",
                (doc_id, contract_symbol),
            ).fetchone()
            is not None
        )


def record_trade(plan: Plan, order_id: str | None, status: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO trades
               (doc_id,ticker,contract_symbol,member,mode,contracts,limit_price,notional,
                conviction,rationale,signal_decayed,order_id,status,placed_at,plan_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                plan.doc_id, plan.ticker, plan.contract_symbol, plan.member, plan.mode.value,
                plan.contracts, plan.limit_price, plan.est_notional, plan.conviction,
                plan.rationale, int(plan.signal_decayed), order_id, status,
                dt.datetime.now().isoformat(), plan.model_dump_json(),
            ),
        )


def record_rejection(doc_id: str, ticker: str | None, reason: str) -> None:
    from ..strategy.engine import clean_prose

    reason = clean_prose(reason)
    with _conn() as c:
        c.execute(
            "INSERT INTO rejections (doc_id,ticker,reason,at) VALUES (?,?,?,?)",
            (doc_id, ticker, reason, dt.datetime.now().isoformat()),
        )


def notional_today() -> float:
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(notional),0) n FROM trades WHERE placed_at LIKE ? AND status='placed'",
            (f"{dt.date.today().isoformat()}%",),
        ).fetchone()
        return float(row["n"])


def open_trade_count() -> int:
    with _conn() as c:
        return int(
            c.execute("SELECT COUNT(*) n FROM trades WHERE status='placed'").fetchone()["n"]
        )


def recent_trades(limit: int = 50) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        )]


def recent_rejections(limit: int = 50) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM rejections ORDER BY id DESC LIMIT ?", (limit,)
        )]


def open_trades_for_ticker(ticker: str) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE ticker=? AND status='placed'", (ticker,)
        )]


def mark_closed(trade_id: int, note: str) -> None:
    with _conn() as c:
        c.execute("UPDATE trades SET status=? WHERE id=?", (f"closed: {note}"[:60], trade_id))


def stats() -> dict:
    """Headline counters for the dashboard."""
    with _conn() as c:
        seen = c.execute("SELECT COUNT(*) n, COALESCE(SUM(n_txns),0) t FROM seen").fetchone()
        placed = c.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(notional),0) v FROM trades WHERE status='placed'"
        ).fetchone()
        rej = c.execute("SELECT COUNT(*) n FROM rejections").fetchone()
        members = c.execute("SELECT COUNT(DISTINCT member) n FROM seen").fetchone()
    return {
        "filings": seen["n"],
        "transactions": seen["t"],
        "members": members["n"],
        "trades": placed["n"],
        "deployed": float(placed["v"]),
        "passed": rej["n"],
    }


def seen_filings(limit: int = 40) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM seen ORDER BY filing_date DESC, seen_at DESC LIMIT ?", (limit,)
        )]


def update_order_state(
    trade_id: int,
    status: str,
    *,
    filled_qty: int | None = None,
    filled_price: float | None = None,
    filled_at: str | None = None,
) -> None:
    import datetime as _dt

    with _conn() as c:
        c.execute(
            """UPDATE trades
               SET status=?, filled_qty=COALESCE(?, filled_qty),
                   filled_price=COALESCE(?, filled_price),
                   filled_at=COALESCE(?, filled_at), last_synced=?
               WHERE id=?""",
            (status, filled_qty, filled_price, filled_at,
             _dt.datetime.now().isoformat(), trade_id),
        )


def live_orders() -> list[dict]:
    """Trades whose broker state may still change."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE order_id IS NOT NULL "
            "AND status IN ('placed','accepted','new','partially_filled')"
        )]
