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


class YFinanceResearchProvider(MarketDataProvider):
    """Research-/Backtest-Provider auf Basis von `yfinance` (kostenlos, verzögerte Daten).

    Pull-only — `yfinance` bietet kein Echtzeit-Streaming; `stream_quotes`/`stream_trades`
    lehnen daher explizit ab, statt eine irreführende Endlosschleife über Polling zu simulieren.
    """

    provider_name = "yfinance_research"

    def get_bars(self, ticker: str, *, interval: str, period: str | None = None,
                 start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        t = yf.Ticker(ticker)
        if period is not None:
            return t.history(period=period, interval=interval)
        return t.history(start=start, end=end, interval=interval)

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
        from alpaca.data.historical.stock import StockHistoricalDataClient
        return StockHistoricalDataClient(api_key, api_secret)

    @staticmethod
    def _build_ca_client(api_key, api_secret):
        from alpaca.data.historical.corporate_actions import CorporateActionsClient
        return CorporateActionsClient(api_key, api_secret)

    def get_bars(self, ticker: str, *, interval: str, period: str | None = None,
                 start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        timeframe = _to_alpaca_timeframe(interval)
        if period is not None:
            start = _period_to_start(period)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=timeframe, start=start, end=end)
        return self._data_client.get_stock_bars(req).df

    def get_quote(self, ticker: str) -> Quote:
        from alpaca.data.requests import StockLatestQuoteRequest
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quotes = self._data_client.get_stock_latest_quote(req)
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
