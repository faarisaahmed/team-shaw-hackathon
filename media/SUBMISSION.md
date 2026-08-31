# Submission copy

Paste-ready text for the lablab submission form.

---

## Project name

**Capitol Desk**

## Tagline (one line)

An autonomous options desk that reads congressional financial disclosures,
decides whether the signal is still alive, and trades it — and declines 97% of
what it sees.

## Short description (~50 words)

Capitol Desk ingests STOCK Act Periodic Transaction Reports from the U.S. House
Clerk, extracts every transaction with Claude — reading scanned filings visually
where no text layer exists — evaluates each against live option chains, and
executes bounded paper trades on Alpaca. Claude decides what to trade; code
decides how much.

## Full description

**The premise.** Every member of Congress must publicly disclose any transaction
over $1,000 within 45 days. 368 of these reports were filed in 2026. They are
free, public, and almost unreadable.

**Why it is hard.** Asset names wrap across lines. The owner column is usually
blank. 12% of filings are scanned paper with zero machine-readable text — one
18-page filing holds 274 transactions and extracts as nothing at all. Paper
filings never print a ticker, because the form instructs members not to. And
every blank form carries a pre-printed `Example Mega Corp` row: a naive
OCR-and-regex pipeline buys it.

Claude reads the text layer where one exists and reads the page visually where
it doesn't. On that 274-row scan it extracted every transaction and correctly
ignored the example row. For scanned filings it proposes a ticker from the asset
name, which Alpaca then verifies before it can become a position — on one filing,
173 proposed, 171 confirmed, two hallucinations caught and dropped.

**The architecture.** Six stages, and the model touches two. Claude extracts the
filings and picks the contract. Everything that touches money — position sizing,
risk limits, order placement — is plain arithmetic in code. There is no prompt a
model can write that gets it a bigger position.

**What it actually does.** Disclosures are stale by construction, so the desk
compares the underlying now against where it traded on the disclosed date and
cuts conviction when the move has already happened. When an outright contract
costs more than the risk budget, it builds a vertical debit spread instead:
Pelosi's disclosed June-2027 Bloom Energy LEAP costs $8,116 outright against a
$5,000 cap, so the desk expressed it as long the $175 call against a short $230
— $2,071 of risk, same expiry, same thesis.

**The part that matters.** 650 transactions parsed. 635 passed over. 3 traded.
It declined a Procter & Gamble purchase because it recognised a routine 401(k)
allocation inside a family trust, and refused another because the option quotes
were internally incoherent — it had caught a bug in our own price feed.

**We measured it.** An event study over 826 disclosed purchases, entering on the
filing date rather than the transaction date (anything earlier is lookahead
bias), shows that copying Congress indiscriminately does not work: hit rates of
45–50%, median trade flat to negative. That argues for the design rather than
against it. If the average disclosure is noise, the value has to come from
selection.

## Tech stack

- **Claude Opus 5** — filing extraction (text and vision), ticker resolution,
  contract selection and conviction
- **Claude Agent SDK** — advisory portfolio-review agent
- **Alpaca MCP Server** — the review agent's read-only market and account tools
- **Alpaca Trading API** — options chains, greeks, single-leg and multi-leg
  order execution, paper account
- Python, FastAPI, SQLite, pytest (45 tests)

## Links

| | |
|---|---|
| Live demo | https://capitol-desk.onrender.com |
| Repository | https://github.com/faarisaahmed/team-shaw-hackathon |
| Video | `media/video/capitol-desk.mp4` (4m18s) |
| Deck | `media/capitol-desk-deck.pdf` (10 slides) |

## Note for judges

The hosted instance is read-only by design: it carries Alpaca credentials only,
so account, positions and P&L are live, but scanning is disabled and `/scan`
returns 403 — a public URL cannot spend tokens or place orders. Clone the repo
and run `desk run` to watch the full pipeline read filings and trade.

These are public filings. The STOCK Act exists to publish this information;
trading on it is not insider trading. All execution is against an Alpaca paper
account, and the desk refuses to start against a live endpoint.
