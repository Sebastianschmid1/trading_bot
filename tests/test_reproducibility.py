"""Tests für die Backtest-Reproduzierbarkeit (RES-003, W5.5)."""

import random

import numpy as np
import pandas as pd

from stockbot.backtest import engine as backtest
from stockbot.backtest import reproducibility as repro
from stockbot.backtest.reproducibility import RunMetadata, capture_run_metadata, set_seed
from stockbot.market import strategies


def _series(close):
    idx = pd.date_range("2023-01-02", periods=len(close), freq="B", tz="UTC")
    frame = pd.DataFrame(
        {"Open": close, "High": np.asarray(close) * 1.01,
         "Low": np.asarray(close) * 0.99, "Close": close,
         "Volume": np.full(len(close), 1_000_000.0)},
        index=idx,
    )
    return frame


def _dummy_long():
    def generate(ticker, tf_data):
        price = float(tf_data["1d"]["Close"].iloc[-1])
        return {"ticker": ticker, "direction": "long", "price": price,
                "stop_loss": price * 0.98, "take_profit": price * 1.05}
    return strategies.Strategy("dummy", "Dummy", generate)


# ── Metadaten-Erfassung + deterministische run_id ────────────────────────────

def test_capture_metadata_has_all_res003_fields():
    md = capture_run_metadata(strategy_key="standard", tickers=["AAPL", "MSFT"], years=2,
                              trade_size=50.0, allow_short=False, seed=7,
                              cost_model={"legacy_cost_pct": 0.1})
    d = md.as_dict()
    for key in ("run_id", "git_commit", "strategy_key", "tickers", "years", "trade_size",
                "allow_short", "seed", "cost_model", "dependency_versions", "created_at",
                "n_tickers"):
        assert key in d
    assert d["n_tickers"] == 2
    assert d["tickers"] == ("AAPL", "MSFT")  # sortiert, eingefroren


def test_run_id_is_deterministic_across_identical_configs():
    kw = dict(strategy_key="standard", tickers=["MSFT", "AAPL"], years=2,
              trade_size=50.0, allow_short=False, seed=0, cost_model={"legacy_cost_pct": 0.1})
    a = capture_run_metadata(**kw)
    b = capture_run_metadata(**dict(kw, tickers=["AAPL", "MSFT"]))  # andere Reihenfolge
    assert a.run_id == b.run_id  # Ticker-Reihenfolge irrelevant


def test_run_id_changes_when_a_reproducible_field_changes():
    base_kw = dict(strategy_key="standard", tickers=["AAPL"], years=2,
                   trade_size=50.0, allow_short=False, seed=0)
    base = capture_run_metadata(**base_kw)
    for changed in (
        dict(years=3), dict(trade_size=100.0), dict(allow_short=True), dict(seed=1),
        dict(strategy_key="bb_revert"), dict(cost_model={"legacy_cost_pct": 0.2}),
    ):
        other = capture_run_metadata(**{**base_kw, **changed})
        assert other.run_id != base.run_id, changed


def test_run_id_ignores_created_at():
    md1 = RunMetadata(strategy_key="x", tickers=("A",), years=1, trade_size=1.0,
                      allow_short=False, seed=0, git_commit="c", dependency_versions={},
                      created_at="2020-01-01 00:00:00")
    md2 = RunMetadata(strategy_key="x", tickers=("A",), years=1, trade_size=1.0,
                      allow_short=False, seed=0, git_commit="c", dependency_versions={},
                      created_at="2026-07-17 12:00:00")
    assert md1.run_id == md2.run_id  # Laufzeit-Zeitstempel bestimmt die Reproduktion nicht


def test_set_seed_makes_rng_deterministic():
    set_seed(123)
    a = (random.random(), float(np.random.random()))
    set_seed(123)
    b = (random.random(), float(np.random.random()))
    assert a == b


# ── Integration in run_backtest (opt-in) ─────────────────────────────────────

def test_run_backtest_default_has_no_metadata(monkeypatch):
    """Ohne capture_metadata bleibt das Ergebnis-Dict unverändert (kein run_metadata-Key)."""
    monkeypatch.setattr(backtest.strat_mod, "get", lambda key: _dummy_long())
    df = _series(np.linspace(100, 130, 40))
    result = backtest.run_backtest("dummy", data={"X": df.copy()}, jobs=1, cost_pct=0.0)
    assert "run_metadata" not in result


def test_run_backtest_capture_metadata_attaches_stable_run_id(monkeypatch):
    monkeypatch.setattr(backtest.strat_mod, "get", lambda key: _dummy_long())
    df = _series(np.linspace(100, 130, 40))
    r1 = backtest.run_backtest("dummy", data={"X": df.copy()}, jobs=1, cost_pct=0.0,
                               capture_metadata=True, seed=0)
    r2 = backtest.run_backtest("dummy", data={"X": df.copy()}, jobs=1, cost_pct=0.0,
                               capture_metadata=True, seed=0)
    assert "run_metadata" in r1
    assert r1["run_metadata"]["run_id"] == r2["run_metadata"]["run_id"]  # reproduzierbar
    assert r1["trades"] == r2["trades"]  # gleiche Version + Config → gleiches Ergebnis
    # geänderter Seed → andere run_id (Herkunft unterscheidbar)
    r3 = backtest.run_backtest("dummy", data={"X": df.copy()}, jobs=1, cost_pct=0.0,
                               capture_metadata=True, seed=1)
    assert r3["run_metadata"]["run_id"] != r1["run_metadata"]["run_id"]
