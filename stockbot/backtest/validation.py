"""Validierungs-Werkzeuge für Backtests (Plan.md §Phase 7, Gate P7).

Reine, IO-freie, deterministische Bausteine gegen Overfitting/Leakage:

- `walk_forward_splits` / `nested_walk_forward` — zeitgeordnete Train/Test-Aufteilungen; Train
  liegt stets VOR Test (kein Look-ahead).
- Purging + **Embargo** — eine Lücke zwischen Train-Ende und Test-Beginn entfernt Beobachtungen,
  deren Label-Horizont in den Test hineinreicht (López-de-Prado-Muster).
- `HoldoutGuard` — sperrt den finalen Holdout technisch: er darf nur begrenzt oft ausgewertet
  werden (Default genau einmal), jeder Zugriff wird protokolliert (Gate P7: „finaler Holdout
  technisch gesperrt").
- `bootstrap_confidence_interval` — geseedetes Bootstrap-Konfidenzintervall einer Kennzahl.
- `regime_performance` — Kennzahlen je Marktregime.
- `sensitivity_analysis` — Stabilität einer Kennzahl über Parametervariationen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class HoldoutLockedError(RuntimeError):
    """Der finale Holdout wurde öfter als erlaubt ausgewertet."""


# ── Walk-forward mit Embargo ─────────────────────────────────────────────────

def walk_forward_splits(n_samples: int, n_splits: int, embargo: int = 0,
                        min_train: int = 1) -> list[tuple[list[int], list[int]]]:
    """Expandierende Walk-forward-Splits über `n_samples` zeitgeordnete Beobachtungen.

    Teilt den Bereich nach dem ersten Trainingsblock in `n_splits` aufeinanderfolgende
    Testblöcke. Train = alle Indizes VOR dem Testblock, abzüglich einer `embargo`-Lücke
    unmittelbar vor dem Test (Purging: entfernt Beobachtungen, deren Label in den Test reicht).
    Train liegt immer strikt vor Test → kein Look-ahead.
    """
    if n_splits < 1:
        raise ValueError("n_splits muss ≥ 1 sein")
    if embargo < 0 or min_train < 1:
        raise ValueError("embargo ≥ 0 und min_train ≥ 1 erforderlich")
    if n_samples <= min_train + n_splits:
        raise ValueError("zu wenige Beobachtungen für die gewünschten Splits")

    test_region_start = min_train
    fold_size = (n_samples - test_region_start) // n_splits
    if fold_size < 1:
        raise ValueError("Testblöcke wären leer — n_splits zu groß")

    splits: list[tuple[list[int], list[int]]] = []
    for k in range(n_splits):
        test_start = test_region_start + k * fold_size
        test_end = n_samples if k == n_splits - 1 else test_start + fold_size
        test_idx = list(range(test_start, test_end))
        train_end = max(0, test_start - embargo)          # Embargo-Lücke vor dem Test
        train_idx = list(range(0, train_end))
        if len(train_idx) < min_train:
            continue
        splits.append((train_idx, test_idx))
    return splits


def nested_walk_forward(n_samples: int, n_outer: int, n_inner: int, embargo: int = 0,
                        min_train: int = 1) -> list[dict]:
    """Genestete Walk-forward-Validierung: je äußerem Split innere Splits NUR auf dem Train-Teil.

    Liefert je äußerem Split ein Dict {train, test, inner:[(train,test),…]}. Die inneren Splits
    (Hyperparameter-Tuning) sehen ausschließlich äußere Trainingsdaten — der äußere Test bleibt
    unberührt.
    """
    outer = walk_forward_splits(n_samples, n_outer, embargo, min_train)
    result = []
    for train_idx, test_idx in outer:
        inner = []
        if len(train_idx) > min_train + n_inner:
            inner = walk_forward_splits(len(train_idx), n_inner, embargo, min_train)
        result.append({"train": train_idx, "test": test_idx, "inner": inner})
    return result


# ── Holdout-Sperre ───────────────────────────────────────────────────────────

class HoldoutGuard:
    """Sperrt den finalen Holdout technisch: begrenzte, protokollierte Auswertungen.

    Default `max_accesses=1` — der Holdout darf genau einmal ausgewertet werden; jeder weitere
    Zugriff wirft `HoldoutLockedError`. Verhindert wiederholtes „am Holdout optimieren".
    """

    def __init__(self, holdout_id: str, max_accesses: int = 1):
        if max_accesses < 1:
            raise ValueError("max_accesses muss ≥ 1 sein")
        self.holdout_id = holdout_id
        self.max_accesses = max_accesses
        self.access_log: list[str] = []

    @property
    def accesses_used(self) -> int:
        return len(self.access_log)

    @property
    def locked(self) -> bool:
        return self.accesses_used >= self.max_accesses

    def evaluate(self, fn, *args, reason: str = "", **kwargs):
        """Führt `fn` genau dann aus, wenn das Zugriffsbudget nicht erschöpft ist."""
        if self.locked:
            raise HoldoutLockedError(
                f"Holdout '{self.holdout_id}' gesperrt: {self.accesses_used}/{self.max_accesses} "
                f"Zugriffe verbraucht"
            )
        self.access_log.append(reason or f"access#{self.accesses_used + 1}")
        return fn(*args, **kwargs)


# ── Bootstrap-Konfidenzintervall ─────────────────────────────────────────────

def bootstrap_confidence_interval(samples, n_resamples: int = 1000, alpha: float = 0.05,
                                  seed: int = 0, statistic=np.mean) -> tuple[float, float, float]:
    """Geseedetes Bootstrap-KI einer Kennzahl über `samples`.

    Gibt (untere Grenze, Punktschätzer, obere Grenze) für das `(1-alpha)`-Intervall zurück.
    Deterministisch über `seed`.
    """
    data = np.asarray(list(samples), dtype=float)
    if data.size == 0:
        return (0.0, 0.0, 0.0)
    if not (0.0 < alpha < 1.0):
        raise ValueError("alpha muss in (0,1) liegen")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, data.size, size=(n_resamples, data.size))
    stats = np.array([statistic(data[row]) for row in idx])
    low = float(np.percentile(stats, 100 * alpha / 2))
    high = float(np.percentile(stats, 100 * (1 - alpha / 2)))
    return (low, float(statistic(data)), high)


# ── Regime-Auswertung ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegimeStats:
    regime: str
    n: int
    mean: float
    total: float


def regime_performance(values, regimes) -> dict[str, RegimeStats]:
    """Aggregiert Kennzahlen (z. B. Trade-Returns) je Regime-Label.

    `values` und `regimes` sind gleich lange Sequenzen (ein Regime-Label je Wert).
    """
    values = list(values)
    regimes = list(regimes)
    if len(values) != len(regimes):
        raise ValueError("values und regimes müssen gleich lang sein")
    buckets: dict[str, list[float]] = {}
    for v, r in zip(values, regimes):
        buckets.setdefault(str(r), []).append(float(v))
    return {
        r: RegimeStats(regime=r, n=len(vs), mean=(sum(vs) / len(vs)), total=sum(vs))
        for r, vs in buckets.items()
    }


# ── Sensitivitätsanalyse ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SensitivityResult:
    metric_by_param: dict
    spread: float          # max − min (absolute Streuung)
    std: float             # Standardabweichung der Kennzahl
    stable: bool           # Streuung ≤ Toleranz


def sensitivity_analysis(param_values, metric_fn, tolerance: float = float("inf")
                         ) -> SensitivityResult:
    """Wertet eine Kennzahl über eine Reihe von Parameterwerten aus und misst ihre Stabilität.

    `metric_fn(param) -> float`. `stable` ist True, wenn die absolute Streuung (max−min) der
    Kennzahl ≤ `tolerance` bleibt — eine robuste Strategie schwankt bei kleinen Parameteränderungen
    nur wenig.
    """
    metric_by_param = {p: float(metric_fn(p)) for p in param_values}
    if not metric_by_param:
        return SensitivityResult({}, 0.0, 0.0, True)
    vals = np.array(list(metric_by_param.values()), dtype=float)
    spread = float(vals.max() - vals.min())
    return SensitivityResult(
        metric_by_param=metric_by_param,
        spread=spread,
        std=float(vals.std()),
        stable=spread <= tolerance,
    )
