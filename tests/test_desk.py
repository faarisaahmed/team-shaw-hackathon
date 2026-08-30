"""Tests for the parts that must never silently drift: parsing and sizing.

The LLM stages are exercised by `desk run`; these cover the deterministic
machinery that bounds real money.
"""
from __future__ import annotations

import datetime as dt

import pytest

from capitoldesk.config import RiskLimits, SETTINGS
from capitoldesk.execute.broker import Contract, Quote
from capitoldesk.extract.models import AssetType, Transaction, TxnType
from capitoldesk.ingest.house import FilingRef
from capitoldesk.strategy.engine import clean_prose


def _ref(doc_id: str) -> FilingRef:
    return FilingRef(
        doc_id=doc_id, last="Doe", first="Jane", suffix=None, prefix="Hon.",
        state_district="CA01", filing_date=dt.date(2026, 8, 21), year=2026,
    )


class TestFilingRef:
    def test_efiled_detection(self):
        # E-filed PDFs carry a text layer; scanned ones need the vision path.
        assert _ref("20035143").is_efiled
        assert not _ref("9116311").is_efiled

    def test_member_name_skips_blank_parts(self):
        assert _ref("1").member_name == "Hon. Jane Doe"

    def test_pdf_url_shape(self):
        assert _ref("20035143").pdf_url.endswith("/ptr-pdfs/2026/20035143.pdf")

    def test_age(self):
        assert _ref("1").age_days(dt.date(2026, 8, 31)) == 10


class TestQuote:
    def test_mid_uses_both_sides(self):
        assert Quote("X", 10.0, 12.0).mid == 11.0

    def test_one_sided_quote_does_not_halve_price(self):
        # The bug that made AMAT read $221 instead of $461.
        assert Quote("X", 443.04, 0.0).mid == 443.04

    def test_spread_pct_penalises_one_sided(self):
        assert Quote("X", 443.04, 0.0).spread_pct == 1.0

    def test_spread_pct(self):
        assert Quote("X", 9.0, 11.0).spread_pct == pytest.approx(0.2)


class TestProseCleaning:
    def test_mangled_emdash(self):
        assert "dash" not in clean_prose("edge intact \ndash we win")

    def test_collapses_whitespace(self):
        assert clean_prose("a  \n  b") == "a b"


class TestSizing:
    """Sizing is arithmetic under hard caps - the model never touches it."""

    def _plan(self, monkeypatch, conviction: float, mid: float, oi: int = 10_000):
        from capitoldesk.strategy import engine
        from capitoldesk.strategy.plan import Decision, Mode, Plan

        st = engine.Strategist.__new__(engine.Strategist)
        contract = Contract("X260101C00100000", "X", "call", 100.0, dt.date(2026, 12, 18), oi, mid)
        quote = Quote("X260101C00100000", mid * 0.98, mid * 1.02)
        txn = Transaction(
            asset_name="X Corp", ticker="X", asset_type=AssetType.STOCK,
            txn_type=TxnType.PURCHASE, txn_date=dt.date(2026, 7, 1),
            amount_min=1001, amount_max=15000,
        )
        from capitoldesk.extract.models import Disclosure

        disc = Disclosure(doc_id="1", member_name="Hon. Jane Doe", filing_date=dt.date(2026, 8, 1))
        d = Decision(action=Mode.SYNTHESIZE, contract_symbol=contract.symbol,
                     conviction=conviction, rationale="test")
        return st._size(disc, txn, contract, quote, d, 105.0, 100.0)

    def test_never_exceeds_per_trade_cap(self, monkeypatch):
        from capitoldesk.strategy.plan import Plan

        res = self._plan(monkeypatch, conviction=1.0, mid=1.00)
        assert isinstance(res, Plan)
        assert res.est_notional <= SETTINGS.risk.max_notional_per_trade

    def test_low_conviction_rejects_expensive_contract(self, monkeypatch):
        from capitoldesk.strategy.plan import Rejection

        # $8,000/contract against a 0.1 * $5,000 = $500 budget.
        res = self._plan(monkeypatch, conviction=0.1, mid=80.0)
        assert isinstance(res, Rejection)
        assert "too expensive" in res.reason

    def test_open_interest_caps_size(self, monkeypatch):
        from capitoldesk.strategy.plan import Plan

        # OI 100 -> at most 5% = 5 contracts, even though the budget allows more.
        res = self._plan(monkeypatch, conviction=1.0, mid=1.00, oi=100)
        assert isinstance(res, Plan)
        assert res.contracts <= 5


class TestRiskLimits:
    def test_paper_only_guard(self):
        from dataclasses import replace

        s = replace(SETTINGS, alpaca_base_url="https://api.alpaca.markets")
        with pytest.raises(SystemExit, match="non-paper"):
            s.require()

    def test_defaults_are_conservative(self):
        r = RiskLimits()
        assert r.max_notional_per_trade < r.max_notional_per_day
        assert r.min_open_interest > 0
        assert 0 < r.max_pct_of_open_interest < 1


