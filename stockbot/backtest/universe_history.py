"""Point-in-Time-Universen + Survivorship-Bias-Messung für Backtests (Plan.md §Phase 7).

Der LIVE-Pfad (`stockbot/market/universes.py`) liefert nur die AKTUELLE Indexzusammensetzung.
Ein korrekter Backtest braucht die Zusammensetzung ZUM JEWEILIGEN ZEITPUNKT (point-in-time),
sonst entsteht **Survivorship-Bias**: heute noch existierende „Gewinner" sind überrepräsentiert,
zwischenzeitlich delistete „Verlierer" fehlen.

Dieses Modul ist ein reiner, IO-freier Seam:
- `UniverseSnapshot` — eine versionierte Momentaufnahme (Stichtag + aktive Mitglieder + optional
  seither delistete Ticker) mit deterministischer `version`.
- `PointInTimeUniverse` — eine geordnete Reihe solcher Snapshots; `members_as_of(date)` gibt die
  zum Datum gültige Zusammensetzung (die letzte Momentaufnahme ≤ Datum).
- `measure_survivorship_bias(...)` — quantifiziert, wie stark ein naiver „heutiges-Universum"-
  Backtest verzerrt.

**Datenlücke (ehrlich dokumentiert):** Es gibt in diesem Repo KEINE echte historische
Mitgliedschaftshistorie. Die Snapshots müssen von außen bereitgestellt werden (z. B. eine
lizenzierte Indexhistorie). Ohne solche Daten degeneriert ein Backtest zwangsläufig zu
„heutiges Universum" — und genau diese Verzerrung macht `measure_survivorship_bias` messbar.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _normalise(members) -> frozenset[str]:
    return frozenset(str(m).strip().upper() for m in (members or ()) if str(m).strip())


def snapshot_version(as_of: str, members) -> str:
    """Deterministische Versions-ID einer Zusammensetzung (Stichtag + sortierte Mitglieder)."""
    payload = as_of.strip() + "|" + ",".join(sorted(_normalise(members)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class UniverseSnapshot:
    """Versionierte Point-in-Time-Momentaufnahme eines Universums.

    `as_of` ist ein naiver UTC-Datums-String 'YYYY-MM-DD' (konsistent zum DB-Zeitvertrag).
    `members` = zum Stichtag AKTIVE Konstituenten. `delisted` = seit der vorigen Aufnahme
    entfernte/delistete Ticker (für die Survivorship-Analyse; nie Teil der aktiven Mitglieder).
    """

    as_of: str
    members: frozenset[str]
    delisted: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        object.__setattr__(self, "members", _normalise(self.members))
        object.__setattr__(self, "delisted", _normalise(self.delisted) - self.members)

    @property
    def version(self) -> str:
        return snapshot_version(self.as_of, self.members)

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "version": self.version,
            "members": sorted(self.members),
            "delisted": sorted(self.delisted),
            "n_members": len(self.members),
        }


class PointInTimeUniverse:
    """Geordnete Reihe von `UniverseSnapshot`s; liefert die zum Datum gültige Zusammensetzung."""

    def __init__(self, snapshots=None):
        # nach Stichtag sortiert, damit members_as_of die jüngste gültige Aufnahme findet
        self._snapshots: list[UniverseSnapshot] = sorted(
            snapshots or [], key=lambda s: s.as_of
        )

    def add(self, snapshot: UniverseSnapshot) -> None:
        self._snapshots.append(snapshot)
        self._snapshots.sort(key=lambda s: s.as_of)

    @property
    def snapshots(self) -> tuple[UniverseSnapshot, ...]:
        return tuple(self._snapshots)

    def versions(self) -> list[str]:
        return [s.version for s in self._snapshots]

    def snapshot_as_of(self, date: str) -> UniverseSnapshot | None:
        """Die jüngste Momentaufnahme mit `as_of` ≤ `date` (oder None, falls keine existiert)."""
        eligible = [s for s in self._snapshots if s.as_of <= date]
        return eligible[-1] if eligible else None

    def members_as_of(self, date: str) -> frozenset[str]:
        """Zum `date` gültige aktive Mitglieder — leer, wenn keine Aufnahme ≤ date existiert."""
        snap = self.snapshot_as_of(date)
        return snap.members if snap else frozenset()


@dataclass(frozen=True)
class SurvivorshipReport:
    """Messgrößen der Survivorship-Verzerrung eines naiven „heutiges-Universum"-Backtests."""

    historical_count: int
    current_count: int
    removed_since: tuple[str, ...]   # damals im Index, heute nicht mehr → im naiven Backtest verschwiegen
    added_since: tuple[str, ...]     # heute im Index, damals nicht → Look-ahead bei naiver Nutzung
    survivorship_bias_pct: float     # Anteil der damaligen Mitglieder, die naiv fehlen

    def as_dict(self) -> dict:
        return {
            "historical_count": self.historical_count,
            "current_count": self.current_count,
            "removed_since": list(self.removed_since),
            "added_since": list(self.added_since),
            "n_removed": len(self.removed_since),
            "n_added": len(self.added_since),
            "survivorship_bias_pct": self.survivorship_bias_pct,
        }


def measure_survivorship_bias(current_members, historical_members) -> SurvivorshipReport:
    """Vergleicht ein historisches Universum mit dem heutigen.

    `removed_since` (im historischen, nicht im aktuellen) sind die entscheidenden Fälle: ein
    Backtest, der nur das HEUTIGE Universum über die Vergangenheit laufen lässt, blendet genau
    diese (oft schwachen/delisteten) Titel aus → Ergebnis zu optimistisch. `survivorship_bias_pct`
    ist ihr Anteil an den historischen Mitgliedern.
    """
    current = _normalise(current_members)
    historical = _normalise(historical_members)
    removed = tuple(sorted(historical - current))
    added = tuple(sorted(current - historical))
    bias = (len(removed) / len(historical) * 100.0) if historical else 0.0
    return SurvivorshipReport(
        historical_count=len(historical),
        current_count=len(current),
        removed_since=removed,
        added_since=added,
        survivorship_bias_pct=bias,
    )
