"""Web dashboard - the URL-accessible face of the desk.

Read-mostly: it renders what the desk has done and lets you kick off a cycle.
All trading logic stays in the desk modules; nothing decides anything here.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..config import ROOT, SETTINGS
from ..execute import ledger

log = logging.getLogger(__name__)

# The desk trades U.S. options, so every timestamp is shown in market time -
# a server running UTC would otherwise report a New York close at 20:00.
MARKET_TZ = ZoneInfo("America/New_York")

# Render's free tier spins an instance down after ~15 minutes without inbound
# traffic, and the next visitor waits ~50s for a cold start. A ping to our own
# LOOPBACK address would not help: the spin-down is driven by requests arriving
# through Render's router, so the request has to leave and come back via the
# public hostname, which Render supplies as RENDER_EXTERNAL_URL.
KEEPWARM_INTERVAL = 600


async def _keep_warm() -> None:
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    target = url.rstrip("/") + "/health"
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            await asyncio.sleep(KEEPWARM_INTERVAL)
            try:
                await client.get(target)
            except Exception as e:  # noqa: BLE001 - never let this kill the app
                log.debug("keep-warm ping failed: %s", e)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_keep_warm())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Capitol Desk", lifespan=lifespan)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

SEED = ROOT / "seed"


def _bootstrap_from_seed() -> None:
    """Populate an empty data dir from the committed seed.

    Hosted instances start with an ephemeral disk, so without this a judge
    opening the URL would see an empty dashboard. Local runs already have a
    real data dir and are left untouched.
    """
    from ..config import DATA

    DATA.mkdir(parents=True, exist_ok=True)
    for name in ("desk.sqlite3", "backtest.json"):
        src, dst = SEED / name, DATA / name
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())
            log.info("seeded %s from %s", dst, src)


_bootstrap_from_seed()

# Single-flight guard: a cycle is expensive and must not overlap itself.
_job = {"running": False, "started": None, "last": None, "error": None}
_lock = threading.Lock()


def _broker():
    from ..execute.broker import Broker

    return Broker()


def _run_cycle(live: bool, days: int) -> None:
    from ..desk.run import Desk

    with _lock:
        if _job["running"]:
            return
        _job.update(running=True, started=dt.datetime.now(), error=None)
    try:
        Desk(live=live).cycle(max_age_days=days)
        _job["last"] = dt.datetime.now()
    except Exception as e:  # noqa: BLE001
        log.exception("cycle failed")
        _job["error"] = str(e)
    finally:
        _job["running"] = False


# Mechanical passes are a wall of near-identical rows; judgment passes are the
# interesting output. Split them so the second kind is not buried by the first.
_PASS_KINDS = [
    ("judgment", "Declined on the merits", ("strategist skipped",)),
    ("budget", "Over the risk budget", ("too expensive for the risk budget",)),
    ("gate", "Blocked by the risk gate", ("risk gate:",)),
    ("liquidity", "No tradable contract", ("no liquid contracts", "no live quote")),
    ("sale", "Disclosed sale, not a purchase", ("disclosed sale",)),
    ("unmapped", "Asset could not be mapped to a ticker", ("no ticker disclosed",)),
    ("untradable", "Not an equity option underlying", ("is not tradable as equity options",
                                                      "no clean directional read")),
]


def _group_rejections(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (judgment calls in full, everything else grouped with counts)."""
    buckets: dict[str, list[dict]] = {k: [] for k, _, _ in _PASS_KINDS}
    buckets["other"] = []
    for r in rows:
        reason = (r.get("reason") or "").lower()
        for key, _, needles in _PASS_KINDS:
            if any(n in reason for n in needles):
                buckets[key].append(r)
                break
        else:
            buckets["other"].append(r)

    judgment = buckets.pop("judgment")
    grouped = []
    for key, label, _ in _PASS_KINDS:
        if key == "judgment" or not buckets.get(key):
            continue
        rows_ = buckets[key]
        grouped.append({
            "label": label,
            "count": len(rows_),
            "tickers": sorted({r["ticker"] for r in rows_ if r.get("ticker")})[:12],
            "example": rows_[0]["reason"],
        })
    if buckets["other"]:
        grouped.append({
            "label": "Other", "count": len(buckets["other"]),
            "tickers": sorted({r["ticker"] for r in buckets["other"] if r.get("ticker")})[:12],
            "example": buckets["other"][0]["reason"],
        })
    grouped.sort(key=lambda g: g["count"], reverse=True)
    return judgment, grouped


def _snapshot() -> dict:
    """Everything the dashboard renders, in one shot."""
    st = ledger.stats()
    account, positions, err = None, [], None
    try:
        b = _broker()
        try:
            from ..execute import reconcile

            reconcile.sync(b)
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile failed: %s", e)
        a = b.account()
        account = {
            "number": a.account_number,
            "equity": float(a.equity),
            "cash": float(a.cash),
            "buying_power": float(a.options_buying_power or a.buying_power or 0),
            "pl_today": float(a.equity) - float(a.last_equity or a.equity),
        }
        for p in b.option_positions():
            positions.append({
                "symbol": p.symbol,
                "qty": int(p.qty),
                "avg": float(p.avg_entry_price),
                "current": float(p.current_price or 0),
                "value": float(p.market_value or 0),
                "pl": float(p.unrealized_pl or 0),
                "plpc": float(p.unrealized_plpc or 0) * 100,
            })
    except Exception as e:  # noqa: BLE001 - dashboard must render without a broker
        err = str(e)
        log.warning("broker unavailable: %s", e)

    from ..research.backtest import load as load_backtest

    trades = ledger.recent_trades(30)
    judgment, grouped = _group_rejections(ledger.recent_rejections(200))
    return {
        "backtest": load_backtest(),
        "judgment_passes": judgment[:10],
        "grouped_passes": grouped,
        "stats": st,
        "account": account,
        "positions": sorted(positions, key=lambda p: p["pl"]),
        "trades": trades,
        "rejections": ledger.recent_rejections(30),
        "filings": ledger.seen_filings(25),
        "paper": SETTINGS.paper,
        "read_only": SETTINGS.read_only,
        "job": {
            "running": _job["running"],
            "started": _job["started"].isoformat() if _job["started"] else None,
            "last": _job["last"].isoformat() if _job["last"] else None,
            "error": _job["error"],
        },
        "broker_error": err,
        "now": dt.datetime.now(MARKET_TZ),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html", _snapshot())


@app.get("/api/state")
def state():
    s = _snapshot()
    s["now"] = s["now"].isoformat()
    return JSONResponse(s)


@app.post("/scan")
def scan(background: BackgroundTasks, live: bool = False, days: int = 30):
    if SETTINGS.read_only:
        # The hosted demo carries broker credentials only. Scanning would spend
        # tokens and place orders, so anyone with the URL must not be able to.
        return JSONResponse(
            {"status": "disabled",
             "detail": "This instance is read-only. Run `desk run` locally to scan."},
            status_code=403,
        )
    if _job["running"]:
        return JSONResponse({"status": "already running"}, status_code=409)
    background.add_task(_run_cycle, live, days)
    return JSONResponse({"status": "started", "live": live, "days": days})


@app.get("/health")
def health():
    return {"ok": True, "paper": SETTINGS.paper, "read_only": SETTINGS.read_only}
