from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

app = typer.Typer(add_completion=False, help="Autonomous congressional-disclosure options desk.")
console = Console()


def _log(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def run(
    live: bool = typer.Option(False, "--live", help="Actually place paper orders."),
    days: int = typer.Option(30, help="Only consider filings this recent."),
    limit: int = typer.Option(None, help="Cap how many filings to process."),
    verbose: bool = typer.Option(False, "-v"),
):
    """Run one full cycle: scan -> extract -> decide -> execute."""
    _log(verbose)
    from .desk.run import Desk

    Desk(live=live).cycle(max_age_days=days, limit=limit)


@app.command()
def positions(verbose: bool = typer.Option(False, "-v")):
    """Show current option positions and P&L."""
    _log(verbose)
    from .execute.broker import Broker

    b = Broker()
    acct = b.account()
    console.print(
        f"[bold]{acct.account_number}[/]  equity ${float(acct.equity):,.2f}  "
        f"cash ${float(acct.cash):,.2f}"
    )
    pos = b.option_positions()
    if not pos:
        console.print("[dim]no option positions.[/]")
        return
    t = Table(header_style="bold")
    for c in ("Symbol", "Qty", "Avg cost", "Current", "Market value", "P&L", "P&L %"):
        t.add_column(c)
    for p in pos:
        pl = float(p.unrealized_pl or 0)
        t.add_row(
            p.symbol, str(p.qty), f"${float(p.avg_entry_price):,.2f}",
            f"${float(p.current_price or 0):,.2f}", f"${float(p.market_value or 0):,.2f}",
            f"[{'green' if pl >= 0 else 'red'}]${pl:,.2f}[/]",
            f"{float(p.unrealized_plpc or 0) * 100:+.1f}%",
        )
    console.print(t)


@app.command()
def journal(limit: int = 25):
    """Show what the desk has done and what it passed over."""
    from .execute import ledger

    tr = ledger.recent_trades(limit)
    t = Table(title="Trades", header_style="bold")
    for c in ("When", "Member", "Ticker", "Contract", "Qty", "Notional", "Conv", "Status"):
        t.add_column(c)
    for r in tr:
        t.add_row(
            (r["placed_at"] or "")[:16], (r["member"] or "").replace("Hon. ", ""),
            r["ticker"], r["contract_symbol"], str(r["contracts"]),
            f"${r['notional']:,.0f}", f"{r['conviction']:.2f}", r["status"],
        )
    console.print(t)
    rj = ledger.recent_rejections(limit)
    t2 = Table(title="Passed over", header_style="bold dim")
    t2.add_column("Ticker"); t2.add_column("Reason", overflow="fold")
    for r in rj:
        t2.add_row(r["ticker"] or "-", escape(r["reason"] or ""))
    console.print(t2)


@app.command()
def loop(
    live: bool = typer.Option(False, "--live", help="Actually place paper orders."),
    interval: int = typer.Option(900, help="Seconds between scans."),
    days: int = typer.Option(30, help="Only consider filings this recent."),
    review_every: int = typer.Option(4 * 3600, help="Seconds between agent reviews (0 to disable)."),
    trade_when_closed: bool = typer.Option(
        False, "--trade-when-closed", help="Submit orders even outside market hours."
    ),
    max_cycles: int = typer.Option(None, help="Stop after N cycles (for testing)."),
    verbose: bool = typer.Option(False, "-v"),
):
    """Run unattended: watch for new filings and act on them."""
    _log(verbose)
    from .desk.loop import DeskLoop

    DeskLoop(
        live=live,
        interval=interval,
        days=days,
        review_every=review_every,
        trade_only_when_open=not trade_when_closed,
    ).run(max_cycles=max_cycles)


@app.command()
def review(verbose: bool = typer.Option(False, "-v")):
    """Have the agent review the open book via Alpaca's MCP server."""
    _log(verbose)
    import asyncio

    from rich.markdown import Markdown

    from .desk.agent import review as do_review

    with console.status("agent reviewing the book via MCP..."):
        note = asyncio.run(do_review())
    console.print(Markdown(note or "_(no output)_"))


if __name__ == "__main__":
    app()
