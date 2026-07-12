"""
Tests für YFinanceResearchProvider + AlpacaPaperMarketDataProvider
(DATA-003, stockbot/market/data_providers.py).

yfinance/alpaca-py werden komplett gemockt (Fake-Clients per Dependency Injection) — offline,
deterministisch, ohne echte Keys/Netzwerk (gleiches Prinzip wie tests/test_broker.py). Prüft nur,
dass die Provider die Rohdaten korrekt ins MarketDataProvider-Interface übersetzen; die
Korrektheit von yfinance/Alpaca selbst ist nicht Gegenstand dieser Tests.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace

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


# ── AlpacaPaperMarketDataProvider — Fake-Clients per Dependency Injection ────

class _FakeBarSet:
    def __init__(self, df):
        self.df = df


class _FakeDataClient:
    """Steht für StockHistoricalDataClient — merkt sich das letzte Request-Objekt fürs Assert."""
    def __init__(self, bars_df=None, quote=None):
        self._bars_df = bars_df if bars_df is not None else pd.DataFrame({"close": [1.0]})
        self._quote = quote
        self.last_bars_request = None
        self.last_quote_request = None

    def get_stock_bars(self, req):
        self.last_bars_request = req
        return _FakeBarSet(self._bars_df)

    def get_stock_latest_quote(self, req):
        self.last_quote_request = req
        return {req.symbol_or_symbols: self._quote}


class _FakeCorporateActionsClient:
    def __init__(self, data):
        self._data = data
        self.last_request = None

    def get_corporate_actions(self, req):
        self.last_request = req
        return SimpleNamespace(data=self._data)


class _FakeTradingClientForClock:
    def __init__(self, is_open):
        self._is_open = is_open

    def get_clock(self):
        return SimpleNamespace(is_open=self._is_open)


def _alpaca_provider(data_client=None, ca_client=None, trading_client=None):
    return dp.AlpacaPaperMarketDataProvider(
        api_key="key", api_secret="secret",
        data_client=data_client or _FakeDataClient(),
        corporate_actions_client=ca_client or _FakeCorporateActionsClient({}),
        trading_client=trading_client)


def test_to_alpaca_timeframe_known_intervals():
    from alpaca.data.timeframe import TimeFrameUnit
    tf = dp._to_alpaca_timeframe("15m")
    assert tf.amount_value == 15 and tf.unit == TimeFrameUnit.Minute
    tf = dp._to_alpaca_timeframe("1d")
    assert tf.amount_value == 1 and tf.unit == TimeFrameUnit.Day


def test_to_alpaca_timeframe_unknown_interval_raises():
    with pytest.raises(ValueError):
        dp._to_alpaca_timeframe("3m")


def test_period_to_start_known_periods():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    start = dp._period_to_start("5d", now=now)
    assert start == now - dp._PERIOD_TO_TIMEDELTA["5d"]


def test_period_to_start_unknown_period_raises():
    with pytest.raises(ValueError):
        dp._period_to_start("2y")


def test_alpaca_get_bars_uses_period_to_compute_start():
    fake_bars = pd.DataFrame({"close": [1.0, 2.0]})
    data_client = _FakeDataClient(bars_df=fake_bars)
    provider = _alpaca_provider(data_client=data_client)
    bars = provider.get_bars("AAPL", interval="1d", period="5d")
    assert bars is fake_bars
    assert data_client.last_bars_request.symbol_or_symbols == "AAPL"
    assert data_client.last_bars_request.start is not None


def test_alpaca_get_quote_maps_bid_ask_to_mid_price():
    quote = SimpleNamespace(bid_price=100.0, ask_price=102.0, timestamp=datetime(2026, 6, 10, tzinfo=timezone.utc))
    data_client = _FakeDataClient(quote=quote)
    provider = _alpaca_provider(data_client=data_client)
    q = provider.get_quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.price == 101.0
    assert q.bid == 100.0 and q.ask == 102.0
    assert q.provider == "alpaca_paper" and q.feed == "iex"


def test_alpaca_stream_quotes_and_trades_not_implemented():
    provider = _alpaca_provider()
    with pytest.raises(NotImplementedError):
        provider.stream_quotes(["AAPL"])
    with pytest.raises(NotImplementedError):
        provider.stream_trades(["AAPL"])


def test_alpaca_get_corporate_actions_maps_splits_and_dividends():
    forward_split = SimpleNamespace(
        corporate_action_type="forward_split", ex_date=date(2026, 6, 1), new_rate=2.0, old_rate=1.0)
    cash_dividend = SimpleNamespace(
        corporate_action_type="cash_dividend", ex_date=date(2026, 5, 1), rate=0.24)
    ca_client = _FakeCorporateActionsClient({
        "forward_splits": [forward_split], "cash_dividends": [cash_dividend],
    })
    provider = _alpaca_provider(ca_client=ca_client)
    actions = provider.get_corporate_actions("AAPL")
    by_type = {a.action_type: a for a in actions}
    assert by_type["forward_splits"].value == 2.0
    assert by_type["forward_splits"].ex_date == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert by_type["cash_dividends"].value == 0.24


def test_alpaca_get_market_status_uses_trading_client_clock():
    provider = _alpaca_provider(trading_client=_FakeTradingClientForClock(is_open=True))
    status = provider.get_market_status()
    assert status.is_open is True and status.session == "regular" and status.provider == "alpaca_paper"

    provider = _alpaca_provider(trading_client=_FakeTradingClientForClock(is_open=False))
    status = provider.get_market_status()
    assert status.is_open is False and status.session == "closed"


def test_alpaca_get_market_status_raises_without_trading_client():
    # api_key/api_secret bewusst leer UND kein injizierter trading_client → broker_client.make_client
    # gibt None zurück (kein echter Netzwerkaufruf), get_market_status muss das klar melden.
    provider = dp.AlpacaPaperMarketDataProvider(
        api_key="", api_secret="",
        data_client=_FakeDataClient(), corporate_actions_client=_FakeCorporateActionsClient({}),
        trading_client=None)
    with pytest.raises(RuntimeError):
        provider.get_market_status()
