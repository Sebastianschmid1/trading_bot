"""Kill-Switch-Service (Phase 3 / RISK-006, siehe docs/Plan.md §11.5,
docs/PLAN_CHECKLIST.md Phase 3).

Ein globaler Kill-Switch blockiert ALLE Nutzer; ein
User-Kill-Switch nur den betroffenen Nutzer — `is_new_position_allowed` prüft beide.

Schutz-Exits (Verkäufe/Positionsschließungen) bleiben laut Konzept §17.4 IMMER erlaubt, auch bei
aktivem Kill-Switch — `is_protective_exit_allowed` liefert daher bewusst immer `True`. Die
Funktion existiert als expliziter, benannter Gegenpol zu `is_new_position_allowed` (Plan.md
verlangt beide Namen als Teil des Service-Interfaces), nicht als Platzhalter für eine künftige
Sperre.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from stockbot.core.domain import KillSwitch
from stockbot.core import metrics


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KillSwitchService:
    """Kill-Switch-Store, optional an den persistenten DB-Seam gebunden."""

    def __init__(self, persistence=None, *, load_on_init: bool = True) -> None:
        self._global: KillSwitch | None = None
        self._by_user: dict[int, KillSwitch] = {}
        self._persistence = persistence
        self._loaded = persistence is None
        if persistence is not None and load_on_init:
            self._load_active()

    def reload(self) -> None:
        """Lädt den aktiven Zustand nach abgeschlossener DB-Initialisierung neu."""
        if self._persistence is not None:
            self._load_active()

    @staticmethod
    def _from_row(row: dict) -> KillSwitch:
        return KillSwitch(
            id=int(row["id"]), scope=str(row["scope"]), active=bool(row["active"]),
            reason=str(row["reason"]), activated_by=str(row["activated_by"]),
            activated_at=str(row["activated_at"]), user_id=row["user_id"],
            deactivated_by=row["deactivated_by"], deactivated_at=row["deactivated_at"],
        )

    def _load_active(self) -> None:
        self._global = None
        self._by_user = {}
        for row in self._persistence.get_active_kill_switches():
            ks = self._from_row(row)
            if ks.scope == "global":
                self._global = ks
            elif ks.user_id is not None:
                self._by_user[int(ks.user_id)] = ks
        self._loaded = True
        self._sync_metrics()

    def _sync_metrics(self) -> None:
        metrics.KILL_SWITCH_ACTIVE.set(bool(self._global and self._global.active))
        metrics.KILL_SWITCH_USERS.set(sum(1 for item in self._by_user.values() if item.active))

    def _ensure_loaded(self) -> None:
        if not self._loaded and self._persistence is not None:
            self._load_active()

    def activate_global(
        self, *, reason: str, activated_by: str, activated_at: str | None = None
    ) -> KillSwitch:
        self._ensure_loaded()
        ks = KillSwitch(
            id=None, scope="global", active=True, reason=reason, activated_by=activated_by,
            activated_at=activated_at or _utcnow_iso())
        if self._persistence is not None:
            ks = self._from_row(self._persistence.activate_kill_switch(
                scope="global", user_id=None, reason=reason, activated_by=activated_by,
                activated_at=ks.activated_at,
            ))
        self._global = ks
        self._sync_metrics()
        return ks

    def deactivate_global(
        self, *, deactivated_by: str, deactivated_at: str | None = None
    ) -> KillSwitch | None:
        """`None`, wenn gerade kein globaler Kill-Switch aktiv ist (nichts zu deaktivieren)."""
        self._ensure_loaded()
        if self._global is None or not self._global.active:
            return None
        ks = dataclasses.replace(
            self._global, active=False, deactivated_by=deactivated_by,
            deactivated_at=deactivated_at or _utcnow_iso())
        if self._persistence is not None:
            row = self._persistence.deactivate_kill_switch(
                scope="global", user_id=None, deactivated_by=deactivated_by,
                deactivated_at=ks.deactivated_at,
            )
            if row is None:
                return None
            ks = self._from_row(row)
        self._global = ks
        self._sync_metrics()
        return ks

    def activate_user(
        self, user_id: int, *, reason: str, activated_by: str, activated_at: str | None = None
    ) -> KillSwitch:
        self._ensure_loaded()
        ks = KillSwitch(
            id=None, scope="user", user_id=user_id, active=True, reason=reason,
            activated_by=activated_by, activated_at=activated_at or _utcnow_iso())
        if self._persistence is not None:
            ks = self._from_row(self._persistence.activate_kill_switch(
                scope="user", user_id=user_id, reason=reason, activated_by=activated_by,
                activated_at=ks.activated_at,
            ))
        self._by_user[user_id] = ks
        self._sync_metrics()
        return ks

    def deactivate_user(
        self, user_id: int, *, deactivated_by: str, deactivated_at: str | None = None
    ) -> KillSwitch | None:
        """`None`, wenn für `user_id` gerade kein Kill-Switch aktiv ist."""
        self._ensure_loaded()
        ks = self._by_user.get(user_id)
        if ks is None or not ks.active:
            return None
        deactivated = dataclasses.replace(
            ks, active=False, deactivated_by=deactivated_by,
            deactivated_at=deactivated_at or _utcnow_iso())
        if self._persistence is not None:
            row = self._persistence.deactivate_kill_switch(
                scope="user", user_id=user_id, deactivated_by=deactivated_by,
                deactivated_at=deactivated.deactivated_at,
            )
            if row is None:
                return None
            deactivated = self._from_row(row)
        self._by_user[user_id] = deactivated
        self._sync_metrics()
        return deactivated

    def is_new_position_allowed(self, user_id: int | None = None) -> bool:
        """False, wenn der globale Kill-Switch aktiv ist ODER (falls `user_id` übergeben) der
        Kill-Switch dieses Nutzers aktiv ist."""
        if self._persistence is not None:
            self._load_active()
        if self._global is not None and self._global.active:
            return False
        if user_id is not None:
            ks = self._by_user.get(user_id)
            if ks is not None and ks.active:
                return False
        return True

    def is_protective_exit_allowed(self, user_id: int | None = None) -> bool:
        """Immer `True` (Konzept §17.4) — Schutz-Exits umgehen den Kill-Switch bewusst."""
        return True

    @property
    def global_status(self) -> KillSwitch | None:
        if self._persistence is not None:
            self._load_active()
        return self._global

    def user_status(self, user_id: int) -> KillSwitch | None:
        if self._persistence is not None:
            self._load_active()
        return self._by_user.get(user_id)
