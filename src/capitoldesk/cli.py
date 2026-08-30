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
def sync(verbose: bool = typer.Option(False, "-v")):
    """Refresh journal order states from the broker."""
    _log(verbose)
    from .execute import reconcile

    counts = reconcile.sync()
    console.print(counts or "[dim]no open orders[/]")


@app.command()
def journal(limit: int = 25):
    """Show what the desk has done and what it passed over."""
    from .execute import ledger

    tr = ledger.recent_trades(limit)
    t = Table(title="Trades", header_style="bold")
    for c in ("When", "Member", "Ticker", "Contract", "Qty", "Notional", "Conv", "Status", "Filled"):
        t.add_column(c)
    for r in tr:
        filled = (
            f"{r['filled_qty']} @ ${r['filled_price']:,.2f}"
            if r.get("filled_qty") and r.get("filled_price") else "—"
        )
        t.add_row(
            (r["placed_at"] or "")[:16], (r["member"] or "").replace("Hon. ", ""),
            r["ticker"], r["contract_symbol"], str(r["contracts"]),
            f"${r['notional']:,.0f}", f"{r['conviction']:.2f}", r["status"], filled,
        )
    console.print(t)
    rj = ledger.recent_rejections(limit)
    t2 = Table(title="Passed over", header_style="bold dim")
    t2.add_column("Ticker"); t2.add_column("Reason", overflow="fold")
    for r in rj:
        t2.add_row(r["ticker"] or "-", escape(r["reason"] or ""))
    console.print(t2)


@app.command()
def backfill(
    year: int = typer.Option(2025, help="Filing year to extract."),
    limit: int = typer.Option(80, help="How many filings to extract."),
    before: str = typer.Option(None, help="Only filings before this date (YYYY-MM-DD)."),
    workers: int = typer.Option(8),
    verbose: bool = typer.Option(False, "-v"),
):
    """Extract historical filings into the local cache, for research."""
    _log(verbose)
    import datetime as dt

    from .extract.pipeline import extract_many
    from .ingest.house import fetch_index

    refs = fetch_index(year)
    if before:
        cutoff = dt.date.fromisoformat(before)
        refs = [r for r in refs if r.filing_date < cutoff]
    refs.sort(key=lambda r: r.filing_date)
    refs = refs[:limit]
    console.print(f"extracting {len(refs)} filings from {year}...")
    out = extract_many(refs, workers=workers)
    console.print(f"[green]extracted {len(out)} filings[/]")


@app.command()
def backtest(
    horizon: int = typer.Option(21, help="Horizon (trading days) for the per-member table."),
    save: bool = typer.Option(True, help="Persist results for the dashboard."),
    verbose: bool = typer.Option(False, "-v"),
):
    """Event study: do disclosed purchases beat SPY after the filing goes public?"""
    _log(verbose)
    from .research.backtest import (
        EventStudy, build_events, by_member, load_cached_disclosures, summarise,
    )

    discs = load_cached_disclosures()
    events = build_events(discs)
    console.print(f"[dim]{len(discs)} filings, {len(events)} disclosed purchases with tickers[/]")
    outcomes = EventStudy().run(events)
    s = summarise(outcomes)

    t = Table(title="Excess return over SPY, from the filing date", header_style="bold")
    for c in ("Horizon", "n", "Mean stock", "Mean SPY", "Mean excess", "Median excess", "Hit rate"):
        t.add_column(c, justify="right" if c != "Horizon" else "left")
    for r in s["horizons"]:
        col = "green" if r["mean_excess"] > 0 else "red"
        t.add_row(
            f"{r['horizon']}d", str(r["n"]), f"{r['mean_stock']*100:.2f}%",
            f"{r['mean_bench']*100:.2f}%",
            f"[{col}]{r['mean_excess']*100:+.2f}%[/]",
            f"{r['median_excess']*100:+.2f}%", f"{r['hit_rate']*100:.1f}%",
        )
    console.print(t)

    rows = by_member(outcomes, horizon=horizon)
    if rows:
        t2 = Table(title=f"By member ({horizon}d, n>=3)", header_style="bold")
        for c in ("Member", "n", "Mean excess", "Hit rate"):
            t2.add_column(c, justify="right" if c != "Member" else "left")
        for r in rows[:12]:
            col = "green" if r["mean_excess"] > 0 else "red"
            t2.add_row(
                r["member"].replace("Hon. ", ""), str(r["n"]),
                f"[{col}]{r['mean_excess']*100:+.2f}%[/]", f"{r['hit_rate']*100:.1f}%",
            )
        console.print(t2)
    console.print(
        "[dim]Entry is the FILING date, not the transaction date - the trade is "
        "private until filed, so entering earlier would be lookahead bias.[/]"
    )
    if save:
        from .research.backtest import save as save_results

        p = save_results(s, rows)
        console.print(f"[dim]saved to {p}[/]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address. Use 0.0.0.0 to expose."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload"),
):
    """Serve the web dashboard."""
    import uvicorn

    uvicorn.run("capitoldesk.web.app:app", host=host, port=port, reload=reload)


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
