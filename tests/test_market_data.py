"""
Tests für das MarketDataProvider-Interface (DATA-003, stockbot/core/market_data.py, Plan.md §10.2).

Reine Konstruktions-/Vertragstests — kein IO, keine echte Provider-Implementierung (folgt mit
dem nächsten Checklisten-Punkt). Prüft, dass das Interface als ABC durchgesetzt wird und die
Wertobjekte die in Plan.md §10.3 geforderten Felder (Provider, Feed, Abrufzeit, Exchange-Zeit)
tragen.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from stockbot.core import market_data as md


def test_market_data_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        md.MarketDataProvider()


class _StubProvider(md.MarketDataProvider):
    """Minimale konkrete Implementierung — beweist, dass ein vollständiger Provider
    instanziierbar ist (die abstrakten Methoden sind kein toter Code)."""

    def get_bars(self, ticker, *, interval, period=None, start=None, end=None):
        return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                             "Close": [1.0], "Volume": [100]})

    def get_quote(self, ticker):
        now = datetime.now(timezone.utc)
        return md.Quote(ticker=ticker, price=100.0, as_of=now, fetched_at=now, provider="stub")

    def stream_quotes(self, tickers):
        return iter(())

    def stream_trades(self, tickers):
        return iter(())

    def get_corporate_actions(self, ticker, *, since=None):
        return []

    def get_market_status(self):
        return md.MarketStatus(is_open=True, session="regular",
                               as_of=datetime.now(timezone.utc), provider="stub")


def test_stub_provider_implements_full_interface():
    provider = _StubProvider()
    bars = provider.get_bars("AAPL", interval="1d", period="5d")
    assert isinstance(bars, pd.DataFrame)
    assert list(bars.columns) == ["Open", "High", "Low", "Close", "Volume"]

    quote = provider.get_quote("AAPL")
    assert quote.ticker == "AAPL" and quote.price == 100.0 and quote.provider == "stub"

    assert list(provider.stream_quotes(["AAPL"])) == []
    assert list(provider.stream_trades(["AAPL"])) == []
    assert provider.get_corporate_actions("AAPL") == []

    status = provider.get_market_status()
    assert status.is_open is True and status.session == "regular"


def test_incomplete_provider_missing_one_method_cannot_be_instantiated():
    class _Incomplete(md.MarketDataProvider):
        def get_bars(self, ticker, *, interval, period=None, start=None, end=None):
            return pd.DataFrame()
        def get_quote(self, ticker):
            raise NotImplementedError
        def stream_quotes(self, tickers):
            return iter(())
        def stream_trades(self, tickers):
            return iter(())
        def get_corporate_actions(self, ticker, *, since=None):
            return []
        # get_market_status fehlt absichtlich

    with pytest.raises(TypeError):
        _Incomplete()


def test_quote_carries_provenance_fields_for_data_002_plan_section_10_3():
    now = datetime.now(timezone.utc)
    q = md.Quote(ticker="AAPL", price=189.5, as_of=now, fetched_at=now,
                provider="yfinance_research", feed="delayed")
    assert q.provider == "yfinance_research" and q.feed == "delayed"
    assert q.bid is None and q.ask is None


def test_quote_is_immutable():
    now = datetime.now(timezone.utc)
    q = md.Quote(ticker="AAPL", price=1.0, as_of=now, fetched_at=now, provider="stub")
    with pytest.raises(Exception):
        q.price = 2.0


def test_corporate_action_defaults():
    ca = md.CorporateAction(ticker="AAPL", action_type="split", ex_date=datetime.now(timezone.utc))
    assert ca.value is None and ca.detail == ""


def test_market_status_fields():
    status = md.MarketStatus(is_open=False, session="closed",
                             as_of=datetime.now(timezone.utc), provider="alpaca_paper")
    assert status.is_open is False and status.session == "closed" and status.provider == "alpaca_paper"
