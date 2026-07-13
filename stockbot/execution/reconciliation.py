"""OMS-Reconciliation in drei Ebenen (Phase 4 / OMS-007, Plan.md §12.7).

Echtzeit bleibt der primäre Pfad: Brokerereignisse werden über den OMS-005-
``broker_event_worker`` verarbeitet, sobald ein echter Brokerstream-Lifecycle existiert.
Dieses Modul emuliert ``stream_order_events`` ausdrücklich nicht. Periodische Aufrufer können
alle 5–15 Minuten :func:`run_periodic_reconciliation` ausführen; der tägliche Voll-Abgleich
nutzt :func:`run_daily_full_reconciliation` und ergänzt einen menschenlesbaren Report.

Reconciliation erkennt und berichtet ausschließlich Abweichungen. Sie verändert weder
Orders noch Positionen und führt insbesondere keine automatische Heilung aus.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from stockbot.core.domain import Order, OrderStatus, Position, PositionStatus
from stockbot.execution.broker_adapter import (
    BrokerAccount,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
)


_OPEN_ORDER_STATUSES = {
    OrderStatus.SUBMITTED,
    OrderStatus.ACCEPTED_BY_BROKER,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCEL_REQUESTED,
}
_BROKER_STATUS_MAP = {
    "submitted": OrderStatus.SUBMITTED.value,
    "accepted": OrderStatus.ACCEPTED_BY_BROKER.value,
    "new": OrderStatus.ACCEPTED_BY_BROKER.value,
    "pending_new": OrderStatus.ACCEPTED_BY_BROKER.value,
    "partially_filled": OrderStatus.PARTIALLY_FILLED.value,
    "pending_cancel": OrderStatus.CANCEL_REQUESTED.value,
    "cancel_requested": OrderStatus.CANCEL_REQUESTED.value,
}
_QUANTITY_REL_TOLERANCE = 1e-9
_QUANTITY_ABS_TOLERANCE = 1e-9
DEFAULT_ACCOUNT_TOLERANCE_PCT = 0.01


@dataclass(frozen=True)
class ReconciliationFinding:
    """Eine festgestellte Abweichung zwischen interner und Broker-Sicht."""

    kind: str
    severity: str = "warning"
    symbol: str | None = None
    internal_order_id: int | None = None
    broker_order_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationReport:
    """Zeitlich abgegrenztes Ergebnis eines periodischen oder täglichen Abgleichs."""

    findings: list[ReconciliationFinding]
    started_at: datetime
    finished_at: datetime
    text_report: str | None = None

    @property
    def ok(self) -> bool:
        return not self.findings


def _symbol(value: str) -> str:
    return value.strip().upper()


def _quantities_close(left: float, right: float) -> bool:
    return math.isclose(
        float(left), float(right),
        rel_tol=_QUANTITY_REL_TOLERANCE,
        abs_tol=_QUANTITY_ABS_TOLERANCE,
    )


def reconcile_positions(
    internal_positions: list[Position],
    broker_positions: list[BrokerPosition],
) -> list[ReconciliationFinding]:
    """Vergleiche nicht geschlossene interne Positionen mit der aggregierten Broker-Sicht."""

    internal_qty: dict[str, float] = defaultdict(float)
    broker_qty: dict[str, float] = defaultdict(float)
    for position in internal_positions:
        if position.status == PositionStatus.CLOSED:
            continue
        internal_qty[_symbol(position.ticker)] += float(position.qty)
    for position in broker_positions:
        broker_qty[_symbol(position.symbol)] += float(position.qty)

    findings: list[ReconciliationFinding] = []
    for symbol in sorted(broker_qty.keys() - internal_qty.keys()):
        findings.append(ReconciliationFinding(
            kind="unknown_broker_position",
            symbol=symbol,
            details={"broker_qty": broker_qty[symbol]},
        ))
    for symbol in sorted(internal_qty.keys() - broker_qty.keys()):
        findings.append(ReconciliationFinding(
            kind="missing_broker_position",
            symbol=symbol,
            details={"internal_qty": internal_qty[symbol]},
        ))
    for symbol in sorted(internal_qty.keys() & broker_qty.keys()):
        if not _quantities_close(internal_qty[symbol], broker_qty[symbol]):
            findings.append(ReconciliationFinding(
                kind="qty_mismatch",
                symbol=symbol,
                details={
                    "entity": "position",
                    "internal_qty": internal_qty[symbol],
                    "broker_qty": broker_qty[symbol],
                },
            ))
    return findings


def _order_status_value(status: OrderStatus | str) -> str:
    return status.value if isinstance(status, OrderStatus) else str(status).strip().lower()


def _matching_broker_order(
    internal: Order,
    broker_orders: list[BrokerOrder],
    unmatched_indices: set[int],
) -> int | None:
    if internal.broker_order_id:
        for index in sorted(unmatched_indices):
            if broker_orders[index].order_id == internal.broker_order_id:
                return index
    if internal.client_order_id:
        for index in sorted(unmatched_indices):
            if broker_orders[index].client_order_id == internal.client_order_id:
                return index
    return None


def reconcile_orders(
    internal_orders: list[Order],
    broker_orders: list[BrokerOrder],
) -> list[ReconciliationFinding]:
    """Vergleiche offene interne Orders mit offenen Brokerorders anhand stabiler IDs."""

    open_internal = [order for order in internal_orders if order.status in _OPEN_ORDER_STATUSES]
    unmatched_broker = set(range(len(broker_orders)))
    findings: list[ReconciliationFinding] = []

    for order in open_internal:
        broker_index = _matching_broker_order(order, broker_orders, unmatched_broker)
        if broker_index is None:
            findings.append(ReconciliationFinding(
                kind="missing_broker_order",
                symbol=_symbol(order.ticker),
                internal_order_id=order.id,
                broker_order_id=order.broker_order_id,
                details={"client_order_id": order.client_order_id},
            ))
            continue

        unmatched_broker.remove(broker_index)
        broker_order = broker_orders[broker_index]
        internal_status = _order_status_value(order.status)
        broker_status = _BROKER_STATUS_MAP.get(
            broker_order.status.strip().lower(), broker_order.status.strip().lower())
        if broker_status and broker_status != internal_status:
            findings.append(ReconciliationFinding(
                kind="order_state_mismatch",
                symbol=_symbol(order.ticker or broker_order.symbol),
                internal_order_id=order.id,
                broker_order_id=broker_order.order_id,
                details={
                    "internal_status": internal_status,
                    "broker_status": broker_order.status,
                },
            ))
        if (order.qty is not None and broker_order.qty is not None
                and not _quantities_close(order.qty, broker_order.qty)):
            findings.append(ReconciliationFinding(
                kind="qty_mismatch",
                symbol=_symbol(order.ticker or broker_order.symbol),
                internal_order_id=order.id,
                broker_order_id=broker_order.order_id,
                details={
                    "entity": "order",
                    "internal_qty": float(order.qty),
                    "broker_qty": float(broker_order.qty),
                },
            ))

    for index in sorted(unmatched_broker):
        broker_order = broker_orders[index]
        findings.append(ReconciliationFinding(
            kind="unknown_broker_order",
            symbol=_symbol(broker_order.symbol) if broker_order.symbol else None,
            broker_order_id=broker_order.order_id,
            details={
                "client_order_id": broker_order.client_order_id,
                "broker_status": broker_order.status,
            },
        ))
    return findings


def _outside_percentage_tolerance(actual: float, expected: float, tolerance_pct: float) -> bool:
    if tolerance_pct < 0:
        raise ValueError("tolerance_pct darf nicht negativ sein.")
    allowed_difference = abs(float(expected)) * float(tolerance_pct) / 100.0
    return abs(float(actual) - float(expected)) > allowed_difference


def reconcile_account(
    expected_cash: float | None,
    expected_buying_power: float | None,
    account: BrokerAccount,
    *,
    tolerance_pct: float,
) -> list[ReconciliationFinding]:
    """Prüfe Kontowerte nur, wenn der Aufrufer dafür Erwartungswerte bereitstellt."""

    findings: list[ReconciliationFinding] = []
    if (expected_cash is not None
            and _outside_percentage_tolerance(account.cash, expected_cash, tolerance_pct)):
        findings.append(ReconciliationFinding(
            kind="cash_mismatch",
            details={
                "expected": float(expected_cash),
                "broker": float(account.cash),
                "tolerance_pct": float(tolerance_pct),
                "currency": account.currency,
            },
        ))
    if (expected_buying_power is not None
            and _outside_percentage_tolerance(
                account.buying_power, expected_buying_power, tolerance_pct)):
        findings.append(ReconciliationFinding(
            kind="buying_power_mismatch",
            details={
                "expected": float(expected_buying_power),
                "broker": float(account.buying_power),
                "tolerance_pct": float(tolerance_pct),
                "currency": account.currency,
            },
        ))
    return findings


def _run_reconciliation(
    adapter: BrokerAdapter,
    *,
    internal_positions: list[Position],
    internal_orders: list[Order],
    expected_cash: float | None,
    expected_buying_power: float | None,
    tolerance_pct: float,
) -> tuple[list[ReconciliationFinding], datetime, datetime]:
    started_at = datetime.now(timezone.utc)
    broker_orders = adapter.list_open_orders()
    broker_positions = adapter.list_positions()
    account = adapter.get_account()
    findings = reconcile_positions(internal_positions, broker_positions)
    findings.extend(reconcile_orders(internal_orders, broker_orders))
    findings.extend(reconcile_account(
        expected_cash, expected_buying_power, account, tolerance_pct=tolerance_pct))
    return findings, started_at, datetime.now(timezone.utc)


def run_periodic_reconciliation(
    adapter: BrokerAdapter,
    *,
    internal_positions: list[Position],
    internal_orders: list[Order],
    expected_cash: float | None = None,
    expected_buying_power: float | None = None,
    tolerance_pct: float = DEFAULT_ACCOUNT_TOLERANCE_PCT,
) -> ReconciliationReport:
    """Hole einen Broker-Snapshot und bündele Positions-, Order- und Kontobefunde."""

    findings, started_at, finished_at = _run_reconciliation(
        adapter,
        internal_positions=internal_positions,
        internal_orders=internal_orders,
        expected_cash=expected_cash,
        expected_buying_power=expected_buying_power,
        tolerance_pct=tolerance_pct,
    )
    return ReconciliationReport(
        findings=findings, started_at=started_at, finished_at=finished_at)


def _format_report(findings: list[ReconciliationFinding]) -> str:
    if not findings:
        return "Täglicher Reconciliation-Abgleich: Keine Abweichung."
    lines = [f"Täglicher Reconciliation-Abgleich: {len(findings)} Abweichung(en)"]
    for finding in findings:
        identity = finding.symbol or finding.broker_order_id or finding.internal_order_id or "Konto"
        lines.append(f"• {finding.kind}: {identity} — {finding.details}")
    return "\n".join(lines)


def run_daily_full_reconciliation(
    adapter: BrokerAdapter,
    *,
    internal_positions: list[Position],
    internal_orders: list[Order],
    expected_cash: float | None = None,
    expected_buying_power: float | None = None,
    tolerance_pct: float = DEFAULT_ACCOUNT_TOLERANCE_PCT,
) -> ReconciliationReport:
    """Führe den vollständigen Snapshot-Abgleich aus und liefere einen Textreport mit."""

    findings, started_at, finished_at = _run_reconciliation(
        adapter,
        internal_positions=internal_positions,
        internal_orders=internal_orders,
        expected_cash=expected_cash,
        expected_buying_power=expected_buying_power,
        tolerance_pct=tolerance_pct,
    )
    return ReconciliationReport(
        findings=findings,
        started_at=started_at,
        finished_at=finished_at,
        text_report=_format_report(findings),
    )
