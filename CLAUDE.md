# Capitol Desk — working notes

Autonomous options desk that mirrors congressional STOCK Act disclosures onto an
Alpaca **paper** account. See `README.md` for the full picture.

## Run it

```bash
.venv/bin/python -m capitoldesk.cli run --days 14        # dry run
.venv/bin/python -m capitoldesk.cli run --days 14 --live # place paper orders
.venv/bin/python -m capitoldesk.cli journal
.venv/bin/python -m capitoldesk.cli review               # agent review over MCP
.venv/bin/python -m pytest tests/ -q
```

## The one rule

**Claude decides what to trade; code decides how much.** Position sizing lives in
`Strategist._size`, limits live in `RiskLimits` (`config.py`), and the
portfolio-level gate is `execute/risk.py`. Do not move sizing or limit
enforcement into a prompt, and do not give the review agent order-placing tools.

## Gotchas worth remembering

- **Never use quote midpoint for stock prices.** The free IEX feed returns
  one-sided quotes (`ask=0.0`), and `(bid+ask)/2` then reports exactly half the
  real price. `Broker.stock_price` goes last-trade → two-sided quote → daily bar.
  This silently poisoned every strike selection until it was caught.
- **DocID >= 20000000 means e-filed** (has a text layer). Lower means a scanned
  paper filing with zero extractable text — those go to Claude as a PDF document
  block and are read visually.
- **Blank PTR forms contain a pre-printed `Example Mega Corp Common Stock` row.**
  Any extraction change must keep ignoring it.
- **Escape model-authored prose before handing it to Rich** (`rich.markup.escape`)
  — a rationale containing `[ST]` gets swallowed as markup otherwise.
- Filings are immutable once posted, so `data/extracted/` and `data/ptr/` are
  cached indefinitely. Delete them to force re-extraction.
- The bulk index is **republished daily**; the ledger's `seen` table is what
  prevents re-trading a filing.

## Layout

```
ingest/house.py       bulk index + PDF fetch
extract/pipeline.py   text vs vision extraction
extract/models.py     pydantic schemas (also the LLM output schema)
strategy/engine.py    candidate generation, LLM decision, sizing
execute/broker.py     Alpaca wrapper (no LLM in here)
execute/risk.py       portfolio-level gate
execute/ledger.py     sqlite journal + dedup
desk/run.py           the cycle
desk/agent.py         Agent SDK review over Alpaca MCP
```
