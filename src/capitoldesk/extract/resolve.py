"""Resolve tickers for filings that don't print them.

The paper PTR form instructs filers to "Provide full name not ticker symbol",
so scanned filings identify holdings only by name - "Apple Inc. Common Stock",
"Bristol Myers Squibb Co". Without resolution the entire scanned corpus is
untradable, and it is not small: one 18-page filing in the 2026 set carries 274
transactions and zero tickers.

Claude proposes a symbol from the name; Alpaca confirms the symbol actually
exists and is tradable. A proposal that fails that check is dropped, not traded,
so a hallucinated ticker cannot become a position.
"""
from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

from ..config import SETTINGS
from ..execute.broker import Broker
from .models import Disclosure

log = logging.getLogger(__name__)

SYSTEM = """\
You map U.S. security names from congressional financial disclosures to their \
exchange ticker symbols.

Rules:
- Return the ticker for the common stock of the named issuer, listed on a U.S. \
exchange (NYSE/Nasdaq).
- Return null when you are not confident, when the asset is not a U.S.-listed \
equity (mutual funds, bonds, treasuries, private LLCs, real estate, options on \
indices), or when the name is too generic to identify one issuer.
- Do not guess. A null is much better than a wrong ticker - a wrong ticker here \
becomes a real trade in the wrong company.
- Use the current ticker for the issuer, accounting for renames (e.g. Facebook \
-> META, Google/Alphabet -> GOOGL)."""


class Mapping(BaseModel):
    asset_name: str
    ticker: str | None = Field(None, description="Uppercase US ticker, or null if unsure")


class Mappings(BaseModel):
    mappings: list[Mapping]


def resolve_tickers(
    disc: Disclosure,
    *,
    broker: Broker | None = None,
    client: anthropic.Anthropic | None = None,
) -> Disclosure:
    """Fill in missing tickers in place, validating each against Alpaca."""
    missing = [t for t in disc.transactions if not t.ticker and t.asset_name]
    if not missing:
        return disc

    names = sorted({t.asset_name.strip() for t in missing})
    client = client or anthropic.Anthropic(api_key=SETTINGS.anthropic_key)
    broker = broker or Broker()

    with client.messages.stream(
        model=SETTINGS.extract_model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": "Map each asset name to a US ticker, or null:\n\n"
                       + "\n".join(f"- {n}" for n in names),
        }],
        output_format=Mappings,
    ) as stream:
        resp = stream.get_final_message()

    proposed = {
        m.asset_name.strip(): m.ticker.strip().upper()
        for m in resp.parsed_output.mappings
        if m.ticker
    }

    # Verify every proposal against the broker before it can become a trade.
    confirmed: dict[str, str] = {}
    for name, sym in proposed.items():
        try:
            asset = broker.trading.get_asset(sym)
        except Exception:  # noqa: BLE001 - unknown symbol
            log.debug("rejected unverifiable ticker %s for %r", sym, name)
            continue
        if getattr(asset, "tradable", False):
            confirmed[name] = sym
        else:
            log.debug("rejected untradable ticker %s for %r", sym, name)

    n = 0
    for t in missing:
        sym = confirmed.get(t.asset_name.strip())
        if sym:
            t.ticker = sym
            t.ticker_inferred = True
            n += 1

    log.info(
        "resolved %d/%d unnamed assets for %s (%d proposed, %d verified)",
        n, len(missing), disc.doc_id, len(proposed), len(confirmed),
    )
    return disc
