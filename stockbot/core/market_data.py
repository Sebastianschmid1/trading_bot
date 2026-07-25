"""MarketDataProvider-Interface (Phase 2 / DATA-003, siehe docs/Plan.md §10.2/§10.6,
docs/PLAN_CHECKLIST.md Phase 2).

Abstraktion für Marktdaten-Zugriffe. Seit W3.2 ist die Leitplanke „kein yfinance im
Produktionssignalpfad" umgesetzt: der Signalpfad (Analyzer-Indikatoren, Trade-Bewertung,
Aktivierungskurs in `core/evaluator.py`/`core/db.py`) läuft über
`market/provider_factory.get_signal_provider()` (Alpaca, nie yfinance). Die konkreten
Implementierungen `AlpacaPaperMarketDataProvider`/`YFinanceResearchProvider` liegen in
`market/data_providers.py`; yfinance bleibt nur noch in den Research-/Anzeige-Helfern
(`market/smartmoney.py`, `market/lookup.py`, Analyzer-Sparklines/`factor_history`), die keine
Trades erzeugen.

Bars werden bewusst als `pandas.DataFrame` (OHLCV, `DatetimeIndex`) zurückgegeben — dasselbe
Format, das die bestehende Indikator-Berechnung schon erwartet, damit die spätere Umstellung
bestehender Aufrufer keinen Format-Bruch erzwingt.

Reine, IO-freie Definition (Interface + Wertobjekte) — im Live-Codepfad genutzt (u. a.
`market/provider_factory.py`, `market/data_providers.py`, `core/risk.py` via `core/data_quality.py`,
`web/feed_status.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class Quote:
    """Ein Kursschnappschuss — Grundlage für Plan.md §10.3 „Datenherkunft je Berechnung
    speichern" (Provider, Feed, Abrufzeit, Exchange-Zeit sind hier bereits als Felder angelegt)."""
    ticker: str
    price: float
    as_of: datetime            # Exchange-Zeit des Kurses
    fetched_at: datetime       # Abrufzeit (kann von as_of abweichen, z. B. bei verzögerten Feeds)
    provider: str
    feed: str = ""
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class CorporateAction:
    """Split/Dividende/Symbolwechsel — beeinflusst historische Bars und P&L-Berechnung."""
    ticker: str
    action_type: str            # "split" | "dividend" | "symbol_change" | ...
    ex_date: datetime
    value: float | None = None  # z. B. Split-Ratio oder Dividendenbetrag
    detail: str = ""


@dataclass(frozen=True)
class MarketStatus:
    """Marktzustand laut Provider — kann vom Exchange-Kalender (`core/exchange_calendar.py`)
    abweichen, z. B. bei ungeplanten Handelsunterbrechungen (Halts)."""
    is_open: bool
    session: str                # "regular" | "pre" | "post" | "closed"
    as_of: datetime
    provider: str


class MarketDataProvider(ABC):
    """Gemeinsames Interface für alle Marktdatenquellen (Plan.md §10.2).

    Implementierungen sind austauschbar, damit Research/Backtest (`YFinanceResearchProvider`)
    und Produktionssignale (`AlpacaPaperMarketDataProvider`, später `LicensedProductionProvider`)
    unabhängig voneinander sind, ohne den aufrufenden Code umschreiben zu müssen.
    """

    @abstractmethod
    def get_bars(self, ticker: str, *, interval: str, period: str | None = None,
                 start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """Historische OHLCV-Bars (`DatetimeIndex`, Spalten Open/High/Low/Close/Volume).

        Entweder `period` (z. B. "5d") ODER `start`/`end` angeben — wie bei `yfinance`."""
        raise NotImplementedError

    def get_bars_batch(self, tickers: list[str], *, interval: str, period: str | None = None,
                       prepost: bool = True) -> dict[str, pd.DataFrame]:
        """Historische OHLCV-Bars für mehrere Ticker → ``{ticker: DataFrame}``.

        Jedes DataFrame ist yfinance-förmig (Spalten Open/High/Low/Close/Volume, `DatetimeIndex`),
        damit der bestehende Signalpfad (`market/analyzer.py`) formatneutral migriert werden kann.
        Ticker ohne Daten fehlen im Ergebnis. `prepost` bezieht Extended-Hours-Bars ein, sofern der
        Provider das unterstützt (sonst ignoriert).

        Default: seriell über :meth:`get_bars` — Provider mit echtem Batch-API überschreiben das."""
        out: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            try:
                df = self.get_bars(ticker, interval=interval, period=period)
            except Exception:
                continue
            if df is not None and len(df):
                out[ticker] = df
        return out

    @abstractmethod
    def get_quote(self, ticker: str) -> Quote:
        """Aktueller Kursschnappschuss."""
        raise NotImplementedError

    @abstractmethod
    def stream_quotes(self, tickers: list[str]):
        """Iterator/Generator laufender Quote-Updates (Ausführung providerabhängig)."""
        raise NotImplementedError

    @abstractmethod
    def stream_trades(self, tickers: list[str]):
        """Iterator/Generator laufender Trade-Ticks (Ausführung providerabhängig)."""
        raise NotImplementedError

    @abstractmethod
    def get_corporate_actions(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[CorporateAction]:
        """Splits/Dividenden seit `since` (Default: providerabhängiger Standardzeitraum)."""
        raise NotImplementedError

    @abstractmethod
    def get_market_status(self) -> MarketStatus:
        """Aktueller Marktzustand laut Provider."""
        raise NotImplementedError
