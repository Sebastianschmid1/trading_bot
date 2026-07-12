"""Konkrete `MarketDataProvider`-Implementierungen (Phase 2 / DATA-003, siehe
docs/Plan.md §10.2, docs/PLAN_CHECKLIST.md Phase 2).

`YFinanceResearchProvider` bündelt die bislang direkt und uneinheitlich über mehrere Module
verstreuten `yfinance`-Aufrufe (`market/analyzer.py`, `core/evaluator.py`, `market/smartmoney.py`,
`market/lookup.py`, `core/db.py`) hinter dem `MarketDataProvider`-Interface — als Research-/
Backtest-Provider, NICHT für den Produktionssignalpfad (Leitplanke „kein yfinance im
Produktionssignalpfad", siehe Definition of Done). `AlpacaPaperMarketDataProvider` (Paper-Feed
über `alpaca-py`) und später `LicensedProductionProvider` folgen als eigene, separate
Checklisten-Punkte.

Noch von KEINEM Live-Codepfad genutzt — die bestehenden Aufrufer weiterhin direkt auf
`yfinance` umzustellen ist ein eigener, größerer Migrationsschritt (Format/Verhalten muss pro
Aufrufer geprüft werden) und folgt separat.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

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
