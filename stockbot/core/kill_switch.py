"""Kill-Switch-Service (Phase 3 / RISK-006, siehe docs/Plan.md §11.5,
docs/PLAN_CHECKLIST.md Phase 3).

Reiner, IO-freier In-Prozess-Store für `KillSwitch` (`stockbot/core/domain.py`) — noch nicht an
eine DB gebunden und von KEINEM Live-Codepfad genutzt, nach demselben Muster wie
`stockbot/core/audit_log.py::AuditLog`. Ein globaler Kill-Switch blockiert ALLE Nutzer; ein
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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KillSwitchService:
    """In-Prozess-Store: je ein optionaler globaler Kill-Switch + einer je Nutzer."""

    def __init__(self) -> None:
        self._global: KillSwitch | None = None
        self._by_user: dict[int, KillSwitch] = {}

    def activate_global(
        self, *, reason: str, activated_by: str, activated_at: str | None = None
    ) -> KillSwitch:
        ks = KillSwitch(
            id=None, scope="global", active=True, reason=reason, activated_by=activated_by,
            activated_at=activated_at or _utcnow_iso())
        self._global = ks
        return ks

    def deactivate_global(
        self, *, deactivated_by: str, deactivated_at: str | None = None
    ) -> KillSwitch | None:
        """`None`, wenn gerade kein globaler Kill-Switch aktiv ist (nichts zu deaktivieren)."""
        if self._global is None or not self._global.active:
            return None
        ks = dataclasses.replace(
            self._global, active=False, deactivated_by=deactivated_by,
            deactivated_at=deactivated_at or _utcnow_iso())
        self._global = ks
        return ks

    def activate_user(
        self, user_id: int, *, reason: str, activated_by: str, activated_at: str | None = None
    ) -> KillSwitch:
        ks = KillSwitch(
            id=None, scope="user", user_id=user_id, active=True, reason=reason,
            activated_by=activated_by, activated_at=activated_at or _utcnow_iso())
        self._by_user[user_id] = ks
        return ks

    def deactivate_user(
        self, user_id: int, *, deactivated_by: str, deactivated_at: str | None = None
    ) -> KillSwitch | None:
        """`None`, wenn für `user_id` gerade kein Kill-Switch aktiv ist."""
        ks = self._by_user.get(user_id)
        if ks is None or not ks.active:
            return None
        deactivated = dataclasses.replace(
            ks, active=False, deactivated_by=deactivated_by,
            deactivated_at=deactivated_at or _utcnow_iso())
        self._by_user[user_id] = deactivated
        return deactivated

    def is_new_position_allowed(self, user_id: int | None = None) -> bool:
        """False, wenn der globale Kill-Switch aktiv ist ODER (falls `user_id` übergeben) der
        Kill-Switch dieses Nutzers aktiv ist."""
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
        return self._global

    def user_status(self, user_id: int) -> KillSwitch | None:
        return self._by_user.get(user_id)
