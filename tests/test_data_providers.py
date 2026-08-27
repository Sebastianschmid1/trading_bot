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


def _aware_daily_bars(periods=3, tz="America/New_York"):
    """Synthetischer tz-AWARER Daily-Frame, wie ihn yf.Ticker.history()/yf.download() liefern
    (Tages-Index auf Börsen-Lokalzeit lokalisiert)."""
    idx = pd.date_range("2022-01-03", periods=periods, freq="B", tz=tz)
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.05, "Volume": 1000}, index=idx)


def test_get_bars_returns_tz_naive_index_even_when_history_is_aware():
    aware = _aware_daily_bars()
    assert aware.index.tz is not None  # Vorbedingung: yfinance liefert tz-aware
    ticker = _FakeTicker(bars=aware)
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        bars = provider.get_bars("AAPL", interval="1d", period="1y")
    finally:
        dp.yf = orig
    assert isinstance(bars.index, pd.DatetimeIndex)
    assert bars.index.tz is None
    # Handelstag-Datum darf sich durch die Normalisierung NICHT verschieben
    assert [t.date() for t in bars.index] == [t.date() for t in aware.index]


def test_get_bars_leaves_already_naive_index_unchanged():
    naive = pd.DataFrame(
        {"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.05, "Volume": 1000},
        index=pd.date_range("2022-01-03", periods=3, freq="B"))
    ticker = _FakeTicker(bars=naive)
    orig = dp.yf
    dp.yf = _FakeYF(ticker)
    try:
        provider = dp.YFinanceResearchProvider()
        bars = provider.get_bars("AAPL", interval="1d", period="1y")
    finally:
        dp.yf = orig
    assert bars.index.tz is None
    assert list(bars.index) == list(naive.index)


def test_get_bars_batch_returns_tz_naive_index_even_when_download_is_aware():
    # yf.download kann (je nach Version/Multi-Ticker) einen tz-aware Index liefern.
    cols = pd.MultiIndex.from_product(
        [["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]])
    idx = pd.date_range("2022-01-03", periods=3, freq="B", tz="America/New_York")
    frame = pd.DataFrame(1.0, index=idx, columns=cols)

    class _FakeYFDownload:
        @staticmethod
        def download(tickers, **kwargs):
            return frame

    orig = dp.yf
    dp.yf = _FakeYFDownload
    try:
        provider = dp.YFinanceResearchProvider()
        bars = provider.get_bars_batch(["AAPL", "MSFT"], interval="1d", period="1y")
    finally:
        dp.yf = orig
    for ticker in ("AAPL", "MSFT"):
        assert bars[ticker].index.tz is None
        assert [t.date() for t in bars[ticker].index] == [t.date() for t in idx]


def test_strip_tz_naive_preserves_trading_day_and_handles_edge_cases():
    aware = _aware_daily_bars(periods=5)
    out = dp._strip_tz_naive(aware)
    assert out.index.tz is None
    # .date() der naiven == .date() der aware Bars (Kern-Nuance)
    assert [t.date() for t in out.index] == [t.date() for t in aware.index]
    # None / leerer Frame robust
    assert dp._strip_tz_naive(None) is None
    empty = pd.DataFrame(columns=["Open", "Close"])
    assert dp._strip_tz_naive(empty) is empty


def test_normalized_yf_bars_do_not_crash_lab_split():
    # Regression W5.1: tz-aware Daily-Bars ließen lab._split() gegen naive pd.Timestamp crashen
    # (TypeError: Cannot compare tz-naive and tz-aware timestamps).
    from stockbot.optimize import lab
    idx = pd.date_range("2022-01-03", periods=600, freq="B", tz="America/New_York")
    df = pd.DataFrame(
        {"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.05, "Volume": 1000}, index=idx)
    normalized = dp._strip_tz_naive(df)
    data = {"AAA": normalized}
    first, split, last, is_years, oos_years = lab._split(data)  # darf nicht werfen
    assert split.tz is None
    # der ursprüngliche crashende Vergleich läuft jetzt sauber
    assert (pd.Timestamp("2022-06-01") < split) in (True, False)


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


def test_alpaca_get_bars_maps_class_suffix_to_alpaca_dot_symbol():
    # AC1: Ein Bars-Abruf für BRK-B schickt BRK.B an Alpaca.
    fake_bars = pd.DataFrame({"close": [1.0]})
    data_client = _FakeDataClient(bars_df=fake_bars)
    provider = _alpaca_provider(data_client=data_client)
    provider.get_bars("BRK-B", interval="1d", period="5d")
    assert data_client.last_bars_request.symbol_or_symbols == "BRK.B"


def _alpaca_bars_df():
    ts = pd.DatetimeIndex(["2026-06-01 14:30", "2026-06-01 15:30", "2026-06-01 14:30"], tz="UTC")
    idx = pd.MultiIndex.from_arrays(
        [["AAPL", "AAPL", "MSFT"], ts], names=["symbol", "timestamp"])
    return pd.DataFrame(
        {"open": [10, 11, 20], "high": [12, 13, 22], "low": [9, 10, 19],
         "close": [11, 12, 21], "volume": [100, 200, 300], "vwap": [10.5, 11.5, 20.5]},
        index=idx)


def test_alpaca_get_bars_batch_normalizes_multiindex_per_ticker():
    data_client = _FakeDataClient(bars_df=_alpaca_bars_df())
    provider = _alpaca_provider(data_client=data_client)
    bars = provider.get_bars_batch(["AAPL", "MSFT"], interval="1d", period="5d")

    assert set(bars) == {"AAPL", "MSFT"}
    aapl = bars["AAPL"]
    assert list(aapl.columns) == ["Open", "High", "Low", "Close", "Volume"]  # yfinance-förmig
    assert isinstance(aapl.index, pd.DatetimeIndex) and not isinstance(aapl.index, pd.MultiIndex)
    assert len(aapl) == 2 and len(bars["MSFT"]) == 1
    assert float(aapl["Close"].iloc[-1]) == 12.0
    # ein Batch-Request mit der vollständigen Symbolliste
    assert data_client.last_bars_request.symbol_or_symbols == ["AAPL", "MSFT"]


def _alpaca_bars_df_class_suffix():
    ts = pd.DatetimeIndex(["2026-06-01 14:30"], tz="UTC")
    idx = pd.MultiIndex.from_arrays([["BRK.B"], ts], names=["symbol", "timestamp"])
    return pd.DataFrame(
        {"open": [10], "high": [12], "low": [9], "close": [11], "volume": [100]}, index=idx)


def test_alpaca_get_bars_batch_maps_class_suffix_request_and_result_key():
    # AC1: Batch-Bars-Abruf für BRK-B schickt BRK.B an Alpaca; die Antwort (nach BRK.B indiziert)
    # landet im Ergebnis-Dict wieder unter dem Bot-Ticker BRK-B.
    data_client = _FakeDataClient(bars_df=_alpaca_bars_df_class_suffix())
    provider = _alpaca_provider(data_client=data_client)
    bars = provider.get_bars_batch(["BRK-B"], interval="1d", period="5d")
    assert data_client.last_bars_request.symbol_or_symbols == ["BRK.B"]
    assert set(bars) == {"BRK-B"}
    assert float(bars["BRK-B"]["Close"].iloc[0]) == 11.0


def test_normalize_alpaca_bars_strips_tz_from_datetimeindex():
    # Alpaca liefert tz-aware UTC; Zielvertrag ist yfinance-förmig/naive UTC (DB-Zeitvertrag).
    ts = pd.DatetimeIndex(["2026-06-01 14:30", "2026-06-01 15:30"], tz="UTC")
    df = pd.DataFrame(
        {"open": [10, 11], "high": [12, 13], "low": [9, 10],
         "close": [11, 12], "volume": [100, 200]},
        index=ts)
    out = dp._normalize_alpaca_bars(df)
    assert out.index.tz is None
    # Zeitwerte bleiben als UTC-Wanduhr erhalten
    assert list(out.index) == [pd.Timestamp("2026-06-01 14:30"), pd.Timestamp("2026-06-01 15:30")]


def test_normalize_alpaca_bars_strips_tz_from_multiindex():
    ts = pd.DatetimeIndex(["2026-06-01 14:30", "2026-06-01 15:30"], tz="UTC")
    idx = pd.MultiIndex.from_arrays(
        [["AAPL", "AAPL"], ts], names=["symbol", "timestamp"])
    df = pd.DataFrame(
        {"open": [10, 11], "high": [12, 13], "low": [9, 10],
         "close": [11, 12], "volume": [100, 200]},
        index=idx)
    out = dp._normalize_alpaca_bars(df)
    assert not isinstance(out.index, pd.MultiIndex)
    assert out.index.tz is None
    assert list(out.index) == [pd.Timestamp("2026-06-01 14:30"), pd.Timestamp("2026-06-01 15:30")]


def test_alpaca_get_bars_batch_empty_tickers_is_noop():
    provider = _alpaca_provider()
    assert provider.get_bars_batch([], interval="1d", period="5d") == {}


def test_alpaca_get_bars_batch_raises_without_data_client():
    provider = dp.AlpacaPaperMarketDataProvider(
        api_key="", api_secret="", data_client=None,
        corporate_actions_client=_FakeCorporateActionsClient({}), trading_client=None)
    with pytest.raises(RuntimeError):
        provider.get_bars_batch(["AAPL"], interval="1d", period="5d")


def test_yfinance_get_bars_batch_splits_grouped_download():
    cols = pd.MultiIndex.from_product(
        [["AAPL", "MSFT"], ["Open", "High", "Low", "Close", "Volume"]])
    idx = pd.DatetimeIndex(["2026-06-01", "2026-06-02"])
    frame = pd.DataFrame(1.0, index=idx, columns=cols)

    class _FakeYFDownload:
        @staticmethod
        def download(tickers, **kwargs):
            _FakeYFDownload.kwargs = kwargs
            return frame

    orig = dp.yf
    dp.yf = _FakeYFDownload
    try:
        provider = dp.YFinanceResearchProvider()
        bars = provider.get_bars_batch(["AAPL", "MSFT"], interval="1d", period="1mo", prepost=False)
    finally:
        dp.yf = orig
    assert set(bars) == {"AAPL", "MSFT"}
    assert list(bars["AAPL"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert _FakeYFDownload.kwargs["prepost"] is False


def test_alpaca_get_quote_maps_bid_ask_to_mid_price():
    quote = SimpleNamespace(bid_price=100.0, ask_price=102.0, timestamp=datetime(2026, 6, 10, tzinfo=timezone.utc))
    data_client = _FakeDataClient(quote=quote)
    provider = _alpaca_provider(data_client=data_client)
    q = provider.get_quote("AAPL")
    assert q.ticker == "AAPL"
    assert q.price == 101.0
    assert q.bid == 100.0 and q.ask == 102.0
    assert q.provider == "alpaca_paper" and q.feed == "iex"


def test_alpaca_get_quote_maps_class_suffix_to_alpaca_dot_symbol():
    # AC1: Ein Quote-Abruf für BRK-B schickt BRK.B an Alpaca; die zurückgegebene Quote trägt
    # weiter den Bot-Ticker (Bindestrich).
    quote = SimpleNamespace(bid_price=100.0, ask_price=102.0,
                            timestamp=datetime(2026, 6, 10, tzinfo=timezone.utc))
    data_client = _FakeDataClient(quote=quote)
    provider = _alpaca_provider(data_client=data_client)
    q = provider.get_quote("BRK-B")
    assert data_client.last_quote_request.symbol_or_symbols == "BRK.B"
    assert q.ticker == "BRK-B"
    assert q.price == 101.0


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


def test_alpaca_get_corporate_actions_raises_without_ca_client():
    # api_key/api_secret bewusst leer → _build_ca_client liefert None (kein echter
    # Netzwerkaufruf), get_corporate_actions muss das klar melden statt mit AttributeError
    # auf None zu knallen.
    provider = dp.AlpacaPaperMarketDataProvider(
        api_key="", api_secret="",
        data_client=_FakeDataClient(), corporate_actions_client=None, trading_client=None)
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY.*ALPACA_API_SECRET"):
        provider.get_corporate_actions("AAPL")


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


# ── BROKER-TIMEOUTS: Marktdaten-/Corporate-Actions-Client bekommen dasselbe HTTP-Timeout ────
#
# Kein Dependency-Injection-Fake hier — bewusst die echten `_build_data_client`/`_build_ca_client`
# durchlaufen (kein Netzwerkaufruf beim Konstruieren), damit geprüft wird, was tatsächlich an den
# echten alpaca-py-Client übergeben wird (siehe broker/client.py::apply_http_timeout).

def test_alpaca_data_client_gets_timeout_session():
    provider = dp.AlpacaPaperMarketDataProvider(api_key="key", api_secret="secret")
    assert isinstance(provider._data_client._session, dp.broker_client._TimeoutSession)
    assert provider._data_client._session._default_timeout == (
        dp.config.ALPACA_CONNECT_TIMEOUT, dp.config.ALPACA_READ_TIMEOUT)


def test_alpaca_corporate_actions_client_gets_timeout_session():
    provider = dp.AlpacaPaperMarketDataProvider(api_key="key", api_secret="secret")
    assert isinstance(provider._ca_client._session, dp.broker_client._TimeoutSession)
    assert provider._ca_client._session._default_timeout == (
        dp.config.ALPACA_CONNECT_TIMEOUT, dp.config.ALPACA_READ_TIMEOUT)
