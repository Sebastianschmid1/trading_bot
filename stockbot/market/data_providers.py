"""Konkrete `MarketDataProvider`-Implementierungen (Phase 2 / DATA-003, siehe
docs/Plan.md §10.2, docs/PLAN_CHECKLIST.md Phase 2).

`YFinanceResearchProvider` bündelt die bislang direkt und uneinheitlich über mehrere Module
verstreuten `yfinance`-Aufrufe (`market/analyzer.py`, `core/evaluator.py`, `market/smartmoney.py`,
`market/lookup.py`, `core/db.py`) hinter dem `MarketDataProvider`-Interface — als Research-/
Backtest-Provider, NICHT für den Produktionssignalpfad (Leitplanke „kein yfinance im
Produktionssignalpfad", siehe Definition of Done).

`AlpacaPaperMarketDataProvider` nutzt die bereits vorhandene `alpaca-py`-Abhängigkeit
(`StockHistoricalDataClient`/`CorporateActionsClient`/`TradingClient.get_clock()`) — dieselben
Zugangsdaten wie `stockbot/broker/client.py`, aber ein eigener, IO-isolierter Marktdaten-Client
(Order-Ausführung und Marktdaten bleiben getrennte Verantwortlichkeiten). Später
`LicensedProductionProvider` als eigener, separater Checklisten-Punkt.

Noch von KEINEM Live-Codepfad genutzt — die bestehenden Aufrufer weiterhin auf ein
`MarketDataProvider` umzustellen ist ein eigener, größerer Migrationsschritt (Format/Verhalten
muss pro Aufrufer geprüft werden) und folgt separat.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from stockbot import config
from stockbot.broker import client as broker_client
from stockbot.core import exchange_calendar
from stockbot.core.market_data import CorporateAction, MarketDataProvider, MarketStatus, Quote


# yfinance-förmige OHLCV-Spalten (Capitalized) — Zielformat für den Signalpfad (analyzer).
_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
_ALPACA_BAR_COLUMN_MAP = {"open": "Open", "high": "High", "low": "Low",
                          "close": "Close", "volume": "Volume"}


def _normalize_alpaca_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Alpaca-`.df` (kleingeschriebene Spalten, ggf. (symbol, timestamp)-MultiIndex) → yfinance-
    förmig: Spalten Open/High/Low/Close/Volume mit reinem `DatetimeIndex`."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    out = df.rename(columns=_ALPACA_BAR_COLUMN_MAP)
    out = out[[c for c in _OHLCV_COLUMNS if c in out.columns]]
    idx = out.index
    if isinstance(idx, pd.MultiIndex):
        idx = idx.get_level_values(-1)   # letzte Ebene ist der Zeitstempel
    idx = pd.DatetimeIndex(idx)
    # Zielvertrag ist yfinance-förmig (Daily-Index war naiv) und der Rest des Codes rechnet
    # durchgehend mit naiver UTC (DB-Zeitvertrag). Alpaca liefert dagegen tz-aware UTC → hier auf
    # naive UTC ziehen, sonst knallt jeder Vergleich mit einer naiven Zeit (tz-naive vs tz-aware).
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out.index = idx
    return out


def _strip_tz_naive(df: pd.DataFrame) -> pd.DataFrame:
    """Zieht einen tz-AWAREN DatetimeIndex auf tz-naiv, damit die yfinance-Frames dem gleichen
    Zeitvertrag folgen wie der Alpaca-Pfad (durchgehend naive Zeitstempel; sonst knallt jeder
    Vergleich mit einer naiven Zeit — tz-naive vs tz-aware, so crasht das Labor seit W5.1).

    Bewusst `tz_localize(None)` (tz fallen lassen, Wanduhr behalten) statt
    `tz_convert("UTC").tz_localize(None)` wie im Alpaca-Zweig: yfinance-**Tages**-Bars sind auf
    die BÖRSEN-Lokalzeit lokalisiert (z. B. 00:00 America/New_York). Downstream (optimize/lab.py)
    nutzt für Daily nur `df.index[i].date()` — der lokale Handelstag darf sich durch die
    Normalisierung NICHT verschieben. `tz_localize(None)` erhält den Kalender-Handelstag garantiert
    für JEDE Börse; `tz_convert("UTC")` würde Bars östlich von UTC (00:00 lokal → Vortag) auf den
    falschen Kalendertag ziehen. Bereits naive Indizes bleiben unverändert; leere/None-Frames robust.
    """
    if df is None or len(df) == 0:
        return df
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        df = df.copy()
        df.index = idx.tz_localize(None)
    return df


def _extract_yf_ticker(data, ticker: str):
    """Holt das OHLCV-Sub-DataFrame eines Tickers aus einem (ggf. gruppierten) yfinance-Batch."""
    if data is None:
        return None
    try:
        return data[ticker]
    except (KeyError, TypeError):
        cols = getattr(data, "columns", None)
        if cols is None:
            return None
        level0 = cols.get_level_values(0) if isinstance(cols, pd.MultiIndex) else cols
        return data if "Close" in level0 else None


class YFinanceResearchProvider(MarketDataProvider):
    """Research-/Backtest-Provider auf Basis von `yfinance` (kostenlos, verzögerte Daten).

    Pull-only — `yfinance` bietet kein Echtzeit-Streaming; `stream_quotes`/`stream_trades`
    lehnen daher explizit ab, statt eine irreführende Endlosschleife über Polling zu simulieren.
    NUR für Research/Backtest/Anzeige — NIE für den Produktionssignalpfad (Leitplanke W3.2).
    """

    provider_name = "yfinance_research"

    def get_bars(self, ticker: str, *, interval: str, period: str | None = None,
                 start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        t = yf.Ticker(ticker)
        if period is not None:
            return _strip_tz_naive(t.history(period=period, interval=interval))
        return _strip_tz_naive(t.history(start=start, end=end, interval=interval))

    def get_bars_batch(self, tickers: list[str], *, interval: str, period: str | None = None,
                       prepost: bool = True) -> dict[str, pd.DataFrame]:
        """Ein Batch-`yf.download` je Timeframe (threadsicher zusammengefügt), pro Ticker auf
        yfinance-förmige OHLCV-Frames aufgeteilt. Nur Research/Anzeige, nicht Prod-Signal."""
        if not tickers:
            return {}
        data = yf.download(list(tickers), period=period, interval=interval, progress=False,
                           auto_adjust=True, group_by="ticker", prepost=prepost)
        out: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            sub = _extract_yf_ticker(data, ticker)
            if sub is None:
                continue
            if isinstance(sub.columns, pd.MultiIndex):
                sub = sub.droplevel(1, axis=1)
            if "Close" in sub.columns:
                sub = sub.dropna(subset=["Close"])
            if len(sub):
                out[ticker] = _strip_tz_naive(sub)
        return out

    def get_quote(self, ticker: str) -> Quote:
        info = yf.Ticker(ticker).fast_info
        now = datetime.now(timezone.utc)
        return Quote(ticker=ticker, price=float(info.last_price), as_of=now, fetched_at=now,
                    provider=self.provider_name, feed="delayed")

    def stream_quotes(self, tickers: list[str]):
        raise NotImplementedError(
            "yfinance bietet kein Echtzeit-Streaming — YFinanceResearchProvider ist Pull-only "
            "(get_quote/get_bars). Für Live-Streams siehe AlpacaPaperMarketDataProvider.")

    def stream_trades(self, tickers: list[str]):
        raise NotImplementedError(
            "yfinance bietet kein Echtzeit-Streaming — YFinanceResearchProvider ist Pull-only "
            "(get_quote/get_bars). Für Live-Streams siehe AlpacaPaperMarketDataProvider.")

    def get_corporate_actions(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[CorporateAction]:
        t = yf.Ticker(ticker)
        actions: list[CorporateAction] = []
        for ex_date, ratio in t.splits.items():
            ex_dt = ex_date.to_pydatetime()
            if since is not None and ex_dt < since:
                continue
            actions.append(CorporateAction(
                ticker=ticker, action_type="split", ex_date=ex_dt, value=float(ratio)))
        for ex_date, amount in t.dividends.items():
            ex_dt = ex_date.to_pydatetime()
            if since is not None and ex_dt < since:
                continue
            actions.append(CorporateAction(
                ticker=ticker, action_type="dividend", ex_date=ex_dt, value=float(amount)))
        return actions

    def get_market_status(self) -> MarketStatus:
        now = datetime.now(timezone.utc)
        is_open = exchange_calendar.is_market_open(now)
        return MarketStatus(
            is_open=is_open, session="regular" if is_open else "closed",
            as_of=now, provider=self.provider_name)


# ── Alpaca ────────────────────────────────────────────────────────────────

# Nur die in stockbot/config.py::SIGNAL_TIMEFRAMES tatsächlich verwendeten Intervalle abgebildet
# (bewusst keine generische Parser-Rätselei — ein fehlendes Intervall soll laut scheitern statt
# still falsch interpretiert zu werden).
_ALPACA_INTERVAL_TO_TIMEFRAME = {
    "5m": (5, "Minute"), "15m": (15, "Minute"), "30m": (30, "Minute"),
    "1h": (1, "Hour"), "1d": (1, "Day"), "1wk": (1, "Week"),
}

_PERIOD_TO_TIMEDELTA = {
    "1d": timedelta(days=1), "5d": timedelta(days=5), "1wk": timedelta(weeks=1),
    "1mo": timedelta(days=30), "3mo": timedelta(days=90), "1y": timedelta(days=365),
}


def _to_alpaca_timeframe(interval: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    try:
        amount, unit_name = _ALPACA_INTERVAL_TO_TIMEFRAME[interval]
    except KeyError:
        raise ValueError(
            f"Intervall {interval!r} ist für AlpacaPaperMarketDataProvider nicht abgebildet "
            f"(bekannt: {sorted(_ALPACA_INTERVAL_TO_TIMEFRAME)}).") from None
    return TimeFrame(amount, getattr(TimeFrameUnit, unit_name))


def _period_to_start(period: str, *, now: datetime | None = None) -> datetime:
    try:
        delta = _PERIOD_TO_TIMEDELTA[period]
    except KeyError:
        raise ValueError(
            f"Zeitraum {period!r} ist für AlpacaPaperMarketDataProvider nicht abgebildet "
            f"(bekannt: {sorted(_PERIOD_TO_TIMEDELTA)}).") from None
    return (now or datetime.now(timezone.utc)) - delta


class AlpacaPaperMarketDataProvider(MarketDataProvider):
    """Paper-Marktdaten-Provider auf Basis von `alpaca-py`.

    Alpacas Marktdaten-API ist selbst nicht paper-/live-getrennt (dieselben Keys liefern
    dieselben Daten) — der Name spiegelt wider, dass dieser Provider für den Paper-Handelspfad
    gedacht ist (Leitplanke „Paper-Modus bleibt Standard"). `data_client`/`corporate_actions_client`/
    `trading_client` sind injizierbar (Tests brauchen keine echten Alpaca-Keys/Netzwerk)."""

    provider_name = "alpaca_paper"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, *,
                 data_client=None, corporate_actions_client=None, trading_client=None):
        api_key = api_key or config.ALPACA_API_KEY
        api_secret = api_secret or config.ALPACA_API_SECRET
        self._data_client = data_client if data_client is not None else self._build_data_client(
            api_key, api_secret)
        self._ca_client = corporate_actions_client if corporate_actions_client is not None else \
            self._build_ca_client(api_key, api_secret)
        self._trading_client = trading_client if trading_client is not None else \
            broker_client.make_client(api_key, api_secret, paper=True)

    @staticmethod
    def _build_data_client(api_key, api_secret):
        # Ohne Keys keinen Client bauen (der Alpaca-Konstruktor wirft sonst ValueError). Der Provider
        # meldet den fehlenden Client bei der Nutzung klar — statt bei der Konstruktion zu crashen.
        if not (api_key and api_secret):
            return None
        from alpaca.data.historical.stock import StockHistoricalDataClient
        return StockHistoricalDataClient(api_key, api_secret)

    @staticmethod
    def _build_ca_client(api_key, api_secret):
        if not (api_key and api_secret):
            return None
        from alpaca.data.historical.corporate_actions import CorporateActionsClient
        return CorporateActionsClient(api_key, api_secret)

    def _require_data_client(self):
        if self._data_client is None:
            raise RuntimeError(
                "AlpacaPaperMarketDataProvider: kein Marktdaten-Client verfügbar (fehlende/ungültige "
                "ALPACA_API_KEY/ALPACA_API_SECRET) — Kurse können nicht abgefragt werden.")
        return self._data_client

    def get_bars(self, ticker: str, *, interval: str, period: str | None = None,
                 start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        timeframe = _to_alpaca_timeframe(interval)
        if period is not None:
            start = _period_to_start(period)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=timeframe, start=start, end=end)
        return self._require_data_client().get_stock_bars(req).df

    def get_bars_batch(self, tickers: list[str], *, interval: str, period: str | None = None,
                       prepost: bool = True) -> dict[str, pd.DataFrame]:
        """Ein einziger Alpaca-Batch-Request je Timeframe (server-seitig zusammengefügt), pro
        Symbol auf yfinance-Format normalisiert. `prepost` hat bei Alpaca keine Entsprechung
        (die Bars-API liefert Sessions feed-/zeitraumabhängig) und wird ignoriert."""
        from alpaca.data.requests import StockBarsRequest
        if not tickers:
            return {}
        timeframe = _to_alpaca_timeframe(interval)
        start = _period_to_start(period) if period is not None else None
        req = StockBarsRequest(symbol_or_symbols=list(tickers), timeframe=timeframe, start=start)
        df = self._require_data_client().get_stock_bars(req).df
        out: dict[str, pd.DataFrame] = {}
        if df is None or len(df) == 0:
            return out
        if isinstance(df.index, pd.MultiIndex):
            available = set(df.index.get_level_values(0))
            for ticker in tickers:
                if ticker not in available:
                    continue
                norm = _normalize_alpaca_bars(df.xs(ticker, level=0))
                if len(norm):
                    out[ticker] = norm
        elif len(tickers) == 1:
            norm = _normalize_alpaca_bars(df)
            if len(norm):
                out[tickers[0]] = norm
        return out

    def get_quote(self, ticker: str) -> Quote:
        from alpaca.data.requests import StockLatestQuoteRequest
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self._require_data_client().get_stock_latest_quote(req)
        q = quotes[ticker]
        bid, ask = float(q.bid_price or 0.0), float(q.ask_price or 0.0)
        price = (bid + ask) / 2 if bid and ask else (ask or bid)
        now = datetime.now(timezone.utc)
        return Quote(ticker=ticker, price=price, as_of=getattr(q, "timestamp", None) or now,
                    fetched_at=now, provider=self.provider_name, feed="iex",
                    bid=bid or None, ask=ask or None)

    def stream_quotes(self, tickers: list[str]):
        raise NotImplementedError(
            "Echtzeit-Streaming (alpaca.data.live.StockDataStream) ist noch nicht angebunden — "
            "AlpacaPaperMarketDataProvider ist bislang Pull-only (get_quote/get_bars).")

    def stream_trades(self, tickers: list[str]):
        raise NotImplementedError(
            "Echtzeit-Streaming (alpaca.data.live.StockDataStream) ist noch nicht angebunden — "
            "AlpacaPaperMarketDataProvider ist bislang Pull-only (get_quote/get_bars).")

    def get_corporate_actions(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[CorporateAction]:
        from alpaca.data.requests import CorporateActionsRequest
        req = CorporateActionsRequest(symbols=[ticker], start=since)
        result = self._ca_client.get_corporate_actions(req)
        actions: list[CorporateAction] = []
        for action_type, items in result.data.items():
            for item in items:
                ex_date = item.ex_date
                ex_dt = ex_date if isinstance(ex_date, datetime) else datetime(
                    ex_date.year, ex_date.month, ex_date.day, tzinfo=timezone.utc)
                if hasattr(item, "rate"):
                    value = float(item.rate)
                elif hasattr(item, "new_rate") and hasattr(item, "old_rate") and item.old_rate:
                    value = float(item.new_rate) / float(item.old_rate)
                else:
                    value = None
                actions.append(CorporateAction(
                    ticker=ticker, action_type=action_type, ex_date=ex_dt, value=value,
                    detail=str(getattr(item, "corporate_action_type", action_type))))
        return actions

    def get_market_status(self) -> MarketStatus:
        if self._trading_client is None:
            raise RuntimeError(
                "AlpacaPaperMarketDataProvider: kein TradingClient verfügbar (fehlende/ungültige "
                "API-Keys) — Marktstatus kann nicht abgefragt werden.")
        clock = self._trading_client.get_clock()
        now = datetime.now(timezone.utc)
        is_open = bool(clock.is_open)
        return MarketStatus(
            is_open=is_open, session="regular" if is_open else "closed",
            as_of=now, provider=self.provider_name)
