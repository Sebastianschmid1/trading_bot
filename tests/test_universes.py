"""
Tests für das automatische Laden der Aktien-Universen (mit Cache & Fallback).
Laufen offline (keine echten Netzwerkzugriffe — die Fetcher werden gemockt).

Lauf:  python test_universes.py   oder   pytest test_universes.py
"""

import sys
import tempfile
from pathlib import Path

from stockbot import config
from stockbot.market import universes


def _tmp_cache():
    universes.CACHE_FILE = Path(tempfile.mkdtemp(prefix="unitest_")) / "cache.json"


def test_sp500_uses_fetcher_and_caches():
    _tmp_cache()
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return ["AAPL", "MSFT", "NVDA"]

    orig = universes.FETCHERS.copy()
    universes.FETCHERS["sp500"] = fake_fetch
    try:
        t1 = universes.get_tickers("sp500")
        t2 = universes.get_tickers("sp500")     # sollte aus dem Cache kommen
        assert t1 == ["AAPL", "MSFT", "NVDA"]
        assert t2 == t1
        assert calls["n"] == 1, "Fetcher darf nur einmal laufen (Cache greift)"
    finally:
        universes.FETCHERS = orig


def test_sp500_falls_back_to_basket_on_error():
    _tmp_cache()

    def boom():
        raise RuntimeError("kein Netz")

    orig = universes.FETCHERS.copy()
    universes.FETCHERS["sp500"] = boom
    try:
        t = universes.get_tickers("sp500")
        assert t == list(config.UNIVERSE_SP500), "Bei Fehler muss der Fallback-Korb kommen"
    finally:
        universes.FETCHERS = orig


def test_regions_without_fetcher_use_basket():
    _tmp_cache()
    assert universes.get_tickers("msci_world") == list(config.UNIVERSE_MSCI_WORLD)
    assert universes.get_tickers("emerging") == list(config.UNIVERSE_EMERGING)


def test_auto_false_returns_basket_without_fetch():
    _tmp_cache()
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return ["AAPL", "MSFT", "NVDA"]

    orig = universes.FETCHERS.copy()
    universes.FETCHERS["sp500"] = fake_fetch
    try:
        # auto=False → kuratierter Korb aus config.py, KEIN Fetch
        t = universes.get_tickers("sp500", auto=False)
        assert t == list(config.UNIVERSE_SP500)
        assert calls["n"] == 0, "Bei auto=False darf der Fetcher nicht laufen"
        # auto=True (Default) lädt weiterhin die Vollliste
        assert universes.get_tickers("sp500", auto=True) == ["AAPL", "MSFT", "NVDA"]
        assert calls["n"] == 1
    finally:
        universes.FETCHERS = orig


def test_force_refresh_bypasses_cache():
    _tmp_cache()
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return ["AAPL"]

    orig = universes.FETCHERS.copy()
    universes.FETCHERS["sp500"] = fake_fetch
    try:
        universes.get_tickers("sp500")
        universes.get_tickers("sp500", force=True)   # erzwingt erneutes Laden
        assert calls["n"] == 2
    finally:
        universes.FETCHERS = orig


def test_stale_cache_triggers_refetch():
    _tmp_cache()
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return ["AAPL", "MSFT"]

    orig = universes.FETCHERS.copy()
    universes.FETCHERS["sp500"] = fake_fetch
    try:
        universes.get_tickers("sp500")
        # mit max_age_s=0 gilt der Cache sofort als veraltet → erneutes Laden
        universes.get_tickers("sp500", max_age_s=0)
        assert calls["n"] == 2
    finally:
        universes.FETCHERS = orig


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
