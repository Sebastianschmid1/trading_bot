"""
Tests fuer RISK-INPUTS Teilaufgabe (1): `avg_volume` fuer ALLE produktiven Strategien.

`analyzer.analyze_ticker` (Strategie "standard") gibt `avg_volume` bereits additiv aus
(siehe test_analyzer.py). `market/strategies.py::_make_signal` — der gemeinsame Signal-
Baustein von "bb_revert" und "ai_adaptive" — berechnete `avg_vol` bislang nur lokal fuer
`vol_ratio` und gab ihn nie zurueck. `risk_context.signal_context` leitet daraus (x `price`)
den `average_dollar_volume` fuer den Liquiditaetscheck (RISK-003 Schritt 9) ab; ohne den
Schluessel blieb der Check fuer diese beiden Strategien immer uebersprungen.

Deckt fuer JEDE Strategie mit `production=True` (heute: standard, bb_revert, ai_adaptive) ab:
  1. `avg_volume` wird unter demselben Schluessel wie `analyzer.analyze_ticker` ausgegeben.
  2. `avg_volume` x `price` ergibt eine plausible Dollar-Groessenordnung (Grundlage von
     `average_dollar_volume` in `risk_context.signal_context`).

Lauf:  pytest tests/test_strategies_avg_volume.py   (offline, synthetische Kurse)
"""

import numpy as np
import pandas as pd
import pytest

from stockbot.market import strategies


def _uptrend_df(seed=1, years=4, volume=2_000_000.0):
    """Synthetische Tageskurse mit Aufwaertstrend + Rauschen (damit Ruecksetzer/Trendwechsel
    ueberhaupt vorkommen) und KONSTANTEM Volumen, damit `avg_volume` deterministisch dem
    konstanten Volumen entspricht (analog test_analyzer.py::_uptrend_df)."""
    n = years * 252
    idx = pd.bdate_range("2018-01-01", periods=n)
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0008, 0.012, n)
    close = 100 * np.exp(np.cumsum(ret))
    return pd.DataFrame(
        {"Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": volume},
        index=idx,
    )


def _first_signal(generate, df):
    """Scannt wachsende Praefixe des DataFrames nach dem ersten ausgeloesten Signal — dieselbe
    Technik wie test_analyzer.py::_first_signal, hier ueber die generische `generate`-Funktion
    einer Strategie (Registry-Signatur: `(ticker, tf_data) -> dict | None`)."""
    for i in range(260, len(df)):
        sig = generate("T", {"1d": df.iloc[:i + 1]})
        if sig:
            return sig
    return None


PRODUCTION_STRATEGIES = strategies.production_strategies()


def test_at_least_the_three_known_strategies_are_production():
    # Schutz gegen eine stillschweigend geschrumpfte/erweiterte Produktions-Liste, die die
    # folgenden parametrisierten Tests unbemerkt aushoehlen wuerde.
    keys = {s.key for s in PRODUCTION_STRATEGIES}
    assert {"standard", "bb_revert", "ai_adaptive"} <= keys


@pytest.mark.parametrize("strategy", PRODUCTION_STRATEGIES, ids=lambda s: s.key)
def test_production_strategy_exposes_avg_volume(strategy):
    # Fester Seed (kein hash(): PYTHONHASHSEED randomisiert Strings pro Prozess) — empirisch
    # loesen alle drei heutigen Produktionsstrategien damit innerhalb der Serie ein Signal aus.
    df = _uptrend_df(seed=1, volume=2_000_000.0)
    sig = _first_signal(strategy.generate, df)
    assert sig is not None, f"{strategy.key}: kein Signal in synthetischen Testdaten ausgeloest"
    assert "avg_volume" in sig, f"{strategy.key}: avg_volume fehlt im Signal-Dict"
    # Konstantes Tagesvolumen -> der Schnitt der letzten Handelstage ist das Volumen selbst.
    assert sig["avg_volume"] == pytest.approx(2_000_000.0, rel=1e-6)


@pytest.mark.parametrize("strategy", PRODUCTION_STRATEGIES, ids=lambda s: s.key)
def test_production_strategy_avg_volume_times_price_is_plausible_dollar_volume(strategy):
    """`avg_volume` x `price` ergibt den Dollar-Umsatz, den `risk_context.signal_context`
    verwendet — hier nur die Groessenordnung geprueft (Kurs im 100er-Bereich x 2 Mio Stueck)."""
    df = _uptrend_df(seed=1, volume=2_000_000.0)
    sig = _first_signal(strategy.generate, df)
    assert sig is not None
    dollar_volume = sig["avg_volume"] * sig["price"]
    assert 1e7 < dollar_volume < 1e10
