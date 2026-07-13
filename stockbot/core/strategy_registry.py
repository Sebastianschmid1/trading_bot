"""Append-only Strategieversionierung (Phase 5 / STRAT-003, Plan.md §13.5).

Der Store ist bewusst nur ein IO-freier In-Prozess-Seam. Eine dauerhafte, über mehrere
Prozesse konsistente Ablage folgt erst mit dem dokumentierten Postgres-Cutover. Wie beim
``AuditLog`` gibt es keine API zum Ändern oder Löschen veröffentlichter Einträge.

Promotions verändern den veröffentlichten Snapshot nicht. Ihr effektiver Status wird als
separate, append-only Historie geführt und jede Änderung verlangt einen menschlichen Actor.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from stockbot.core.domain import StrategyVersion


RELEASE_STATUSES = ("draft", "candidate", "shadow", "paper", "live", "archived")
_FORWARD_STATUS = {
    "draft": "candidate",
    "candidate": "shadow",
    "shadow": "paper",
    "paper": "live",
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _freeze(value: Any) -> Any:
    """Kopiert verschachtelte Container in unveränderliche Entsprechungen."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class StrategyStatusChange:
    """Ein menschlich bestätigtes, unveränderliches Promotion-Ereignis."""

    strategy_id: int
    version: int
    old_status: str
    new_status: str
    actor: str
    changed_at: str


