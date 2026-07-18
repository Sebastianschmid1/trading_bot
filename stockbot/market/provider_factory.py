"""Auswahl des Marktdaten-Providers je Betriebszweck (W3.2 / DATA-003).

Ein zentraler Ort, der entscheidet, WELCHER `MarketDataProvider` den jeweiligen Codepfad
bedient — damit die Leitplanke „kein yfinance im Produktionssignalpfad" an genau einer Stelle
durchgesetzt wird:

- `get_signal_provider()`  → Produktions-Signalpfad (Analyzer-Indikatoren, Trade-Bewertung,
  Aktivierungskurs). IMMER Alpaca, NIE yfinance. Ohne Alpaca-Keys degradiert der Provider
  sauber (Datenabrufe werfen und werden von den Aufrufern per Fallback abgefangen) — er fällt
  bewusst NICHT auf yfinance zurück, denn das würde die Leitplanke verletzen.
- `get_research_provider()` → Research/Backtest/Anzeige (yfinance, kostenlos, verzögert).

`set_signal_provider()`/`reset_signal_provider()` erlauben Tests, einen Fake-Provider zu
injizieren, ohne echte Keys/Netzwerk.
"""

from __future__ import annotations

from stockbot.core.market_data import MarketDataProvider
from stockbot.market.data_providers import (
    AlpacaPaperMarketDataProvider, YFinanceResearchProvider,
)

_signal_provider: MarketDataProvider | None = None


def get_signal_provider() -> MarketDataProvider:
    """Der Marktdaten-Provider für den PRODUKTIONS-Signalpfad — Alpaca, niemals yfinance."""
    global _signal_provider
    if _signal_provider is None:
        _signal_provider = AlpacaPaperMarketDataProvider()
    return _signal_provider


def set_signal_provider(provider: MarketDataProvider | None) -> None:
    """Ersetzt den (gecachten) Signalprovider — für Tests/Dependency-Injection."""
    global _signal_provider
    _signal_provider = provider


def reset_signal_provider() -> None:
    """Verwirft den gecachten Signalprovider (nächster Zugriff baut ihn neu)."""
    global _signal_provider
    _signal_provider = None


def get_research_provider() -> MarketDataProvider:
    """yfinance-Provider — NUR für Research/Backtest/Anzeige, nie für Prod-Signale."""
    return YFinanceResearchProvider()
