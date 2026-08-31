"""Thin, deterministic wrapper over Alpaca. No LLM reasoning lives in here.

Everything that touches money is plain code so it can be unit-tested and so a
model cannot talk its way into a different position size.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import OptionSnapshotRequest, StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus, ContractType, OrderSide, TimeInForce
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest

from ..config import SETTINGS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Contract:
    symbol: str
    underlying: str
    right: str
    strike: float
    expiration: dt.date
    open_interest: int
    close_price: float | None

    @property
    def dte(self) -> int:
        return (self.expiration - dt.date.today()).days


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    delta: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.ask or self.bid

    @property
    def spread_pct(self) -> float:
        """Bid/ask spread as a fraction of mid. Our main liquidity screen."""
        m = self.mid
        if not m or self.bid <= 0 or self.ask <= 0:
            return 1.0
        return (self.ask - self.bid) / m


class Broker:
    def __init__(self) -> None:
        SETTINGS.require(need_llm=False)
        self.trading = TradingClient(
            SETTINGS.alpaca_key, SETTINGS.alpaca_secret, paper=SETTINGS.paper
        )
        self.options = OptionHistoricalDataClient(SETTINGS.alpaca_key, SETTINGS.alpaca_secret)
        self.stocks = StockHistoricalDataClient(SETTINGS.alpaca_key, SETTINGS.alpaca_secret)

    # ---------- account ----------

    def account(self):
        return self.trading.get_account()

    def positions(self):
        return self.trading.get_all_positions()

    def option_positions(self):
        return [p for p in self.positions() if len(p.symbol) > 6 and p.symbol[-9:].isdigit()]

    # ---------- market data ----------

    def stock_price(self, symbol: str) -> float | None:
        """Last traded price, with quote/bar fallbacks.

        Deliberately NOT the quote midpoint. The free IEX feed regularly returns
        one-sided quotes (ask=0), and a naive (bid+ask)/2 then reports exactly
        half the true price - which silently poisons every downstream strike
        selection and signal-decay calculation.
        """
        from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
        from alpaca.data.timeframe import TimeFrame

        try:
            t = self.stocks.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbol)
            )[symbol]
            if t.price and t.price > 0:
                return float(t.price)
        except Exception as e:  # noqa: BLE001
            log.debug("no last trade for %s: %s", symbol, e)

        try:
            q = self.stocks.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol)
            )[symbol]
            if q.bid_price > 0 and q.ask_price > 0:
                return (q.bid_price + q.ask_price) / 2
        except Exception as e:  # noqa: BLE001
            log.debug("no quote for %s: %s", symbol, e)

        try:
            bars = self.stocks.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=dt.date.today() - dt.timedelta(days=10),
                )
            )
            rows = bars.data.get(symbol) or []
            if rows:
                return float(rows[-1].close)
        except Exception as e:  # noqa: BLE001
            log.warning("no price at all for %s: %s", symbol, e)
        return None

    def find_contracts(
        self,
        underlying: str,
        *,
        right: str = "call",
        min_dte: int | None = None,
        max_dte: int | None = None,
        strike_low: float | None = None,
        strike_high: float | None = None,
        limit: int = 200,
    ) -> list[Contract]:
        """List tradable contracts matching a window."""
        today = dt.date.today()
        r = SETTINGS.risk
        min_dte = r.min_days_to_expiry if min_dte is None else min_dte
        max_dte = r.max_days_to_expiry if max_dte is None else max_dte

        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            type=ContractType.CALL if right == "call" else ContractType.PUT,
            expiration_date_gte=today + dt.timedelta(days=min_dte),
            expiration_date_lte=today + dt.timedelta(days=max_dte),
            strike_price_gte=str(strike_low) if strike_low else None,
            strike_price_lte=str(strike_high) if strike_high else None,
            limit=limit,
        )
        try:
            resp = self.trading.get_option_contracts(req)
        except Exception as e:  # noqa: BLE001
            log.warning("contract lookup failed for %s: %s", underlying, e)
            return []

        out: list[Contract] = []
        for c in resp.option_contracts or []:
            out.append(
                Contract(
                    symbol=c.symbol,
                    underlying=c.underlying_symbol,
                    right=c.type.value,
                    strike=float(c.strike_price),
                    expiration=c.expiration_date,
                    open_interest=int(c.open_interest or 0),
                    close_price=float(c.close_price) if c.close_price else None,
                )
            )
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        out: dict[str, Quote] = {}
        # The snapshot endpoint caps how many symbols it will take per call.
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            try:
                snaps = self.options.get_option_snapshot(
                    OptionSnapshotRequest(symbol_or_symbols=chunk)
                )
            except Exception as e:  # noqa: BLE001
                log.warning("snapshot failed: %s", e)
                continue
            for sym, s in (snaps or {}).items():
                q = getattr(s, "latest_quote", None)
                if not q:
                    continue
                g = getattr(s, "greeks", None)
                out[sym] = Quote(
                    sym,
                    float(q.bid_price or 0),
                    float(q.ask_price or 0),
                    delta=float(g.delta) if g and g.delta is not None else None,
                    theta=float(g.theta) if g and g.theta is not None else None,
                    vega=float(g.vega) if g and g.vega is not None else None,
                    iv=float(s.implied_volatility) if getattr(s, "implied_volatility", None) else None,
                )
        return out

    # ---------- execution ----------

    def buy_to_open(
        self, symbol: str, qty: int, limit_price: float, *, client_order_id: str | None = None
    ):
        """Marketable limit order. We never send naked market orders on options -
        wide spreads on illiquid strikes make that genuinely dangerous."""
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            type="limit",
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            client_order_id=client_order_id,
        )
        return self.trading.submit_order(req)

    # ---------- history (signal decay) ----------

    def price_on(self, symbol: str, day: dt.date) -> float | None:
        """Closing price on/near `day`. Used to measure how far a name has
        already moved since the congressperson actually traded it."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        try:
            bars = self.stocks.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=day - dt.timedelta(days=7),
                    end=day + dt.timedelta(days=1),
                )
            )
            rows = bars.data.get(symbol) or []
            return float(rows[-1].close) if rows else None
        except Exception as e:  # noqa: BLE001
            log.warning("no history for %s @ %s: %s", symbol, day, e)
            return None

    def close_option_position(self, symbol: str):
        """Liquidate a held option position at market."""
        return self.trading.close_position(symbol)

    def buy_debit_spread(
        self,
        long_symbol: str,
        short_symbol: str,
        qty: int,
        net_debit_limit: float,
        *,
        client_order_id: str | None = None,
    ):
        """Vertical debit spread: buy the lower strike, sell the higher.

        Max loss is the net debit paid, which is why this is the right structure
        when an outright call costs more than the per-trade risk budget allows.
        """
        from alpaca.trading.enums import OrderClass, PositionIntent
        from alpaca.trading.requests import OptionLegRequest

        req = LimitOrderRequest(
            qty=qty,
            side=OrderSide.BUY,
            type="limit",
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.MLEG,
            limit_price=round(net_debit_limit, 2),
            client_order_id=client_order_id,
            legs=[
                OptionLegRequest(
                    symbol=long_symbol, ratio_qty=1,
                    side=OrderSide.BUY, position_intent=PositionIntent.BUY_TO_OPEN,
                ),
                OptionLegRequest(
                    symbol=short_symbol, ratio_qty=1,
                    side=OrderSide.SELL, position_intent=PositionIntent.SELL_TO_OPEN,
                ),
            ],
        )
        return self.trading.submit_order(req)
