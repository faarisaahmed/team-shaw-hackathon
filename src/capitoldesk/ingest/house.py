"""Ingest Periodic Transaction Reports from the U.S. House Clerk.

Source of truth is the Clerk's bulk archive, republished daily:
    https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip
containing <YEAR>FD.xml, one <Member> element per filing. FilingType 'P' is a
Periodic Transaction Report. Each filing's PDF lives at:
    https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf

DocIDs >= 20000000 are e-filed (extractable text). Lower DocIDs are scanned
paper filings that yield no text and need the vision path.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import DATA

log = logging.getLogger(__name__)

BULK_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
# The Clerk's CDN rejects requests without a browser-ish UA.
UA = {"User-Agent": "Mozilla/5.0 (compatible; capitoldesk/0.1; research)"}

EFILED_MIN_DOCID = 20_000_000


@dataclass(frozen=True)
class FilingRef:
    """A row from the bulk XML index - metadata only, no transactions yet."""

    doc_id: str
    last: str
    first: str
    suffix: str | None
    prefix: str | None
    state_district: str | None
    filing_date: dt.date
    year: int

    @property
    def member_name(self) -> str:
        parts = [self.prefix, self.first, self.last, self.suffix]
        return " ".join(p for p in parts if p)

    @property
    def is_efiled(self) -> bool:
        """E-filed PDFs carry a text layer; scanned ones do not."""
        return self.doc_id.isdigit() and int(self.doc_id) >= EFILED_MIN_DOCID

    @property
    def pdf_url(self) -> str:
        return PTR_PDF.format(year=self.year, doc_id=self.doc_id)

    def age_days(self, today: dt.date | None = None) -> int:
        return ((today or dt.date.today()) - self.filing_date).days


def _cache_dir(year: int) -> Path:
    d = DATA / "ptr" / str(year)
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_index(year: int, *, refresh: bool = False) -> list[FilingRef]:
    """Download and parse the bulk XML index, returning only PTR filings."""
    cache = DATA / "index" / f"{year}FD.xml"
    cache.parent.mkdir(parents=True, exist_ok=True)

    if refresh or not cache.exists():
        log.info("fetching bulk index for %s", year)
        r = httpx.get(BULK_ZIP.format(year=year), headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
            cache.write_bytes(z.read(name))

    root = ET.fromstring(cache.read_text(encoding="utf-8-sig"))
    out: list[FilingRef] = []
    for m in root:
        if m.findtext("FilingType") != "P":
            continue
        raw_date = m.findtext("FilingDate")
        doc_id = m.findtext("DocID")
        if not raw_date or not doc_id:
            continue
        try:
            filing_date = dt.datetime.strptime(raw_date, "%m/%d/%Y").date()
        except ValueError:
            log.warning("unparseable FilingDate %r on DocID %s", raw_date, doc_id)
            continue
        out.append(
            FilingRef(
                doc_id=doc_id.strip(),
                last=(m.findtext("Last") or "").strip(),
                first=(m.findtext("First") or "").strip(),
                suffix=(m.findtext("Suffix") or "").strip() or None,
                prefix=(m.findtext("Prefix") or "").strip() or None,
                state_district=(m.findtext("StateDst") or "").strip() or None,
                filing_date=filing_date,
                year=int(m.findtext("Year") or year),
            )
        )
    out.sort(key=lambda f: f.filing_date, reverse=True)
    log.info("index %s: %d PTR filings", year, len(out))
    return out


def fetch_pdf(ref: FilingRef, *, client: httpx.Client | None = None) -> bytes:
    """Fetch a PTR PDF, caching it on disk. Filings are immutable once posted."""
    path = _cache_dir(ref.year) / f"{ref.doc_id}.pdf"
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()

    owns = client is None
    client = client or httpx.Client(headers=UA, timeout=60, follow_redirects=True)
    try:
        r = client.get(ref.pdf_url)
        r.raise_for_status()
        path.write_bytes(r.content)
        return r.content
    finally:
        if owns:
            client.close()


def recent_filings(
    *, year: int | None = None, max_age_days: int = 75, refresh: bool = False
) -> list[FilingRef]:
    """PTRs filed within `max_age_days`, newest first.

    Spans the year boundary automatically so a January run still sees December.
    """
    today = dt.date.today()
    year = year or today.year
    years = {year}
    if (today - dt.timedelta(days=max_age_days)).year != year:
        years.add(year - 1)

    refs: list[FilingRef] = []
    for y in sorted(years):
        try:
            refs.extend(fetch_index(y, refresh=refresh))
        except httpx.HTTPError as e:
            log.warning("could not fetch index for %s: %s", y, e)

    fresh = [r for r in refs if r.age_days(today) <= max_age_days]
    fresh.sort(key=lambda f: f.filing_date, reverse=True)
    return fresh
