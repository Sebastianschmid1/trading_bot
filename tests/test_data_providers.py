"""
Tests für YFinanceResearchProvider (DATA-003, stockbot/market/data_providers.py).

yfinance wird komplett gemockt (Fake-Ticker) — offline, deterministisch. Prüft nur, dass der
Provider die yfinance-Rohdaten korrekt ins MarketDataProvider-Interface übersetzt; die
Korrektheit von yfinance selbst ist nicht Gegenstand dieser Tests.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from stockbot.market import data_providers as dp


class _FakeFastInfo:
    def __init__(self, price):
        self.last_price = price


class _FakeTicker:
    def __init__(self, price=100.0, bars=None, splits=None, dividends=None):
        self._price = price
        self._bars = bars if bars is not None else pd.DataFrame(
            {"Open": [1.0], "High": [1.1], "Low": [0.9], "Close": [1.05], "Volume": [1000]})
        self._splits = splits if splits is not None else pd.Series(dtype=float)
        self._dividends = dividends if dividends is not None else pd.Series(dtype=float)

    @property
    def fast_info(self):
        return _FakeFastInfo(self._price)

    def history(self, **kwargs):
        self.history_kwargs = kwargs
        return self._bars

    @property
    def splits(self):
        return self._splits

    @property
    def dividends(self):
        return self._dividends


class _FakeYF:
    def __init__(self, ticker):
        self._ticker = ticker

    def Ticker(self, symbol):
        return self._ticker


def test_get_bars_uses_period_when_given():
    ticker = _FakeTicker()
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        bars = provider.get_bars("AAPL", interval="1d", period="5d")
    finally:
        dp.yf = orig
    assert isinstance(bars, pd.DataFrame)
    assert ticker.history_kwargs == {"period": "5d", "interval": "1d"}


def test_get_bars_uses_start_end_when_no_period():
    ticker = _FakeTicker()
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        start, end = datetime(2026, 1, 1), datetime(2026, 2, 1)
        provider.get_bars("AAPL", interval="1h", start=start, end=end)
    finally:
        dp.yf = orig
    assert ticker.history_kwargs == {"start": start, "end": end, "interval": "1h"}


def test_get_quote_maps_fast_info_to_quote():
    ticker = _FakeTicker(price=189.5)
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        quote = provider.get_quote("AAPL")
    finally:
        dp.yf = orig
    assert quote.ticker == "AAPL"
    assert quote.price == 189.5
    assert quote.provider == "yfinance_research"
    assert quote.feed == "delayed"


def test_stream_quotes_and_trades_not_implemented():
    provider = dp.YFinanceResearchProvider()
    with pytest.raises(NotImplementedError):
        provider.stream_quotes(["AAPL"])
    with pytest.raises(NotImplementedError):
        provider.stream_trades(["AAPL"])


def test_get_corporate_actions_maps_splits_and_dividends():
    splits = pd.Series([2.0], index=pd.DatetimeIndex([pd.Timestamp("2026-06-01", tz="UTC")]))
    dividends = pd.Series([0.24], index=pd.DatetimeIndex([pd.Timestamp("2026-05-01", tz="UTC")]))
    ticker = _FakeTicker(splits=splits, dividends=dividends)
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        actions = provider.get_corporate_actions("AAPL")
    finally:
        dp.yf = orig
    by_type = {a.action_type: a for a in actions}
    assert by_type["split"].value == 2.0
    assert by_type["dividend"].value == 0.24


def test_get_corporate_actions_filters_by_since():
    splits = pd.Series([2.0], index=pd.DatetimeIndex([pd.Timestamp("2020-01-01", tz="UTC")]))
    ticker = _FakeTicker(splits=splits)
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        actions = provider.get_corporate_actions("AAPL", since=datetime(2025, 1, 1, tzinfo=timezone.utc))
    finally:
        dp.yf = orig
    assert actions == []


def test_get_market_status_reflects_exchange_calendar(monkeypatch):
    provider = dp.YFinanceResearchProvider()
    monkeypatch.setattr(dp.exchange_calendar, "is_market_open", lambda at=None: True)
    status = provider.get_market_status()
    assert status.is_open is True
    assert status.session == "regular"
    assert status.provider == "yfinance_research"

    monkeypatch.setattr(dp.exchange_calendar, "is_market_open", lambda at=None: False)
    status = provider.get_market_status()
    assert status.is_open is False
    assert status.session == "closed"
