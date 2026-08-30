"""The plan object that flows from decision -> risk gate -> execution."""
from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class Mode(str, Enum):
    REPLICATE = "replicate"   # filing named an explicit contract
    SYNTHESIZE = "synthesize" # filing showed stock; we express it in options
    SKIP = "skip"


class Decision(BaseModel):
    """What Claude decides, given real contracts and real quotes.

    Claude picks *which* contract and *how strongly*; it does not compute
    position size. That is deliberate - sizing is arithmetic bounded by hard
    caps, and belongs in code.
    """

    action: Mode = Field(description="replicate, synthesize, or skip")
    contract_symbol: str | None = Field(
        None, description="OCC symbol of the chosen contract, exactly as listed in the candidates"
    )
    conviction: float = Field(
        0.0, ge=0.0, le=1.0,
        description=(
            "0-1. Drives position size. Weigh: size of the disclosed trade, how much "
            "the underlying has already moved since the transaction date (a name that "
            "already ran has less edge left), filing freshness, and contract liquidity."
        ),
    )
    rationale: str = Field(description="Two or three sentences. Be concrete about the tradeoff.")
    signal_decayed: bool = Field(
        False, description="True if the underlying has already made the move the filer was positioned for"
    )


class Plan(BaseModel):
    """A fully-sized, risk-checked intent to trade."""

    doc_id: str
    member: str
    ticker: str
    mode: Mode
    contract_symbol: str
    right: str
    strike: float
    expiration: dt.date
    contracts: int
    limit_price: float
    est_notional: float
    conviction: float
    rationale: str
    signal_decayed: bool = False

    # provenance
    txn_date: dt.date
    filing_date: dt.date
    disclosed_min: float
    disclosed_max: float
    underlying_at_txn: float | None = None
    underlying_now: float | None = None

    @property
    def move_since_txn(self) -> float | None:
        if self.underlying_at_txn and self.underlying_now:
            return (self.underlying_now / self.underlying_at_txn) - 1
        return None


class Rejection(BaseModel):
    doc_id: str
    ticker: str | None
    reason: str
