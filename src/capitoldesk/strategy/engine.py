"""Disclosure -> options plan.

Division of labour, deliberately strict:

  code  generates real candidate contracts (live strikes, quotes, OI, spread)
  LLM   picks one and assigns conviction, with market context
  code  converts conviction into a position size and enforces hard caps

The model never computes a position size and never sees a way around the caps.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import anthropic

from ..config import SETTINGS
from ..extract.models import AssetType, Disclosure, Transaction, TxnType
from ..execute.broker import Broker, Contract, Quote
from .plan import Decision, Mode, Plan, Rejection

log = logging.getLogger(__name__)

MAX_CANDIDATES = 16

# Models occasionally emit a mangled em-dash escape inside JSON string values,
# which surfaces as a literal "\ndash" in the prose. Normalise it rather than
# shipping broken text into the journal and the demo console.
_PROSE_FIXES = {"\ndash": " - ", "\nmdash": " - ", "&mdash;": " - ", "\u2014": " - "}
# An unpaired double quote wedged between two lowercase words is a mangled dash,
# not quotation: 'family trust "a routine allocation'.
_STRAY_QUOTE = re.compile(r'(?<=[a-z,\)]) "(?=[a-z])')


def clean_prose(text: str) -> str:
    if not text:
        return text
    for bad, good in _PROSE_FIXES.items():
        text = text.replace(bad, good)
    if text.count('"') % 2 == 1:
        text = _STRAY_QUOTE.sub(" - ", text)
    return " ".join(text.split())

SYSTEM = """\
You are the strategist for an options desk that mirrors publicly disclosed \
congressional stock trades (STOCK Act Periodic Transaction Reports).

You are given one disclosed transaction and a list of REAL, currently tradable \
option contracts with live quotes. Pick at most one contract, or skip.

How to think about it:

1. Disclosures are stale by design - members have up to 45 days to file. Always \
check how far the underlying has already moved since the transaction date. If the \
name has already made a large move in the filer's direction, most of the edge is \
gone: either skip, or cut conviction hard and prefer a nearer-dated / less \
extended strike. Set signal_decayed=true when this applies.
2. Match the filer's expressed structure when the filing names one. Several \
members buy deep in-the-money LEAPS as stock replacement (high delta, low theta). \
Replicating that style matters more than hitting the exact strike, which may no \
longer be sensibly priced.
3. For a plain stock purchase, the filer bought SHARES: delta 1.00, no theta, no \
vega. Your job is to approximate that, not to buy a lottery ticket and not to buy \
volatility. Strongly prefer in-the-money calls with delta >= 0.70 and low time \
value as a share of premium. An at-the-money call at high IV is mostly extrinsic: \
it bleeds theta daily and loses money if IV falls even when you are right on \
direction - risks the filer never took. If the only tradable contracts are ATM or \
OTM with high IV and heavy time value, that is a good reason to skip rather than \
a reason to settle.
4. Liquidity is a hard practical constraint. Prefer higher open interest and \
tighter spreads. A great thesis in an untradable contract is worth nothing.
5. A disclosed SALE is a much weaker signal than a purchase. Members sell to rebalance, to satisfy ethics guidance, for tax reasons, or because a blind trust churned - none of which is a bearish view. Only express a sale as long puts when the filing looks genuinely informative (a large, concentrated, decisive exit), and skip otherwise.
6. Conviction should reflect: size of the disclosed trade, freshness, how much \
move is left, and liquidity. Be honest and use the full 0-1 range. Most trades \
should not be 1.0.

