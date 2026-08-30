"""Event study: is there signal in congressional purchases at all?

This deliberately measures the UNDERLYING, not a simulated options book.
Simulating option fills across historical chains means guessing at spreads,
liquidity and assignment on contracts we cannot re-quote - the result would look
precise and be fiction. Measuring the underlying answers the question the desk
actually rests on: after a filing becomes public, does the name outperform?

Two choices that keep it honest:

* The event date is the FILING date, not the transaction date. The transaction
  is private until it is filed - up to 45 days later - so entering on the
  transaction date is lookahead bias, and it is the single easiest way to
  manufacture a fake edge in this dataset.
* Returns are excess over SPY across the identical window, so a rising market
  is not mistaken for skill.
"""
from __future__ import annotations

import datetime as dt
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..config import DATA
from ..extract.models import Disclosure, TxnType

log = logging.getLogger(__name__)

BENCHMARK = "SPY"
# Alpaca rejects the whole request if any symbol is malformed, so screen first.
# Disclosures yield things like "EFC$D" (a preferred-share class) that are not
# plain equity tickers.
VALID_SYMBOL = __import__("re").compile(r"^[A-Z]{1,5}$")
HORIZONS = [5, 21, 63]  # trading days: ~1 week, ~1 month, ~1 quarter


@dataclass
class Event:
    ticker: str
    member: str
    filing_date: dt.date
    txn_date: dt.date
    amount_mid: float
    lag_days: int


@dataclass
class Outcome:
    event: Event
    horizon: int
    stock_return: float
    bench_return: float

    @property
    def excess(self) -> float:
        return self.stock_return - self.bench_return


def load_cached_disclosures() -> list[Disclosure]:
    """Every filing already extracted to data/extracted/."""
    out: list[Disclosure] = []
    root = DATA / "extracted"
    if not root.exists():
        return out
    for p in sorted(root.rglob("*.json")):
        try:
            out.append(Disclosure.model_validate_json(p.read_text()))
        except Exception as e:  # noqa: BLE001
            log.warning("could not load %s: %s", p, e)
    return out


def build_events(discs: list[Disclosure]) -> list[Event]:
    """One event per disclosed purchase of a tickered equity."""
    events: list[Event] = []
    for d in discs:
        for t in d.transactions:
            if not t.ticker or t.txn_type != TxnType.PURCHASE:
                continue
            events.append(
                Event(
                    ticker=t.ticker,
                    member=d.member_name,
                    filing_date=d.filing_date,
                    txn_date=t.txn_date,
                    amount_mid=t.amount_mid,
                    lag_days=(d.filing_date - t.txn_date).days,
                )
            )
    return events


class EventStudy:
    def __init__(self, broker=None) -> None:
        from ..execute.broker import Broker

        self.broker = broker or Broker()
        self._bars: dict[str, list] = {}

    def _load(self, symbols: set[str], start: dt.date, end: dt.date) -> None:
        """Fetch each symbol's daily bars ONCE over the whole study window.

        The naive shape - one request per event - refetches the benchmark for
        every single event, which dominates runtime as the sample grows.
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        todo = sorted(s for s in symbols if s not in self._bars)
        skipped = [s for s in todo if not VALID_SYMBOL.match(s)]
        if skipped:
            log.info("skipping %d non-equity symbols: %s", len(skipped), ", ".join(skipped[:8]))
        todo = [s for s in todo if VALID_SYMBOL.match(s)]

        def fetch(syms: list[str]):
            return self.broker.stocks.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=syms, timeframe=TimeFrame.Day,
                    start=start, end=end, adjustment="all",
                )
            )

        for i in range(0, len(todo), 100):
            chunk = todo[i : i + 100]
            try:
                bars = fetch(chunk)
            except Exception as e:  # noqa: BLE001
                # One rejected symbol fails the whole batch, so fall back to
                # per-symbol requests rather than losing 99 good ones.
                log.warning("batch of %d failed (%s); retrying individually", len(chunk), e)
                for sym in chunk:
                    try:
                        self._bars[sym] = fetch([sym]).data.get(sym) or []
                    except Exception:  # noqa: BLE001
                        self._bars[sym] = []
                continue
            for sym in chunk:
                self._bars[sym] = bars.data.get(sym) or []

    def _window(self, symbol: str, start: dt.date, horizon: int) -> float | None:
        """Return over `horizon` trading days from the first bar on/after start."""
        rows = self._bars.get(symbol) or []
        idx = next((i for i, b in enumerate(rows) if b.timestamp.date() >= start), None)
        if idx is None:
            return None
        return self._forward_return(rows[idx:], horizon)

    @staticmethod
    def _forward_return(rows: list, horizon: int) -> float | None:
        """Return from the first bar on/after the event to `horizon` bars later."""
        if len(rows) < horizon + 1:
            return None
        entry = float(rows[0].close)
        exit_ = float(rows[horizon].close)
        if entry <= 0:
            return None
        return exit_ / entry - 1

    def run(self, events: list[Event], *, today: dt.date | None = None) -> list[Outcome]:
        today = today or dt.date.today()
        if not events:
            return []

        start = min(e.filing_date for e in events)
        symbols = {e.ticker for e in events} | {BENCHMARK}
        self._load(symbols, start, today)

        out: list[Outcome] = []
        for ev in events:
            for h in HORIZONS:
                sr = self._window(ev.ticker, ev.filing_date, h)
                br = self._window(BENCHMARK, ev.filing_date, h)
                if sr is None or br is None:
                    continue
                out.append(Outcome(ev, h, sr, br))
        return out


def summarise(outcomes: list[Outcome]) -> dict:
    by_h: dict[int, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        by_h[o.horizon].append(o)

    rows = []
    for h in sorted(by_h):
        ex = [o.excess for o in by_h[h]]
        if not ex:
            continue
        rows.append({
            "horizon": h,
            "n": len(ex),
            "mean_excess": statistics.mean(ex),
            "median_excess": statistics.median(ex),
            "hit_rate": sum(1 for e in ex if e > 0) / len(ex),
            "stdev": statistics.pstdev(ex) if len(ex) > 1 else 0.0,
            "mean_stock": statistics.mean(o.stock_return for o in by_h[h]),
            "mean_bench": statistics.mean(o.bench_return for o in by_h[h]),
        })
    return {"horizons": rows, "events": len({id(o.event) for o in outcomes})}


def by_member(outcomes: list[Outcome], horizon: int = 21, min_n: int = 3) -> list[dict]:
    g: dict[str, list[float]] = defaultdict(list)
    for o in outcomes:
        if o.horizon == horizon:
            g[o.event.member].append(o.excess)
    rows = [
        {"member": m, "n": len(v), "mean_excess": statistics.mean(v),
         "hit_rate": sum(1 for e in v if e > 0) / len(v)}
        for m, v in g.items() if len(v) >= min_n
    ]
    rows.sort(key=lambda r: r["mean_excess"], reverse=True)
    return rows


RESULT_PATH = DATA / "backtest.json"


def save(summary: dict, members: list[dict], *, path: Path | None = None) -> Path:
    """Persist a run so the dashboard can show it without recomputing."""
    import json

    path = path or RESULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "members": members,
    }, indent=2))
    return path


def load(path: Path | None = None) -> dict | None:
    import json

    path = path or RESULT_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        log.warning("could not read %s: %s", path, e)
        return None