class TestTickerResolution:
    """A proposed ticker must be confirmed by the broker before it can trade."""

    def test_unverifiable_ticker_is_dropped(self, monkeypatch):
        from capitoldesk.extract import resolve
        from capitoldesk.extract.models import Disclosure, Transaction, TxnType

        class FakeTrading:
            def get_asset(self, sym):
                raise ValueError("unknown symbol")

        class FakeBroker:
            trading = FakeTrading()

        class FakeStream:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get_final_message(self):
                class M:
                    parsed_output = resolve.Mappings(
                        mappings=[resolve.Mapping(asset_name="Nonesuch Corp", ticker="ZZZZ")]
                    )
                return M()

        class FakeClient:
            class messages:
                @staticmethod
                def stream(**kw): return FakeStream()

        disc = Disclosure(
            doc_id="1", member_name="Hon. Jane Doe", filing_date=dt.date(2026, 8, 1),
            transactions=[Transaction(
                asset_name="Nonesuch Corp", txn_type=TxnType.PURCHASE,
                txn_date=dt.date(2026, 7, 1), amount_min=1001, amount_max=15000,
            )],
        )
        out = resolve.resolve_tickers(disc, broker=FakeBroker(), client=FakeClient())
        assert out.transactions[0].ticker is None

    def test_untradable_asset_is_dropped(self):
        from capitoldesk.extract import resolve
        from capitoldesk.extract.models import Disclosure, Transaction, TxnType

        class Asset:
            tradable = False

        class FakeBroker:
            class trading:
                @staticmethod
                def get_asset(sym): return Asset()

        class FakeStream:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def get_final_message(self):
                class M:
                    parsed_output = resolve.Mappings(
                        mappings=[resolve.Mapping(asset_name="Delisted Co", ticker="DEAD")]
                    )
                return M()

        class FakeClient:
            class messages:
                @staticmethod
                def stream(**kw): return FakeStream()

        disc = Disclosure(
            doc_id="1", member_name="Hon. Jane Doe", filing_date=dt.date(2026, 8, 1),
            transactions=[Transaction(
                asset_name="Delisted Co", txn_type=TxnType.PURCHASE,
                txn_date=dt.date(2026, 7, 1), amount_min=1001, amount_max=15000,
            )],
        )
        out = resolve.resolve_tickers(disc, broker=FakeBroker(), client=FakeClient())
        assert out.transactions[0].ticker is None


class TestLoopState:
    def test_summary_is_readable(self):
        from capitoldesk.desk.loop import LoopState

        s = LoopState(cycles=3, filings_seen=12, orders_placed=2)
        out = s.summary()
        assert "cycles 3" in out and "filings 12" in out and "orders 2" in out


class TestSpreads:
    """A debit spread's max loss is the net debit - that is what gets sized."""

    def _parts(self, long_mid: float, short_mid: float, *, short_strike: float = 120.0,
               short_exp: dt.date = dt.date(2026, 12, 18)):
        from capitoldesk.strategy import engine

        st = engine.Strategist.__new__(engine.Strategist)
        long_c = Contract("L", "X", "call", 100.0, dt.date(2026, 12, 18), 5000, long_mid)
        long_q = Quote("L", long_mid * 0.99, long_mid * 1.01)
        short_c = Contract("S", "X", "call", short_strike, short_exp, 5000, short_mid)
        short_q = Quote("S", short_mid * 0.99, short_mid * 1.01)
        return st, long_c, long_q, (short_c, short_q)

    def _run(self, st, lc, lq, short, conviction=1.0):
        from capitoldesk.extract.models import Disclosure, Transaction, TxnType
        from capitoldesk.strategy.plan import Decision, Mode

        txn = Transaction(
            asset_name="X Corp", ticker="X", txn_type=TxnType.PURCHASE,
            txn_date=dt.date(2026, 7, 1), amount_min=1001, amount_max=15000,
        )
        disc = Disclosure(doc_id="1", member_name="Hon. Jane Doe", filing_date=dt.date(2026, 8, 1))
        d = Decision(action=Mode.REPLICATE, contract_symbol="L", short_leg_symbol="S",
                     conviction=conviction, rationale="test")
        return st._size(disc, txn, lc, lq, d, 105.0, 100.0, short=short)

    def test_spread_sizes_on_net_debit_not_long_premium(self):
        from capitoldesk.strategy.plan import Plan

        # Outright would be $8,000/contract - far over the $5,000 cap.
        # Net debit is $80 - $60 = $20 -> ~$2,100 with the crossing buffer.
        st, lc, lq, short = self._parts(80.0, 60.0)
        res = self._run(st, lc, lq, short)
        assert isinstance(res, Plan), res
        assert res.is_spread and res.short_leg_symbol == "S"
        assert res.est_notional <= SETTINGS.risk.max_notional_per_trade
        assert res.limit_price == pytest.approx(21.0)

    def test_credit_spread_is_rejected(self):
        from capitoldesk.strategy.plan import Rejection

        # Short leg richer than the long - that is a credit, not a debit.
        st, lc, lq, short = self._parts(60.0, 80.0)
        res = self._run(st, lc, lq, short)
        assert isinstance(res, Rejection)
        assert "not a debit" in res.reason

    def test_thinner_leg_governs_open_interest_cap(self):
        from capitoldesk.strategy.plan import Plan

        st, lc, lq, short = self._parts(5.0, 3.0)
        thin = Contract("S", "X", "call", 120.0, dt.date(2026, 12, 18), 40, 3.0)
        res = self._run(st, lc, lq, (thin, short[1]))
        assert isinstance(res, Plan)
        assert res.contracts <= 2  # 5% of 40

    def test_structure_label(self):
        from capitoldesk.strategy.plan import Plan

        st, lc, lq, short = self._parts(80.0, 60.0)
        res = self._run(st, lc, lq, short)
        assert isinstance(res, Plan) and res.structure == "debit spread"
