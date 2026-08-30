"""The desk loop: filings in, orders out."""
from __future__ import annotations

import datetime as dt
import logging

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ..config import SETTINGS
from ..execute import ledger, risk
from ..execute.broker import Broker
from ..extract.models import Disclosure
from ..extract.pipeline import extract_many
from ..ingest.house import recent_filings
from ..strategy.engine import Strategist
from ..strategy.plan import Plan, Rejection

log = logging.getLogger(__name__)
console = Console()


class Desk:
    def __init__(self, *, live: bool = False) -> None:
        SETTINGS.require()
        self.live = live
        self.broker = Broker()
        self.strategist = Strategist(self.broker)

    # ---------- stages ----------

    def scan(self, *, max_age_days: int | None = None, refresh: bool = True,
             include_seen: bool = False) -> list:
        age = max_age_days or SETTINGS.risk.max_filing_age_days
        refs = recent_filings(max_age_days=age, refresh=refresh)
        if not include_seen:
            refs = [r for r in refs if not ledger.already_seen(r.doc_id)]
        return refs

    def understand(self, refs: list) -> list[Disclosure]:
        discs = extract_many(refs)
        for d in discs:
            ledger.mark_seen(d.doc_id, d.member_name, d.filing_date, len(d.transactions))
        return discs

    def decide(self, discs: list[Disclosure]) -> tuple[list[Plan], list[Rejection]]:
        plans: list[Plan] = []
        rejects: list[Rejection] = []
        for d in discs:
            for t in d.transactions:
                res = self.strategist.plan_for(d, t)
                if isinstance(res, Plan):
                    plans.append(res)
                else:
                    rejects.append(res)
                    ledger.record_rejection(res.doc_id, res.ticker, res.reason)
        plans.sort(key=lambda p: p.conviction, reverse=True)
        return plans, rejects

    def handle_exits(self, discs: list[Disclosure]) -> list[str]:
        """Close mirrored positions when the filer discloses selling the name.

        The entry signal was 'this member bought X'. When that stops being true,
        the reason we are in the trade has expired - so we exit, regardless of
        whether the position is up or down.
        """
        if not SETTINGS.risk.exit_on_disclosed_sale:
            return []

        from ..extract.models import TxnType

        held = {p.symbol for p in self.broker.option_positions()}
        notes: list[str] = []
        for d in discs:
            for t in d.transactions:
                if not t.ticker or t.txn_type not in (TxnType.SALE, TxnType.PARTIAL_SALE):
                    continue
                for row in ledger.open_trades_for_ticker(t.ticker):
                    sym = row["contract_symbol"]
                    if sym not in held:
                        continue
                    msg = f"{t.ticker}: {d.member_name} disclosed a sale - closing {sym}"
                    if not self.live:
                        notes.append(f"[dry-run] {msg}")
                        continue
                    try:
                        self.broker.close_option_position(sym)
                        ledger.mark_closed(row["id"], f"{d.member_name} sold")
                        held.discard(sym)
                        notes.append(msg)
                    except Exception as e:  # noqa: BLE001
                        notes.append(f"failed to close {sym}: {e}")
        return notes

    def execute(self, plans: list[Plan]) -> list[tuple[Plan, str]]:
        acct = self.broker.account()
        bp = float(acct.options_buying_power or acct.buying_power or 0)
        results: list[tuple[Plan, str]] = []

        for p in plans:
            v = risk.check(p, buying_power=bp)
            if not v.approved:
                ledger.record_rejection(p.doc_id, p.ticker, f"risk gate: {v.reason}")
                results.append((p, f"blocked: {v.reason}"))
                continue

            if not self.live:
                results.append((p, "dry-run"))
                continue

            try:
                coid = f"cd-{p.doc_id}-{p.contract_symbol}"[:48]
                if p.is_spread:
                    order = self.broker.buy_debit_spread(
                        p.contract_symbol, p.short_leg_symbol, p.contracts,
                        p.limit_price, client_order_id=coid,
                    )
                else:
                    order = self.broker.buy_to_open(
                        p.contract_symbol, p.contracts, p.limit_price, client_order_id=coid,
                    )
                ledger.record_trade(p, str(order.id), "placed")
                bp -= p.est_notional
                results.append((p, f"placed {order.id}"))
            except Exception as e:  # noqa: BLE001
                ledger.record_trade(p, None, "failed")
                results.append((p, f"failed: {e}"))
        return results

    # ---------- one full pass ----------

    def cycle(self, *, max_age_days: int | None = None, limit: int | None = None) -> None:
        acct = self.broker.account()
        console.print(
            Panel(
                f"[bold]Capitol Desk[/]   account [cyan]{acct.account_number}[/] "
                f"({'PAPER' if SETTINGS.paper else 'LIVE'})\n"
                f"cash ${float(acct.cash):,.0f}   options buying power "
                f"${float(acct.options_buying_power or 0):,.0f}   "
                f"mode [{'red bold' if self.live else 'yellow'}]"
                f"{'ARMED - will place orders' if self.live else 'DRY RUN'}[/]",
                border_style="cyan",
            )
        )

        # Reconcile first: decisions and risk checks should see what actually
        # happened at the broker, not what we optimistically recorded.
        from ..execute import reconcile

        synced = reconcile.sync(self.broker)
        if synced:
            console.print(f"[dim]order states:[/] " + ", ".join(f"{k} {v}" for k, v in synced.items()))

        refs = self.scan(max_age_days=max_age_days)
        if limit:
            refs = refs[:limit]
        console.print(f"[dim]new filings to process:[/] {len(refs)}")
        if not refs:
            console.print("[dim]nothing new.[/]")
            return

        with console.status("reading filings with Claude..."):
            discs = self.understand(refs)
        n_txn = sum(len(d.transactions) for d in discs)
        console.print(f"[dim]extracted[/] {n_txn} transactions from {len(discs)} filings")

        with console.status("evaluating against live option chains..."):
            plans, rejects = self.decide(discs)
        console.print(f"[dim]candidate trades:[/] {len(plans)}   [dim]passed over:[/] {len(rejects)}")

        exits = self.handle_exits(discs)
        for n in exits:
            console.print(f"[magenta]exit[/] {n}")

        if plans:
            results = self.execute(plans)
            self._render(results)
        if rejects:
            self._render_rejects(rejects)

    # ---------- rendering ----------

    def _render(self, results: list[tuple[Plan, str]]) -> None:
        t = Table(title="Trades", header_style="bold", show_lines=True)
        for col in ("Member", "Ticker", "Contract", "Qty", "Limit", "Notional", "Conv", "Status"):
            t.add_column(col)
        for p, status in results:
            colour = "green" if status.startswith("placed") else (
                "yellow" if status == "dry-run" else "red"
            )
            symbol = (
                f"{p.contract_symbol}\n / {p.short_leg_symbol}" if p.is_spread else p.contract_symbol
            )
            t.add_row(
                p.member.replace("Hon. ", ""), p.ticker, symbol, str(p.contracts),
                f"${p.limit_price:,.2f}", f"${p.est_notional:,.0f}",
                f"{p.conviction:.2f}", f"[{colour}]{status}[/]",
            )
        console.print(t)
        for p, _ in results:
            move = f"{p.move_since_txn * 100:+.1f}%" if p.move_since_txn is not None else "n/a"
            flag = " [red](signal decayed)[/]" if p.signal_decayed else ""
            console.print(
                Panel(
                    escape(p.rationale),
                    title=(
                        f"{p.ticker} {p.mode.value} ({p.structure}) - {p.member} "
                        f"- underlying {move} since txn{flag}"
                    ),
                    border_style="dim",
                )
            )

    def _render_rejects(self, rejects: list[Rejection]) -> None:
        t = Table(title="Passed over", header_style="bold dim", show_lines=False)
        t.add_column("Ticker"); t.add_column("Reason", overflow="fold")
        for r in rejects[:20]:
            t.add_row(r.ticker or "-", escape(r.reason))
        console.print(t)
