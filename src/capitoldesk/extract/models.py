"""Domain models for congressional disclosures.

These pydantic models are also the JSON schema Claude extracts into, so the
field descriptions are load-bearing prompt text - edit them with that in mind.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    STOCK = "ST"
    OPTION = "OP"
    CORPORATE_BOND = "CS"
    MUTUAL_FUND = "MF"
    ETF = "EF"
    GOV_SECURITY = "GS"
    OTHER_ASSET = "AB"
    OTHER = "OT"


class TxnType(str, Enum):
    PURCHASE = "P"
    SALE = "S"
    PARTIAL_SALE = "S_PARTIAL"
    EXCHANGE = "E"


class Owner(str, Enum):
    SELF = "SELF"
    SPOUSE = "SP"
    DEPENDENT = "DC"
    JOINT = "JT"


class OptionDetail(BaseModel):
    """Populated only when the filing explicitly names a contract.

    Pelosi-style filings state 'Purchased 100 call options with a strike price
    of $100 and an expiration date of 6/17/27'. That is a literal contract we
    can replicate rather than infer.
    """

    right: str | None = Field(None, description="'call' or 'put'")
    strike: float | None = Field(None, description="Strike price in dollars")
    expiration: dt.date | None = Field(None, description="Expiration date")
    contracts: int | None = Field(None, description="Number of contracts stated")


class Transaction(BaseModel):
    owner: Owner | None = Field(None, description="SELF if the owner column is blank")
    asset_name: str = Field(description="Full asset name exactly as printed")
    ticker: str | None = Field(
        None, description="Ticker in parentheses, e.g. 'BE'. Null if the filing shows none."
    )
    asset_type: AssetType | None = Field(None, description="Bracketed code, e.g. [ST] -> ST")
    txn_type: TxnType = Field(description="P purchase, S sale, S_PARTIAL for 'S (partial)'")
    txn_date: dt.date
    notification_date: dt.date | None = None
    amount_min: float = Field(description="Lower bound of the disclosed dollar range")
    amount_max: float = Field(description="Upper bound of the disclosed dollar range")
    description: str | None = Field(None, description="The filing's Description field, verbatim")
    option_detail: OptionDetail | None = Field(
        None, description="Only if an explicit option contract is described"
    )

    @property
    def amount_mid(self) -> float:
        return (self.amount_min + self.amount_max) / 2


class Disclosure(BaseModel):
    """One Periodic Transaction Report."""

    doc_id: str
    member_name: str
    state_district: str | None = None
    filing_date: dt.date
    transactions: list[Transaction] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """What Claude returns for a single PTR document."""

    member_name: str = Field(description="Name printed on the filing, e.g. 'Hon. Nancy Pelosi'")
    state_district: str | None = Field(None, description="e.g. 'CA11'")
    transactions: list[Transaction]
    unreadable: bool = Field(
        False, description="True if the document could not be read at all"
    )
    notes: str | None = Field(
        None, description="Anything ambiguous a human should check"
    )
