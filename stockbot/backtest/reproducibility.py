"""Reproduzierbarkeit von Backtest-Läufen (RES-003).

Erfasst je Lauf die vollständige, für Reproduktion nötige Herkunft: Run-ID, Git-Commit,
Strategie-/Datenversion, Universum, Parameter, Kostenmodell, Zeitraum, Seed und die Versionen
der relevanten Dependencies. Reines Metadaten-/Seed-Modul — es ändert das Backtest-ERGEBNIS
nicht, sondern beschreibt und determinisiert nur den Lauf.

Die ``run_id`` ist ein DETERMINISTISCHER Hash über die reproduktionsrelevanten Felder (ohne
``created_at``): gleiche Konfiguration + gleicher Commit + gleiche Deps + gleicher Seed ergeben
dieselbe ``run_id`` → so lässt sich „gleicher Run + gleiche Version → gleiches Ergebnis"
(Gate P7) technisch prüfen.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

# Dependencies, deren Version das Backtest-Ergebnis beeinflussen kann.
_TRACKED_DEPENDENCIES = ("pandas", "numpy", "yfinance", "alpaca-py", "pandas_market_calendars")

# Fester Default-Seed: der Backtest ist heute deterministisch (kein RNG im Entscheidungspfad),
# der Seed determinisiert etwaige künftige stochastische Elemente und wird mitprotokolliert.
DEFAULT_SEED = 0


@lru_cache(maxsize=1)
def _git_commit() -> str:
    """Aktueller Git-Commit (kurz) oder 'unknown' — read-only, fehlertolerant."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=here, capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _dependency_versions() -> dict[str, str]:
    """Installierte Versionen der reproduktionsrelevanten Dependencies."""
    import importlib.metadata as md
    versions: dict[str, str] = {}
    for name in _TRACKED_DEPENDENCIES:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = "absent"
    return versions


def set_seed(seed: int = DEFAULT_SEED) -> int:
    """Seedet die Standard-RNGs (`random`, optional `numpy`) für deterministische Läufe.

    Gibt den gesetzten Seed zurück. `numpy` wird nur geseedet, wenn importierbar.
    """
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    return seed


@dataclass(frozen=True)
class RunMetadata:
    """Vollständige Reproduktions-Herkunft eines Backtest-Laufs (Plan.md §RES-003)."""

    strategy_key: str
    tickers: tuple[str, ...]
    years: int
    trade_size: float
    allow_short: bool
    seed: int
    cost_model: dict = field(default_factory=dict)
    strategy_version: str | None = None
    universe_region: str | None = None
    trail_mode: str | None = None
    trail_mult: float | None = None
    git_commit: str = field(default_factory=_git_commit)
    dependency_versions: dict = field(default_factory=_dependency_versions)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    )

    @property
    def n_tickers(self) -> int:
        return len(self.tickers)

    def _reproducible_fields(self) -> dict:
        """Alle Felder, die die Reproduktion bestimmen — OHNE die Laufzeit `created_at`."""
        data = asdict(self)
        data.pop("created_at", None)
        return data

    @property
    def run_id(self) -> str:
        """Deterministischer Hash der reproduktionsrelevanten Felder (stabil über Läufe)."""
        payload = json.dumps(self._reproducible_fields(), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict:
        """JSON-freundliche Darstellung inklusive abgeleiteter Felder."""
        out = asdict(self)
        out["run_id"] = self.run_id
        out["n_tickers"] = self.n_tickers
        return out


def capture_run_metadata(*, strategy_key: str, tickers, years: int, trade_size: float,
                         allow_short: bool, seed: int = DEFAULT_SEED,
                         cost_model: dict | None = None, strategy_version: str | None = None,
                         universe_region: str | None = None, trail_mode: str | None = None,
                         trail_mult: float | None = None) -> RunMetadata:
    """Erfasst die Reproduktions-Metadaten eines Laufs.

    `tickers` wird sortiert+entdupliziert eingefroren, damit dieselbe Ticker-Menge unabhängig
    von der Aufrufreihenfolge dieselbe `run_id` ergibt.
    """
    frozen_tickers = tuple(sorted({str(t) for t in (tickers or ())}))
    return RunMetadata(
        strategy_key=str(strategy_key),
        tickers=frozen_tickers,
        years=int(years),
        trade_size=float(trade_size),
        allow_short=bool(allow_short),
        seed=int(seed),
        cost_model=dict(cost_model or {}),
        strategy_version=strategy_version,
        universe_region=universe_region,
        trail_mode=trail_mode,
        trail_mult=(float(trail_mult) if trail_mult is not None else None),
    )
