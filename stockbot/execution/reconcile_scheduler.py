"""Verdrahtung der periodischen OMS-Reconciliation mit Persistenz und Alarmierung."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from stockbot.core import db, metrics
from stockbot.core.domain import Order, Position
from stockbot.execution.broker_adapter import AlpacaBrokerAdapter
from stockbot.execution.oms import _as_order
from stockbot.execution.post_trade_scan import _as_position
from stockbot.execution.reconciliation import (
    ReconciliationReport,
    run_periodic_reconciliation,
)


log = logging.getLogger(__name__)


def build_internal_state(
    user_id: int, *, persistence: Any = db,
) -> tuple[list[Position], list[Order]]:
    """Lädt die offenen Positionen und OMS-Orders eines Nutzers über den DB-Seam."""
    positions = [_as_position(row) for row in persistence.get_active_trades(user_id)]
    orders = [
        _as_order(row)
        for row in persistence.get_open_oms_orders()
        if int(row["user_id"]) == int(user_id)
    ]
    return positions, orders


def _user_id(user: Any) -> int:
    if isinstance(user, Mapping):
        return int(user["user_id"])
    return int(user.user_id)


def reconcile_user_oms(
    user: Any, client: Any, *, persistence: Any = db,
) -> ReconciliationReport:
    """Führt den rein lesenden OMS-Abgleich aus; Brokerfehler bleiben nutzerlokal."""
    user_id = _user_id(user)
    try:
        positions, orders = build_internal_state(user_id, persistence=persistence)
        report = run_periodic_reconciliation(
            AlpacaBrokerAdapter(client),
            internal_positions=positions,
            internal_orders=orders,
        )
        metrics.RECONCILIATION_FINDINGS_TOTAL.inc(len(report.findings))
        return report
    except Exception as exc:
        log.warning(
            "[%s] OMS-Reconciliation fehlgeschlagen: %r",
            user_id,
            exc,
            exc_info=True,
        )
        now = datetime.now(timezone.utc)
        return ReconciliationReport(findings=[], started_at=now, finished_at=now)


def format_admin_alarm(reports: Mapping[int, ReconciliationReport]) -> str | None:
    """Baut genau eine Adminmeldung aus den Findings mehrerer Nutzer."""
    lines = ["⚠️ OMS-Reconciliation: Abweichungen erkannt"]
    for user_id, report in reports.items():
        lines.extend(
            f"• User {user_id}: {finding.symbol or 'Konto'} — {finding.kind}"
            for finding in report.findings
        )
    return "\n".join(lines) if len(lines) > 1 else None


def finding_keys(reports: Mapping[int, ReconciliationReport]) -> set[tuple]:
    """Liefert stabile Schlüssel für die Alarm-Deduplizierung zwischen Jobläufen."""
    return {
        (
            int(user_id), finding.kind, finding.symbol,
            finding.internal_order_id, finding.broker_order_id,
            repr(sorted(finding.details.items())),
        )
        for user_id, report in reports.items()
        for finding in report.findings
    }