Skip freely. Not trading is a valid, common, and often correct answer."""


def _fmt_candidates(cands: list[tuple[Contract, Quote]], spot: float | None) -> str:
    lines = []
    for c, q in cands:
        money = f"{(c.strike / spot - 1) * 100:+.0f}%" if spot else "?"
        # Intrinsic vs extrinsic makes the stock-replacement question concrete.
        intrinsic = max(0.0, spot - c.strike) if c.right == "call" else max(0.0, c.strike - spot)
        extrinsic_pct = ((q.mid - intrinsic) / q.mid * 100) if q.mid > 0 else 100.0
        lines.append(
            f"  {c.symbol}  strike ${c.strike:<8.2f} exp {c.expiration} "
            f"dte {c.dte:<4} strike_vs_spot {money:>6}  "
            f"mid ${q.mid:<8.2f} spread {q.spread_pct * 100:>5.1f}%  OI {c.open_interest:<6} "
            f"delta {q.delta if q.delta is not None else float('nan'):.2f}  "
            f"theta {q.theta if q.theta is not None else float('nan'):+.3f}  "
            f"IV {q.iv * 100 if q.iv else float('nan'):.0f}%  "
            f"time-value {extrinsic_pct:.0f}% of premium"
        )
    return "\n".join(lines)


class Strategist:
    def __init__(self, broker: Broker | None = None) -> None:
        self.broker = broker or Broker()
        self.client = anthropic.Anthropic(api_key=SETTINGS.anthropic_key)

    # ---------- candidate generation (pure code) ----------

    def _candidates(self, txn: Transaction, spot: float) -> list[tuple[Contract, Quote]]:
        r = SETTINGS.risk
        od = txn.option_detail

        if od and od.strike and od.expiration:
            # Replicate: search around the disclosed strike and expiry.
            lo, hi = od.strike * 0.75, max(od.strike * 1.35, spot * 1.1)
            min_dte = max(r.min_days_to_expiry, (od.expiration - dt.date.today()).days - 120)
            max_dte = min(r.max_days_to_expiry, (od.expiration - dt.date.today()).days + 120)
        else:
            # Synthesize: bracket the spot, from moderately ITM to modestly OTM.
            lo, hi = spot * 0.75, spot * 1.20
            min_dte, max_dte = r.min_days_to_expiry, r.max_days_to_expiry

        right = (od.right if od and od.right else "call")
        if txn.txn_type in (TxnType.SALE, TxnType.PARTIAL_SALE):
            right = "put"

        contracts = self.broker.find_contracts(
            txn.ticker,
            right=right,
            min_dte=max(min_dte, r.min_days_to_expiry),
            max_dte=min(max_dte, r.max_days_to_expiry),
            strike_low=lo,
            strike_high=hi,
        )
        liquid = [c for c in contracts if c.open_interest >= r.min_open_interest]
        if not liquid:
            return []

        quotes = self.broker.quotes([c.symbol for c in liquid])
        pairs = [
            (c, quotes[c.symbol])
            for c in liquid
            if c.symbol in quotes
            and quotes[c.symbol].mid > 0
            and quotes[c.symbol].spread_pct <= SETTINGS.risk.max_spread_pct
        ]
        # Keep a spread of strikes/expiries rather than 200 near-identical rows.
        pairs.sort(key=lambda p: (abs(p[0].strike - spot), p[0].dte))
        return pairs[:MAX_CANDIDATES]

    # ---------- decision (LLM) ----------

    def plan_for(
        self, disc: Disclosure, txn: Transaction
    ) -> Plan | Rejection:
        if not txn.ticker:
            return Rejection(doc_id=disc.doc_id, ticker=None, reason="no ticker disclosed")
        if txn.asset_type not in (AssetType.STOCK, AssetType.OPTION):
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason=f"asset type {txn.asset_type} is not tradable as equity options",
            )
        is_sale = txn.txn_type in (TxnType.SALE, TxnType.PARTIAL_SALE)
        if is_sale and not SETTINGS.risk.mirror_sales_as_puts:
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason="disclosed sale; put-mirroring disabled (exit-on-sale still applies)",
            )
        if txn.txn_type == TxnType.EXCHANGE:
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason="exchange has no clean directional read",
            )

        spot = self.broker.stock_price(txn.ticker)
        if not spot:
            return Rejection(doc_id=disc.doc_id, ticker=txn.ticker, reason="no live quote")

        cands = self._candidates(txn, spot)
        if not cands:
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason="no liquid contracts in the target window",
            )

        at_txn = self.broker.price_on(txn.ticker, txn.txn_date)
        move = f"{(spot / at_txn - 1) * 100:+.1f}%" if at_txn else "unknown"
        od = txn.option_detail

        prompt = f"""\
