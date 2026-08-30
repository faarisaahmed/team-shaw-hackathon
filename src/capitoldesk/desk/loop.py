"""The unattended runner.

Without this the desk is a script a human types. With it, the desk is a process
that watches for new filings and acts on them on its own - which is the claim
the project actually makes.

Two cadences, because they have different natural rates:
  * scan   - the House Clerk republishes the bulk index daily, and filings post
             through the business day. Polling every ~15 minutes is plenty.
  * review - the book only needs a risk pass a few times a day.

Orders are only submitted while the market is open. Filings found outside
market hours are held and traded at the next open rather than queued blind
into a session whose prices we have not seen.
"""
from __future__ import annotations

import datetime as dt
import logging
import signal
import time
from dataclasses import dataclass, field

from rich.console import Console

from ..config import SETTINGS
from ..execute import ledger
from .run import Desk

log = logging.getLogger(__name__)
console = Console()


@dataclass
class LoopState:
    cycles: int = 0
    filings_seen: int = 0
    orders_placed: int = 0
    errors: int = 0
    started: dt.datetime = field(default_factory=dt.datetime.now)
    last_review: dt.datetime | None = None

    def summary(self) -> str:
        up = dt.datetime.now() - self.started
        hrs, rem = divmod(int(up.total_seconds()), 3600)
        return (
            f"up {hrs}h{rem // 60:02d}m | cycles {self.cycles} | "
            f"filings {self.filings_seen} | orders {self.orders_placed} | errors {self.errors}"
        )


class DeskLoop:
    def __init__(
        self,
        *,
        live: bool = False,
        interval: int = 900,
        review_every: int = 4 * 3600,
        days: int = 30,
        trade_only_when_open: bool = True,
    ) -> None:
        self.desk = Desk(live=live)
        self.interval = interval
        self.review_every = review_every
        self.days = days
        self.trade_only_when_open = trade_only_when_open
        self.state = LoopState()
        self._stop = False

    def _install_signals(self) -> None:
        def handle(signum, _frame):
            console.print(f"\n[yellow]signal {signum} - finishing this cycle and stopping[/]")
            self._stop = True

        signal.signal(signal.SIGINT, handle)
        signal.signal(signal.SIGTERM, handle)

    def market_open(self) -> bool:
        try:
            return bool(self.desk.broker.trading.get_clock().is_open)
        except Exception as e:  # noqa: BLE001
            log.warning("clock unavailable, assuming closed: %s", e)
            return False

    def _tick(self) -> None:
        """One pass. Never raises - a bad cycle must not kill the process."""
        self.state.cycles += 1
        is_open = self.market_open()

        from ..execute import reconcile

        try:
            synced = reconcile.sync(self.desk.broker)
            if synced:
                console.print("[dim]order states: " + ", ".join(f"{k} {v}" for k, v in synced.items()) + "[/]")
        except Exception as e:  # noqa: BLE001
            log.warning("reconcile failed: %s", e)

        refs = self.desk.scan(max_age_days=self.days)
        if not refs:
            console.print(f"[dim]{dt.datetime.now():%H:%M:%S} no new filings[/]")
            return

        console.print(f"[cyan]{dt.datetime.now():%H:%M:%S}[/] {len(refs)} new filing(s)")
        discs = self.desk.understand(refs)
        self.state.filings_seen += len(discs)

        if self.trade_only_when_open and not is_open:
            # Extraction is cached, so reopening these at the next open is cheap.
            # We deliberately do not price or size against a closed market.
            console.print(
                "[yellow]market closed - filings extracted and held for the next open[/]"
            )
            self._unmark(discs)
            return

        plans, _ = self.desk.decide(discs)
        if not plans:
            console.print("[dim]nothing met the bar[/]")
            return

        results = self.desk.execute(plans)
        placed = [r for r in results if r[1].startswith("placed")]
        self.state.orders_placed += len(placed)
        self.desk._render(results)

        exits = self.desk.handle_exits(discs)
        for n in exits:
            console.print(f"[magenta]exit[/] {n}")

    def _unmark(self, discs) -> None:
        """Undo 'seen' so held filings are reconsidered at the next open."""
        import sqlite3

        with sqlite3.connect(ledger.DB) as c:
            c.executemany("DELETE FROM seen WHERE doc_id=?", [(d.doc_id,) for d in discs])

    def _maybe_review(self) -> None:
        if self.review_every <= 0:
            return
        now = dt.datetime.now()
        if self.state.last_review and (now - self.state.last_review).total_seconds() < self.review_every:
            return
        if not ledger.recent_trades(1):
            return
        self.state.last_review = now
        try:
            import asyncio

            from .agent import review

            note = asyncio.run(review())
            console.rule("[bold]desk review")
            console.print(note)
            console.rule()
        except Exception as e:  # noqa: BLE001
            log.error("review failed: %s", e)
            self.state.errors += 1

    def run(self, *, max_cycles: int | None = None) -> LoopState:
        self._install_signals()
        console.print(
            f"[bold cyan]Capitol Desk loop[/] - every {self.interval}s, "
            f"{'ARMED' if self.desk.live else 'DRY RUN'}, "
            f"trading {'only while open' if self.trade_only_when_open else 'always'}\n"
            f"[dim]ctrl-c to stop[/]"
        )
        while not self._stop:
            try:
                self._tick()
                self._maybe_review()
            except Exception as e:  # noqa: BLE001
                self.state.errors += 1
                log.exception("cycle failed: %s", e)
                console.print(f"[red]cycle error:[/] {e}")

            if max_cycles and self.state.cycles >= max_cycles:
                break
            if self._stop:
                break

            console.print(f"[dim]{self.state.summary()} - sleeping {self.interval}s[/]")
            for _ in range(self.interval):
                if self._stop:
                    break
                time.sleep(1)

        console.print(f"[bold]stopped.[/] {self.state.summary()}")
        return self.state
