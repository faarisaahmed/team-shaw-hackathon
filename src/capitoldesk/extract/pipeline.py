"""Turn a PTR PDF into structured transactions.

Two paths, chosen per filing:
  * text   - e-filed PDFs carry a text layer. pypdf pulls it, Claude structures it.
             Cheap, and the majority of filings (~88%).
  * vision - scanned paper filings extract ~0 characters. The raw PDF goes to
             Claude as a document block and is read visually.

Regex was considered and rejected: asset names wrap across lines, the owner
column is often blank, transaction types include 'S (partial)', and the header
text extracts mangled ('P  T  R'). An LLM handles that variance; a regex would
silently drop rows, which is the worst failure mode for a trading signal.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import io
import json
import logging
from pathlib import Path

import anthropic
import pypdf

from ..config import DATA, SETTINGS
from ..ingest.house import FilingRef, fetch_pdf
from .models import Disclosure, ExtractionResult

log = logging.getLogger(__name__)

# Below this many characters we assume there is no usable text layer.
MIN_TEXT_CHARS = 200
# Generous: the largest 2026 filing needs well over 16k output tokens.
MAX_OUTPUT_TOKENS = 64000

SYSTEM = """\
You extract transactions from U.S. House Periodic Transaction Reports (PTRs), \
filed under the STOCK Act. These are public disclosures.

Format notes that matter:
- Section headers extract with mangled spacing ("P  T  R", "F  S  :"). Ignore them.
- Each transaction row is: [Owner] Asset Name (TICKER) [TYPE] | txn type | date | \
notification date | amount range.
- The Owner column holds SP (spouse), DC (dependent child), or JT (joint). When \
blank, the filer owns it: use SELF.
- Transaction type: P = purchase, S = sale, "S (partial)" = partial sale, E = exchange.
- Asset type is the bracketed code: [ST] stock, [OP] option, [GS] government security, \
[AB] other asset, [MF] mutual fund, [EF] ETF, [CS] corporate bond.
- Amount is a printed range like "$1,001 - $15,000". Record both bounds as numbers. \
"$1,000,001 - $5,000,000" means amount_min 1000001, amount_max 5000000.
- A "Description" field may name an explicit option contract, e.g. "Purchased 100 call \
options with a strike price of $100 and an expiration date of 6/17/27". When it does, \
fill option_detail (right, strike, expiration, contracts). Two-digit years are 20xx.
- Dates print as MM/DD/YYYY.

Extract EVERY transaction row, including assets with no ticker. Do not invent a \
ticker that is not printed. If the document is illegible, set unreadable=true."""


def pdf_text(data: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join(p.extract_text() or "" for p in reader.pages).strip()
    except Exception as e:  # noqa: BLE001 - a corrupt PDF must not kill the batch
        log.warning("pypdf failed: %s", e)
        return ""


def _cache_path(ref: FilingRef) -> Path:
    d = DATA / "extracted" / str(ref.year)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{ref.doc_id}.json"


def extract(
    ref: FilingRef,
    *,
    client: anthropic.Anthropic | None = None,
    use_cache: bool = True,
    resolve: bool = True,
    broker=None,
) -> Disclosure:
    """Extract one filing. Results are cached - filings never change once posted."""
    cache = _cache_path(ref)
    if use_cache and cache.exists():
        return Disclosure.model_validate_json(cache.read_text())

    client = client or anthropic.Anthropic(api_key=SETTINGS.anthropic_key)
    data = fetch_pdf(ref)
    text = pdf_text(data)

    if len(text) >= MIN_TEXT_CHARS:
        content = [{"type": "text", "text": f"PTR text for DocID {ref.doc_id}:\n\n{text}"}]
        path = "text"
    else:
        # Scanned filing: hand Claude the PDF itself and let it read the page.
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(data).decode(),
                },
            },
            {
                "type": "text",
                "text": f"This scanned PTR (DocID {ref.doc_id}) has no text layer. "
                "Read the page visually and extract every transaction row.",
            },
        ]
        path = "vision"

    log.info("extracting %s (%s) via %s", ref.doc_id, ref.last, path)
    # Streamed, with a large ceiling. Some members file enormous PTRs - one
    # 18-page scanned filing in the 2026 set holds well over a hundred rows and
    # silently truncated at 16k output tokens, which surfaced only as a JSON
    # parse error. Streaming also keeps the request under the HTTP timeout.
    with client.messages.stream(
        model=SETTINGS.extract_model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractionResult,
    ) as stream:
        resp = stream.get_final_message()

    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            f"extraction of {ref.doc_id} hit the {MAX_OUTPUT_TOKENS}-token output cap; "
            "the filing is larger than the ceiling and would be silently truncated"
        )
    result: ExtractionResult = resp.parsed_output

    disc = Disclosure(
        doc_id=ref.doc_id,
        member_name=result.member_name or ref.member_name,
        state_district=result.state_district or ref.state_district,
        filing_date=ref.filing_date,
        transactions=result.transactions,
    )

    # Scanned filings name assets but never print tickers, so without this the
    # whole paper corpus is untradable. Proposals are verified against Alpaca.
    if resolve and any(not t.ticker and t.asset_name for t in disc.transactions):
        from .resolve import resolve_tickers

        try:
            disc = resolve_tickers(disc, broker=broker, client=client)
        except Exception as e:  # noqa: BLE001 - resolution is best-effort
            log.warning("ticker resolution failed for %s: %s", ref.doc_id, e)

    cache.write_text(disc.model_dump_json(indent=2))
    return disc


def extract_many(
    refs: list[FilingRef], *, workers: int = 8, use_cache: bool = True, resolve: bool = True
) -> list[Disclosure]:
    """Extract a batch concurrently. One failure never sinks the run."""
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_key)
    broker = None
    if resolve:
        from ..execute.broker import Broker

        broker = Broker()  # shared: asset lookups are read-only and thread-safe

    out: list[Disclosure] = []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {
            ex.submit(
                extract, r, client=client, use_cache=use_cache, resolve=resolve, broker=broker
            ): r
            for r in refs
        }
        for fut in cf.as_completed(futs):
            ref = futs[fut]
            try:
                out.append(fut.result())
            except Exception as e:  # noqa: BLE001
                log.error("extraction failed for %s (%s): %s", ref.doc_id, ref.last, e)
    out.sort(key=lambda d: d.filing_date, reverse=True)
    return out