DISCLOSED TRANSACTION
  Filer            : {disc.member_name} ({disc.state_district})
  Filed            : {disc.filing_date}  ({(dt.date.today() - disc.filing_date).days} days ago)
  Transaction date : {txn.txn_date}  ({(dt.date.today() - txn.txn_date).days} days ago)
  Asset            : {txn.asset_name} ({txn.ticker}) [{txn.asset_type.value if txn.asset_type else '?'}]
  Action           : {txn.txn_type.value}
  Disclosed amount : ${txn.amount_min:,.0f} - ${txn.amount_max:,.0f}
  Owner            : {txn.owner.value if txn.owner else 'SELF'}
  Filing note      : {txn.description or '(none)'}
  Explicit contract: {f'{od.contracts}x {od.right} @ ${od.strike} exp {od.expiration}' if od else '(none - stock trade)'}

MARKET NOW
  {txn.ticker} spot at transaction date : {f'${at_txn:,.2f}' if at_txn else 'unknown'}
  {txn.ticker} spot now                 : ${spot:,.2f}
  Move since transaction                : {move}

TRADABLE CANDIDATES (live quotes, per-contract prices; multiply by 100 for cost)
{_fmt_candidates(cands, spot)}

Choose one contract_symbol from the list above, or skip."""

        resp = self.client.messages.parse(
            model=SETTINGS.model,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Decision,
        )
        d: Decision = resp.parsed_output
        d.rationale = clean_prose(d.rationale)

        if d.action == Mode.SKIP or not d.contract_symbol:
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason=f"strategist skipped: {d.rationale}",
            )

        chosen = next((p for p in cands if p[0].symbol == d.contract_symbol), None)
        if chosen is None:
            # The model must pick from the list it was given; anything else is a
            # hallucinated symbol and we refuse it rather than look it up.
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason=f"strategist returned off-list symbol {d.contract_symbol!r}",
            )

        contract, quote = chosen
        return self._size(disc, txn, contract, quote, d, spot, at_txn)

    # ---------- sizing (pure code, hard-bounded) ----------

    def _size(
        self,
        disc: Disclosure,
        txn: Transaction,
        contract: Contract,
        quote: Quote,
        d: Decision,
        spot: float,
        at_txn: float | None,
    ) -> Plan | Rejection:
        r = SETTINGS.risk
        # Pay up to the ask to get filled, but never above it.
        limit = round(quote.mid + (quote.ask - quote.mid) * 0.6, 2) or quote.ask
        per_contract = limit * 100
        if per_contract <= 0:
            return Rejection(doc_id=disc.doc_id, ticker=txn.ticker, reason="no valid price")

        budget = r.max_notional_per_trade * d.conviction
        qty = int(budget // per_contract)
        qty = min(qty, r.max_contracts_per_order)
        # Never take a meaningful share of a contract's open interest.
        qty = min(qty, max(1, int(contract.open_interest * r.max_pct_of_open_interest)))

        if qty < 1:
            return Rejection(
                doc_id=disc.doc_id, ticker=txn.ticker,
                reason=(
                    f"contract too expensive for the risk budget: "
                    f"${per_contract:,.0f}/contract vs ${budget:,.0f} allotted "
                    f"(conviction {d.conviction:.2f})"
                ),
            )

        return Plan(
            doc_id=disc.doc_id,
            member=disc.member_name,
            ticker=txn.ticker,
            mode=d.action,
            contract_symbol=contract.symbol,
            right=contract.right,
            strike=contract.strike,
            expiration=contract.expiration,
            contracts=qty,
            limit_price=limit,
            est_notional=qty * per_contract,
            conviction=d.conviction,
            rationale=d.rationale,
            signal_decayed=d.signal_decayed,
            txn_date=txn.txn_date,
            filing_date=disc.filing_date,
            disclosed_min=txn.amount_min,
            disclosed_max=txn.amount_max,
            underlying_at_txn=at_txn,
            underlying_now=spot,
        )
