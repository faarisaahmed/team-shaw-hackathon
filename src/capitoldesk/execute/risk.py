"""Portfolio-level gate. The last thing between a plan and a live order.

Per-trade sizing already happened in the strategist; this enforces the limits
that are only knowable across the whole book.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import SETTINGS
from ..strategy.plan import Plan
from . import ledger


@dataclass(frozen=True)
class Verdict:
    approved: bool
    reason: str = ""


def check(plan: Plan, *, buying_power: float | None = None) -> Verdict:
    r = SETTINGS.risk

    if ledger.already_traded(plan.doc_id, plan.contract_symbol):
        return Verdict(False, "already mirrored this filing/contract")

    if plan.est_notional > r.max_notional_per_trade:
        return Verdict(
            False,
            f"notional ${plan.est_notional:,.0f} exceeds per-trade cap ${r.max_notional_per_trade:,.0f}",
        )

    spent = ledger.notional_today()
    if spent + plan.est_notional > r.max_notional_per_day:
        return Verdict(
            False,
            f"would breach daily cap: ${spent:,.0f} spent + ${plan.est_notional:,.0f} "
            f"> ${r.max_notional_per_day:,.0f}",
        )

    if ledger.open_trade_count() >= r.max_open_positions:
        return Verdict(False, f"at position limit ({r.max_open_positions})")

    if plan.contracts > r.max_contracts_per_order:
        return Verdict(False, f"{plan.contracts} contracts exceeds per-order cap")

    if not (r.min_days_to_expiry <= (plan.expiration - plan.txn_date).days):
        pass  # informational only; DTE window already applied at candidate time

    if buying_power is not None and plan.est_notional > buying_power:
        return Verdict(False, f"insufficient buying power (${buying_power:,.0f})")

    return Verdict(True)