class StrategyVersionRegistry:
    """In-Prozess-Registry für unveränderliche Strategie-Snapshots."""

    def __init__(self) -> None:
        self._versions: dict[int, list[StrategyVersion]] = {}
        self._status_changes: dict[tuple[int, int], list[StrategyStatusChange]] = {}
        self._used_ids: set[int] = set()
        self._next_id = 1

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in RELEASE_STATUSES:
            raise ValueError(
                f"Unbekannter Release-Status {status!r}; erlaubt: {', '.join(RELEASE_STATUSES)}"
            )

    def _new_id(self) -> int:
        while self._next_id in self._used_ids:
            self._next_id += 1
        result = self._next_id
        self._used_ids.add(result)
        self._next_id += 1
        return result

    def publish(self, version: StrategyVersion) -> StrategyVersion:
        """Veröffentlicht einen tief eingefrorenen Snapshot als nächste Version.

        Versionsnummern gehören der Registry: auch das erneute Publizieren desselben
        Templates erzeugt die nächste Nummer. Eine bereits verwendete ID wird ebenfalls
        nicht wiederverwendet.
        """
        self._validate_status(version.release_status)
        versions = self._versions.setdefault(version.strategy_id, [])
        next_version = versions[-1].version + 1 if versions else 1
        # Ein expliziter Startwert darf beim ersten Import erhalten bleiben. Danach sind nur
        # lückenlose Nummern möglich; ein erneut publiziertes (altes) Template bekommt immer die
        # nächste Nummer statt einen vorhandenen Snapshot zu überschreiben.
        if not versions and isinstance(version.version, int) and version.version > 0:
            next_version = version.version

        published_id = version.id
        if published_id is None or published_id in self._used_ids:
            published_id = self._new_id()
        else:
            self._used_ids.add(published_id)
            self._next_id = max(self._next_id, published_id + 1)

        published = dataclasses.replace(
            version,
            id=published_id,
            version=next_version,
            params=_freeze(version.params),
            cost_model=_freeze(version.cost_model),
            created_at=version.created_at or _utcnow_iso(),
        )
        versions.append(published)
        return published

    def get(self, strategy_id: int, version: int) -> StrategyVersion:
        for item in self._versions.get(strategy_id, ()):
            if item.version == version:
                return item
        raise KeyError((strategy_id, version))

    def latest(self, strategy_id: int) -> StrategyVersion:
        versions = self._versions.get(strategy_id)
        if not versions:
            raise KeyError(strategy_id)
        return versions[-1]

    def all_versions(self, strategy_id: int) -> tuple[StrategyVersion, ...]:
        return tuple(self._versions.get(strategy_id, ()))

    def effective_status(self, strategy_id: int, version: int) -> str:
        """Aktueller Status aus Snapshot plus append-only Promotion-Historie."""
        history = self._status_changes.get((strategy_id, version), ())
        return history[-1].new_status if history else self.get(strategy_id, version).release_status

    def promote(
        self, strategy_id: int, version: int, new_status: str, actor: str
    ) -> StrategyVersion:
        """Protokolliert genau einen Vorwärtsschritt (oder Archivierung) und liefert eine Sicht.

        Die zurückgegebene Instanz trägt den effektiven Status; der mit ``get`` erreichbare
        veröffentlichte Snapshot wird weder ersetzt noch verändert.
        """
        self._validate_status(new_status)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("Promotion erfordert einen nicht-leeren actor")

        published = self.get(strategy_id, version)
        old_status = self.effective_status(strategy_id, version)
        if new_status != "archived" and _FORWARD_STATUS.get(old_status) != new_status:
            raise ValueError(f"Statusübergang {old_status!r} -> {new_status!r} ist nicht erlaubt")
        if old_status == "archived":
            raise ValueError("Eine archivierte Strategieversion kann nicht promoviert werden")

        change = StrategyStatusChange(
            strategy_id=strategy_id,
            version=version,
            old_status=old_status,
            new_status=new_status,
            actor=actor.strip(),
            changed_at=_utcnow_iso(),
        )
        self._status_changes.setdefault((strategy_id, version), []).append(change)
        return dataclasses.replace(published, release_status=new_status)

    def status_history(
        self, strategy_id: int, version: int
    ) -> tuple[StrategyStatusChange, ...]:
        """Read-only Sicht auf Promotion-Ereignisse in Einfügereihenfolge."""
        self.get(strategy_id, version)  # Unbekannte Versionen nicht als leere Historie tarnen.
        return tuple(self._status_changes.get((strategy_id, version), ()))

    def snapshot_from_registry(
        self,
        strategy_key: str,
        *,
        code_commit: str,
        params: dict | None = None,
        strategy_id: int | None = None,
        feature_version: str = "unversioned",
        universe_version: str = "unversioned",
        cost_model: dict | None = None,
        release_status: str = "draft",
        entry_rules: str | None = None,
        exit_rules: str | None = None,
    ) -> StrategyVersion:
        """Baut ein noch unveröffentlichtes Template aus einer V1-Produktionsstrategie.

        Ohne persistente Strategy-Tabelle dient die stabile 1-basierte Position im bestehenden
        ``REGISTRY`` vorläufig als In-Prozess-``strategy_id``. Aufrufer mit einer echten ID können
        sie explizit übergeben. Auch diese Zuordnung wird erst beim Postgres-Cutover persistent.
        """
        # Lazy import hält den Core-Service unabhängig vom schweren Market-Modul, solange kein
        # Registry-Snapshot angefordert wird.
        from stockbot.market import strategies

        strategy = strategies.REGISTRY.get(strategy_key)
        if strategy is None:
            raise KeyError(strategy_key)
        if not strategy.production:
            raise ValueError(f"{strategy_key!r} ist keine produktive V1-Strategie")
        self._validate_status(release_status)

        resolved_id = strategy_id
        if resolved_id is None:
            resolved_id = tuple(strategies.REGISTRY).index(strategy_key) + 1
        resolved_params = (
            strategies.strategy_runtime_params(strategy_key) if params is None else params
        )
        implementation = f"siehe stockbot.market.strategies::{strategy.generate.__name__}"
        return StrategyVersion(
            id=None,
            strategy_id=resolved_id,
            version=0,
            params=dict(resolved_params),
            feature_version=feature_version,
            universe_version=universe_version,
            entry_rules=entry_rules if entry_rules is not None else strategy.description,
            exit_rules=exit_rules if exit_rules is not None else implementation,
            cost_model=dict(cost_model or {}),
            release_status=release_status,
            code_commit=code_commit,
            created_at=None,
        )
