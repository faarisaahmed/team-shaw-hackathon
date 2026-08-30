# Capitol Desk

An autonomous options desk that reads congressional financial disclosures and
mirrors them in the options market.

Members of Congress are required by the **STOCK Act** to publicly disclose any
transaction over $1,000 within 45 days. Those filings are public, free, and
machine-readable-ish. This desk ingests them, works out what was actually
bought, decides whether the signal is still live, and expresses it as an options
position on Alpaca — end to end, unattended.

Everything here runs against an Alpaca **paper** account. The desk refuses to
start against a live endpoint.

## How it works

```
ingest    House Clerk bulk archive → XML index → PTR PDFs        pure code
extract   e-filed PDFs  → pypdf text  ─┐
          scanned PDFs  → (no text)   ─┴→ Claude → structured transactions
strategy  live option chain + greeks → Claude picks a contract + conviction
size      conviction → contracts, bounded by hard caps            pure code
risk      per-trade / per-day / OI / liquidity gate               pure code
execute   marketable limit order via Alpaca                       pure code
review    agent inspects the live book through Alpaca's MCP server
```

The split is deliberate. **Claude decides *what* to trade; code decides *how
much*.** Position sizing and risk limits are plain arithmetic in
`config.py` and `execute/risk.py`, so a model cannot reason its way into a
bigger position.

### Why an LLM at all

Two places where it genuinely beats the alternative:

**Reading the filings.** PTR PDFs are hostile. Asset names wrap across lines,
the owner column is often blank, transaction types include `S (partial)`, and
headers extract as mangled spacing (`P  T  R`). Worse, ~12% of filings are
scanned paper with **zero** extractable text. A regex parser silently drops rows
— the worst possible failure for a trading signal. Claude reads the text path
and falls back to reading the page visually.

That fallback earns its keep. One scanned filing in the test set is a nil return
reading "Nothing to report for July 2026" — and the blank form has a pre-printed
`Example Mega Corp Common Stock` row sitting in the table. Claude correctly
extracted nothing. OCR-plus-regex buys Mega Corp.

**Judging the signal.** Disclosures are stale by construction — up to 45 days,
often more. Whether the edge survives that lag is a judgment call about a
specific name at a specific price, and the desk makes it explicitly: it compares
the underlying now against where it traded on the disclosed transaction date,
and cuts conviction (or skips) when the move has already happened.

## Setup

```bash
uv venv --python 3.12
uv pip install -e .
```

`.env` needs:

```
ALPACA_API_KEY_ID="PK..."
ALPACA_SECRET_KEY="..."
ALPACA_BASE_URL="https://paper-api.alpaca.markets"
ANTHROPIC_API_KEY="sk-ant-..."
```

Register Alpaca's MCP server with Claude Code (used by `desk review`):

```bash
claude mcp add alpaca --scope local --transport stdio \
  --env ALPACA_API_KEY="$ALPACA_API_KEY_ID" \
  --env ALPACA_SECRET_KEY="$ALPACA_SECRET_KEY" \
  --env ALPACA_PAPER_TRADE=true \
  -- "$HOME/.local/bin/uvx" alpaca-mcp-server
```

## Use

```bash
desk run --days 14              # dry run: show what it would trade, and why
desk run --days 14 --live       # arm it — places paper orders
desk positions                  # open option positions and P&L
desk journal                    # what it traded, and everything it passed over
desk review                     # agent reviews the live book via MCP
desk loop --live                # run unattended: watch for filings and act
desk serve                      # web dashboard on http://127.0.0.1:8000
```

`desk loop` is the unattended runner. It scans on an interval, holds filings
found outside market hours rather than pricing them into a session it hasn't
seen, and periodically hands the book to the review agent.

Nothing trades without `--live`.

## Risk limits

All in `RiskLimits` in `src/capitoldesk/config.py`:

| Limit | Default | Why |
|---|---|---|
| `max_notional_per_trade` | $5,000 | Caps any single idea |
| `max_notional_per_day` | $25,000 | Caps a bad day |
| `max_open_positions` | 25 | Concentration |
| `max_pct_of_open_interest` | 5% | Never be the market in a contract |
| `min_open_interest` | 50 | Skip untradable strikes |
| `max_spread_pct` | 20% | Skip strikes you can't get out of |
| `min/max_days_to_expiry` | 21 / 400 | No lottery tickets, no dead money |
| `max_filing_age_days` | 75 | Stale filings aren't signal |
| `mirror_sales_as_puts` | `False` | Sales are noisy — see below |
| `exit_on_disclosed_sale` | `True` | If the filer exits, so do we |

Orders are always **marketable limit**, never market. Option spreads on thin
strikes make market orders genuinely dangerous.

## Design notes

**Purchases and sales are not symmetric.** A disclosed purchase is a decision. A
disclosed sale is often rebalancing, ethics-driven divestment, tax-lot
management, or blind-trust churn. So sales don't open puts by default — but they
*do* close any position the desk opened by mirroring that name, because the
reason for the trade has expired.

**Expensive underlyings get spreads, not skips.** When an outright call costs
more than the per-trade budget — routine on high-priced names and deep-ITM
LEAPS — the strategist can build a vertical debit spread instead: long the
strike it wants, short a higher strike at the same expiry. Max loss is the net
debit, so that is what gets sized against the risk cap. Every Bloom Energy trade
was blocked as unaffordable before this existed; the same filing now expresses
as long the $175 June-2027 call against a short $230, $2,071 of risk instead of
$8,116.

**Stock purchases want delta, not volatility.** When a filer buys shares they get
delta 1.00 with no theta and no vega. Synthesizing that with an at-the-money call
at 65% IV takes on decay and vol risk the filer never took. The strategist is
given live greeks and told to prefer high-delta ITM calls, or skip.

**The agent is advisory, not executing.** `desk review` gets read-only MCP tools.
It can tell you a position is wrong; it cannot close it.

## Data sources

- House: `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip`
  → `<YEAR>FD.xml` index (`FilingType=P` is a PTR), PDFs at
  `/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf`. Republished daily.
- Senate (`efdsearch.senate.gov`) is not yet wired up — see below.

## Not done yet

- **Senate filings.** House only. The Senate EFD portal requires an interstitial
  terms-of-access acceptance and serves HTML rather than a bulk archive, so it
  needs a separate ingester.
- **Backtesting.** There's no historical replay, so "does this actually make
  money" is unanswered. The 2026 index has 368 PTRs; replaying them against
  historical option chains is the obvious next step.
- **Committee context.** Whether a filer sits on a committee overseeing the
  sector they bought is arguably the strongest feature available, and it isn't
  used.

## Tests

```bash
python -m pytest tests/ -q
```

Covers the deterministic machinery — filing classification, quote handling,
sizing under caps, and the paper-only guard. The LLM stages are exercised by
running `desk run`.

## Legal

These are public filings. The STOCK Act exists to make this information
available. Trading on public disclosures is not insider trading — it is the
opposite: acting on information the law requires be published. This is also a
paper-trading account.
