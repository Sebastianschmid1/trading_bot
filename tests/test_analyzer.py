"""
Tests für additive Analyzer-Felder (RISK-LIQUIDITY).

Deckt ab, dass `analyzer.analyze_ticker` das intern ohnehin schon berechnete
durchschnittliche Tagesvolumen (Stück) additiv im Signal-Dict mitgibt (`avg_volume`) —
die Grundlage, aus der `risk_context.signal_context` den Dollar-Umsatz für den
Liquiditätscheck (RISK-003 Schritt 9) ableitet, ohne einen weiteren Marktdatenabruf.

Lauf:  pytest tests/test_analyzer.py   (offline, synthetische Kurse)
"""

import numpy as np
import pandas as pd
import pytest

from stockbot.market import analyzer


def _uptrend_df(seed=1, years=2, volume=2_000_000.0):
    """Synthetische Tageskurse mit klarem Aufwärtstrend + KONSTANTEM Volumen, damit
    `avg_volume` deterministisch dem konstanten Volumen entspricht."""
    n = years * 252
    idx = pd.bdate_range("2018-01-01", periods=n)
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0008, 0.01, n)
    close = 100 * np.exp(np.cumsum(ret))
    return pd.DataFrame(
        {"Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": volume},
        index=idx,
    )


def _first_signal(df):
    for i in range(260, len(df)):
        sig = analyzer.analyze_ticker("T", {"1d": df.iloc[:i + 1]})
        if sig:
            return sig
    return None


def test_analyze_ticker_exposes_avg_volume():
    df = _uptrend_df(volume=2_000_000.0)
    sig = _first_signal(df)
    assert sig is not None
    # Konstantes Tagesvolumen -> der Schnitt der abgeschlossenen Handelstage ist das Volumen selbst.
    assert sig["avg_volume"] == pytest.approx(2_000_000.0, rel=1e-6)


def test_analyze_ticker_avg_volume_times_price_is_plausible_dollar_volume():
    """`avg_volume` × `price` ergibt den Dollar-Umsatz, den risk_context.signal_context
    verwendet — hier nur die Größenordnung geprüft (Kurs~100er-Bereich × 2 Mio Stück)."""
    df = _uptrend_df(volume=2_000_000.0)
    sig = _first_signal(df)
    assert sig is not None
    dollar_volume = sig["avg_volume"] * sig["price"]
    assert 1e7 < dollar_volume < 1e10
