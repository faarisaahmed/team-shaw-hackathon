"""Configuration and hard risk limits for the desk.

Every number that bounds real (paper) money lives here, not in a prompt.
The agent can reason about strategy; it cannot reason its way past these.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")


def _env(*names: str, default: str | None = None) -> str | None:
    """First non-empty value among `names`. Tolerates the two Alpaca key spellings."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip().strip('"')
    return default


@dataclass(frozen=True)
class RiskLimits:
    """Hard caps. Enforced in code before any order leaves the process."""

    max_notional_per_trade: float = 5_000.0
    max_notional_per_day: float = 25_000.0
    max_open_positions: int = 25
    max_contracts_per_order: int = 20
    max_pct_of_open_interest: float = 0.05   # don't be >5% of a contract's OI
    min_open_interest: int = 50              # skip illiquid contracts
    max_spread_pct: float = 0.20             # skip if bid/ask spread > 20% of mid
    min_days_to_expiry: int = 21
    max_days_to_expiry: int = 400
    # A disclosure older than this is stale signal; the edge is long gone.
    max_filing_age_days: int = 75
    # Mirror disclosed SALES as long puts. Off by default: congressional sales
    # are far noisier than purchases (rebalancing, blind-trust churn, tax lots),
    # so the directional read is much weaker.
    mirror_sales_as_puts: bool = False
    # Always close a mirrored position when the filer discloses selling it.
    exit_on_disclosed_sale: bool = True


@dataclass(frozen=True)
class Settings:
    alpaca_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY_ID", "ALPACA_API_KEY") or "")
    alpaca_secret: str = field(default_factory=lambda: _env("ALPACA_SECRET_KEY") or "")
    alpaca_base_url: str = field(
        default_factory=lambda: _env("ALPACA_BASE_URL", default="https://paper-api.alpaca.markets")
    )
    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY") or "")

    model: str = "claude-opus-5"
    # Extraction is high-volume and mechanical; strategy is the judgment call.
    # Both default to Opus 5 - drop `extract_model` to a cheaper model if the
    # filing backlog gets expensive.
    extract_model: str = "claude-opus-5"

    risk: RiskLimits = field(default_factory=RiskLimits)

    @property
    def paper(self) -> bool:
        return "paper" in self.alpaca_base_url

    @property
    def read_only(self) -> bool:
        """Hosted-demo mode: serve live broker data, refuse to spend anything.

        On by default whenever there is no Anthropic key, so a deployment that
        only carries Alpaca credentials degrades into a safe read-only demo
        instead of erroring on startup. Can be forced with CAPITOL_DESK_READ_ONLY.
        """
        forced = (_env("CAPITOL_DESK_READ_ONLY") or "").strip().lower()
        if forced in ("1", "true", "yes"):
            return True
        if forced in ("0", "false", "no"):
            return False
        return not self.anthropic_key

    def require(self, *, need_llm: bool = True) -> None:
        checks = [
            ("ALPACA_API_KEY_ID", self.alpaca_key),
            ("ALPACA_SECRET_KEY", self.alpaca_secret),
        ]
        if need_llm:
            checks.append(("ANTHROPIC_API_KEY", self.anthropic_key))
        missing = [n for n, v in checks if not v]
        if missing:
            raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
        if not self.paper:
            raise SystemExit(
                "Refusing to start against a non-paper Alpaca endpoint. "
                "This desk is built for paper trading only; "
                f"ALPACA_BASE_URL={self.alpaca_base_url!r}"
            )


SETTINGS = Settings()
