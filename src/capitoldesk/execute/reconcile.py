"""Keep the journal honest about what actually happened at the broker.

An order is recorded as 'placed' the moment it is accepted, but Alpaca decides
what becomes of it - filled, partially filled, rejected, expired, canceled.
Without this the journal would report every order as 'placed' forever, and the
dashboard would show trades that never happened.
"""
from __future__ import annotations

import logging

from . import ledger

log = logging.getLogger(__name__)

# Broker states that mean this order will not change again.
TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day", "replaced"}


def sync(broker=None) -> dict[str, int]:
    """Pull broker state for every open order and write it back to the journal."""
    if broker is None:
        from .broker import Broker

        broker = Broker()

    counts: dict[str, int] = {}
    for row in ledger.live_orders():
        try:
            order = broker.trading.get_order_by_id(row["order_id"])
        except Exception as e:  # noqa: BLE001 - a missing order must not stop the sweep
            log.warning("could not fetch order %s: %s", row["order_id"], e)
            continue

        status = getattr(order.status, "value", str(order.status)).lower()
        filled_qty = int(float(order.filled_qty or 0))
        filled_price = float(order.filled_avg_price) if order.filled_avg_price else None
        filled_at = order.filled_at.isoformat() if getattr(order, "filled_at", None) else None

        ledger.update_order_state(
            row["id"], status,
            filled_qty=filled_qty or None,
            filled_price=filled_price,
            filled_at=filled_at,
        )
        counts[status] = counts.get(status, 0) + 1
        if status in TERMINAL:
            log.info(
                "%s %s -> %s (%s @ %s)",
                row["ticker"], row["contract_symbol"], status, filled_qty, filled_price,
            )
    return counts
