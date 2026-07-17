"""Tests für die Backtest-Validierung (W5.6, Gate P7)."""

import numpy as np
import pytest

from stockbot.backtest.validation import (
    HoldoutGuard,
    HoldoutLockedError,
    bootstrap_confidence_interval,
    nested_walk_forward,
    regime_performance,
    sensitivity_analysis,
    walk_forward_splits,
)


# ── Walk-forward / Embargo ───────────────────────────────────────────────────

def test_walk_forward_train_is_always_before_test():
    splits = walk_forward_splits(n_samples=100, n_splits=4, embargo=0, min_train=20)
    assert len(splits) == 4
    for train, test in splits:
        assert max(train) < min(test)          # kein Look-ahead
        assert set(train).isdisjoint(test)


def test_walk_forward_embargo_creates_gap_before_test():
    embargo = 5
    splits = walk_forward_splits(n_samples=100, n_splits=3, embargo=embargo, min_train=20)
    for train, test in splits:
        assert min(test) - max(train) > embargo   # Lücke ≥ Embargo zwischen Train-Ende und Test


def test_walk_forward_covers_test_region_without_overlap():
    splits = walk_forward_splits(n_samples=90, n_splits=3, embargo=0, min_train=30)
    test_indices = [i for _, test in splits for i in test]
    assert len(test_indices) == len(set(test_indices))       # kein Testblock überlappt
    assert test_indices == sorted(test_indices)              # fortlaufend


def test_walk_forward_rejects_bad_params():
    with pytest.raises(ValueError):
        walk_forward_splits(n_samples=5, n_splits=10, min_train=2)


def test_nested_walk_forward_inner_only_on_train():
    outer = nested_walk_forward(n_samples=200, n_outer=3, n_inner=2, embargo=0, min_train=30)
    assert len(outer) == 3
    for fold in outer:
        max_train = max(fold["train"])
        # innere Splits indexieren in den Train-Teil → alle < Länge des Train-Teils
        for inner_train, inner_test in fold["inner"]:
            assert max(inner_test) < len(fold["train"])
            assert max(inner_train) < min(inner_test)
        assert max_train < min(fold["test"])


# ── Holdout-Sperre ───────────────────────────────────────────────────────────

def test_holdout_guard_allows_once_then_locks():
    guard = HoldoutGuard("final-2026", max_accesses=1)
    assert guard.evaluate(lambda: 42, reason="final-eval") == 42
    assert guard.locked
    with pytest.raises(HoldoutLockedError):
        guard.evaluate(lambda: 99)
    assert guard.accesses_used == 1        # der abgelehnte Zugriff zählt nicht


def test_holdout_guard_respects_budget():
    guard = HoldoutGuard("h", max_accesses=2)
    guard.evaluate(lambda: 1)
    assert not guard.locked
    guard.evaluate(lambda: 2)
    assert guard.locked


# ── Bootstrap-KI ─────────────────────────────────────────────────────────────

def test_bootstrap_ci_is_deterministic_and_ordered():
    data = list(np.linspace(-1.0, 1.0, 50))
    low1, mid1, high1 = bootstrap_confidence_interval(data, n_resamples=500, seed=7)
    low2, mid2, high2 = bootstrap_confidence_interval(data, n_resamples=500, seed=7)
    assert (low1, mid1, high1) == (low2, mid2, high2)   # geseedet → reproduzierbar
    assert low1 <= mid1 <= high1


def test_bootstrap_ci_empty_returns_zeros():
    assert bootstrap_confidence_interval([], seed=0) == (0.0, 0.0, 0.0)


# ── Regime / Sensitivität ────────────────────────────────────────────────────

def test_regime_performance_groups_by_label():
    stats = regime_performance([1.0, 2.0, -1.0, 3.0], ["bull", "bull", "bear", "bull"])
    assert stats["bull"].n == 3
    assert stats["bull"].total == 6.0
    assert stats["bear"].mean == -1.0


def test_sensitivity_analysis_measures_spread_and_stability():
    res = sensitivity_analysis([1, 2, 3], metric_fn=lambda p: 10.0 + 0.1 * p, tolerance=0.5)
    assert round(res.spread, 6) == 0.2
    assert res.stable                                   # 0.2 ≤ 0.5
    strict = sensitivity_analysis([1, 2, 3], metric_fn=lambda p: 10.0 * p, tolerance=0.5)
    assert not strict.stable                            # große Streuung
