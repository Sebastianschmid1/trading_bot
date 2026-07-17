"""Lesender Post-Trade-Scan und gebündelte Admin-Alarmierung."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from stockbot.core import db, metrics
from stockbot.core.domain import Mode, Position, PositionStatus
from stockbot.core.post_trade_risk import PostTradeRiskFinding, scan_open_positions
from stockbot.execution.oms import _as_order


def _as_position(row: Mapping[str, Any]) -> Position:
    """Mappt einen aktiven Legacy-Trade auf die bestehende Position-Domäne."""
    signal = json.loads(row.get("signal_json") or "{}")
    direction = str(row.get("direction") or signal.get("direction") or "long").lower()
    qty = float(row.get("broker_filled_qty") or signal.get("qty") or 1.0)
    if direction == "short":
        qty = -abs(qty)
    else:
        qty = abs(qty)
    return Position(
        id=int(row["id"]), user_id=int(row["user_id"]), ticker=str(row["ticker"]),
        strategy_version_id=int(signal.get("strategy_version_id") or 0),
        mode=Mode(signal.get("mode", Mode.PAPER)), status=PositionStatus.OPEN, qty=qty,
        avg_entry_price=row.get("entry"), opened_at=row.get("created_at"),
    )


def load_post_trade_risk_data(*, persistence: Any = db) -> tuple[tuple[Position, ...], tuple]:
    """Lädt und mappt die Scan-Daten; der produktive Zugriff läuft über den DB-Seam."""
    position_rows, order_rows = persistence.get_post_trade_risk_rows()
    positions = tuple(_as_position(row) for row in position_rows)
    orders = tuple(_as_order(row) for row in order_rows)
    return positions, orders


def finding_keys(findings: list[PostTradeRiskFinding]) -> set[tuple[int, str, str]]:
    return {(item.user_id, item.ticker, item.decision.code) for item in findings}


def format_admin_alarm(findings: list[PostTradeRiskFinding]) -> str:
    lines = ["🛑 Post-Trade-Risiko: Positionen ohne aktive Schutzorder"]
    lines.extend(
        f"• User {item.user_id}: {item.ticker} — {item.decision.reason}"
        for item in findings
    )
    return "\n".join(lines)


async def run_post_trade_scan(
    *, persistence: Any = db,
    notifier: Callable[[str], Awaitable[None]],
    previous_findings: set[tuple[int, str, str]],
) -> list[PostTradeRiskFinding]:
    """Führt einen Scan aus und alarmiert einmal je veränderter Finding-Menge."""
    positions, orders = load_post_trade_risk_data(persistence=persistence)
    findings = scan_open_positions(positions, orders)
    metrics.POSITIONS_WITHOUT_STOP.set(len(findings))
    current = finding_keys(findings)
    if findings and current != previous_findings:
        await notifier(format_admin_alarm(findings))
    previous_findings.clear()
    previous_findings.update(current)
    return findings
