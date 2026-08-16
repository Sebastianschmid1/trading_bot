"""
Tests für die Asset-Klassen-Registry (Schritt 2): Profil-Defaults, Universum-Aufbau,
asset_pref-Persistenz und die Profil-Durchreichung in den Analyzer.

Lauf:  python -m tests.test_asset_classes   oder   pytest tests/test_asset_classes.py
Offline (kein Netz).
"""

import sys
import tempfile
from pathlib import Path

from stockbot.core import db
from stockbot.market import asset_classes as ac
from stockbot.market import analyzer
from stockbot import config


def fresh():
    d = tempfile.mkdtemp(prefix="assettest_")
    db.DB_FILE = Path(d) / "a.db"
    db.init_db()


def test_default_and_unknown_fallback_to_stocks():
    assert ac.get_asset_class(None).key == "stocks"
    assert ac.get_asset_class("gibtsnicht").key == "stocks"
    assert [a.key for a in ac.all_asset_classes()][0] == "stocks"   # Aktien zuerst


def test_all_four_classes_registered_with_profiles():
    keys = [a.key for a in ac.all_asset_classes()]
    assert keys == ["stocks", "etf", "crypto", "commodity"]      # Aktien zuerst
    assert ac.get_asset_class("etf").profile.smart_money is False
    assert ac.get_asset_class("commodity").profile is ac.ETF_PROFILE
    cp = ac.get_asset_class("crypto").profile
    assert cp.prepost is False and cp.block_weekly_downtrend is False and cp.smart_money is False
    assert cp.rsi_oversold == 30.0 and cp.rsi_overbought == 75.0


def test_fixed_universes_nonempty_and_independent_of_user():
    for key in ("etf", "crypto", "commodity"):
        t = ac.get_asset_class(key).get_tickers({})
        assert t and isinstance(t, list)
    assert "BTC-USD" in ac.get_asset_class("crypto").get_tickers(None)
    assert "GLD" in ac.get_asset_class("commodity").get_tickers(None)


def test_stock_profile_matches_config_defaults():
    p = ac.STOCK_PROFILE
    assert p.prepost is True
    assert p.rsi_oversold == config.RSI_OVERSOLD
    assert p.rsi_overbought == config.RSI_OVERBOUGHT
    assert p.min_strength == config.MIN_SIGNAL_STRENGTH
    assert p.block_weekly_downtrend == config.BLOCK_WEEKLY_DOWNTREND


def test_stock_tickers_merges_regions(monkeypatch):
    monkeypatch.setattr(ac.universes, "get_tickers",
                        lambda region, auto=True: {"sp500": ["AAPL", "MSFT"],
                                                   "emerging": ["MSFT", "BABA"]}[region])
    user = {"market_regions": ["sp500", "emerging"], "auto_universe": False}
    tickers = ac.get_asset_class("stocks").get_tickers(user)
    assert tickers == ["AAPL", "MSFT", "BABA"]          # dedupliziert, Reihenfolge stabil


def test_asset_pref_persists():
    fresh()
    db.get_or_create_user(7, "tester")
    assert db.get_user(7)["asset_pref"] == "stocks"     # Default
    db.set_asset_pref(7, "etf")
    assert db.get_user(7)["asset_pref"] == "etf"


def test_classify_ticker():
    assert ac.classify_ticker("AAPL") == "stocks"
    assert ac.classify_ticker("SPY") == "etf"
    assert ac.classify_ticker("GLD") == "commodity"
    assert ac.classify_ticker("BTC-USD") == "crypto"
    assert ac.classify_ticker("FOO-USD") == "crypto"        # -USD-Suffix → Krypto
    assert ac.classify_ticker("") == "stocks"


def test_correlation_group_for_ticker_known_universes():
    # RISK-005-Wiring: Emerging-Markets-ADR aus UNIVERSE_EMERGING → Gruppe "emerging".
    assert ac.correlation_group_for_ticker("BABA") == "emerging"
    assert ac.correlation_group_for_ticker("tsm") == "emerging"     # case-insensitiv
    assert ac.correlation_group_for_ticker(" ggb ".strip()) == "emerging"


def test_correlation_group_for_ticker_prefers_first_matching_universe():
    # AAPL steht sowohl in UNIVERSE_SP500 als auch UNIVERSE_MSCI_WORLD — config.UNIVERSES ist
    # sp500-zuerst definiert, die Zuordnung ist deterministisch die erste passende Gruppe.
    assert list(config.UNIVERSES)[0] == "sp500"
    assert "AAPL" in config.UNIVERSE_SP500 and "AAPL" in config.UNIVERSE_MSCI_WORLD
    assert ac.correlation_group_for_ticker("AAPL") == "sp500"


def test_correlation_group_for_ticker_unknown_and_empty_is_none():
    # Ticker aus keiner statischen Liste (z. B. nur ueber das dynamische volle S&P-500-Universum
    # in market/universes.py bekannt) bleibt ohne Gruppe — fail-neutral, kein Raten.
    assert ac.correlation_group_for_ticker("NOPE_XYZ") is None
    assert ac.correlation_group_for_ticker("") is None
    assert ac.correlation_group_for_ticker(None) is None


def test_apply_sl_tp_mode_overrides_and_aus():
    base = {"price": 100.0, "atr": 2.0, "stop_loss": 90.0, "take_profit": 130.0}
    # passiv = (1.0, 1.5) → SL 98, TP 103
    s = analyzer.apply_sl_tp_mode(dict(base), "passiv")
    assert round(s["stop_loss"], 2) == 98.0 and round(s["take_profit"], 2) == 103.0
    # aggressiv = (2.5, 4.0) → SL 95, TP 108
    s = analyzer.apply_sl_tp_mode(dict(base), "aggressiv")
    assert round(s["stop_loss"], 2) == 95.0 and round(s["take_profit"], 2) == 108.0
    # 'aus' entfernt die Grenzen
    s = analyzer.apply_sl_tp_mode(dict(base), "aus")
    assert s["stop_loss"] is None and s["take_profit"] is None
    # fehlendes ATR → Strategie-Vorgabe bleibt erhalten
    s = analyzer.apply_sl_tp_mode({"price": 100.0, "atr": None, "stop_loss": 90.0, "take_profit": 130.0}, "normal")
    assert s["stop_loss"] == 90.0 and s["take_profit"] == 130.0


def test_analyze_universe_empty_is_noop():
    assert analyzer.analyze_universe([], profile=ac.STOCK_PROFILE) == []


def test_profile_threads_into_analyze_ticker(monkeypatch):
    """analyze_universe ohne generate nutzt den Default-Generator MIT Profil."""
    seen = {}

    def fake_download(tickers, prepost=True):
        seen["prepost"] = prepost
        return {"1d": object()}

    def fake_analyze(ticker, tf_data, profile=None):
        seen["profile"] = profile
        return None

    monkeypatch.setattr(analyzer, "_download_all_timeframes", fake_download)
    monkeypatch.setattr(analyzer, "_tf_data_for", lambda downloads, ticker: {"1d": object()})
    monkeypatch.setattr(analyzer, "analyze_ticker", fake_analyze)
    prof = ac.Profile(prepost=False, min_strength=42.0)
    analyzer.analyze_universe(["BTC-USD"], profile=prof)
    assert seen["prepost"] is False
    assert seen["profile"] is prof


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
