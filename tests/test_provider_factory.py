"""Tests für die Marktdaten-Provider-Auswahl (W3.2 / DATA-003, provider_factory)."""

import pytest

from stockbot.market import provider_factory as pf
from stockbot.market.data_providers import (
    AlpacaPaperMarketDataProvider, YFinanceResearchProvider,
)


@pytest.fixture(autouse=True)
def _reset_provider():
    pf.reset_signal_provider()
    yield
    pf.reset_signal_provider()


def test_signal_provider_is_alpaca_never_yfinance():
    provider = pf.get_signal_provider()
    # Leitplanke: der Prod-Signalpfad nutzt NIE yfinance.
    assert isinstance(provider, AlpacaPaperMarketDataProvider)
    assert not isinstance(provider, YFinanceResearchProvider)
    assert provider.provider_name == "alpaca_paper"


def test_signal_provider_is_cached_singleton():
    assert pf.get_signal_provider() is pf.get_signal_provider()


def test_set_and_reset_signal_provider():
    sentinel = object()
    pf.set_signal_provider(sentinel)
    assert pf.get_signal_provider() is sentinel
    pf.reset_signal_provider()
    assert pf.get_signal_provider() is not sentinel


def test_research_provider_is_yfinance():
    assert isinstance(pf.get_research_provider(), YFinanceResearchProvider)
