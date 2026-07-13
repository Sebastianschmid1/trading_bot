"""Post-Trade-Risk (Phase 3 / RISK-005, siehe docs/Plan.md §11.4).

Reine, IO-freie Prüfung, ob eine offene Position durch mindestens eine aktive Exit-Order
geschützt ist. Die Liste der zugehörigen Orders wird vom Aufrufer bereitgestellt; dieses Modul
fragt weder Datenbank noch Broker ab. Da das aktuelle Domänenmodell an ``Order`` noch keinen
Ordertyp (Stop-Loss/Take-Profit/Liquidation) speichert, gilt eine aktive Order auf der
Gegenseite der Position als Schutzorder. Bei Long-Positionen (positive Menge) ist das eine
Verkaufsorder, bei Short-Positionen (negative Menge) eine Kauforder.

Noch von KEINEM Live-Codepfad genutzt. Die anderen Punkte aus Plan.md §11.4 (aktive Stops,
Tagesverlustlimit fortschreiben, Exposure laufend berechnen und Abweichungen alarmieren) sind
bewusst NICHT Teil dieses Moduls.
"""

from __future__ import annotations

from dataclasses import dataclass

from stockbot.core.domain import Order, OrderStatus, Position, PositionStatus


@dataclass(frozen=True)
class PostTradeRiskDecision:
    """Ergebnis des Post-Trade-Checks. ``ok=False`` bedeutet: Schutzorder fehlt."""

    ok: bool
    reason: str = ""
    code: str = ""


_OK = PostTradeRiskDecision(ok=True)

_ACTIVE_ORDER_STATUSES = frozenset({
    OrderStatus.SUBMITTED,
    OrderStatus.ACCEPTED_BY_BROKER,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.CANCEL_REQUESTED,
})


def check_open_position_has_protective_order(
    *, position: Position, active_orders: tuple[Order, ...],
) -> PostTradeRiskDecision:
    """Erkennt eine offene Position ohne Schutzorder.

    Nur ``PositionStatus.OPEN`` ist anwendbar. Eine Schutzorder muss zum selben Nutzer und
    Ticker gehören, einen aktiven Orderstatus haben und die Position verkleinern: ``sell`` für
    Long (``qty > 0``), ``buy`` für Short (``qty < 0``). Terminale Orders sowie lediglich
    lokal erstellte/validierte, aber noch nicht eingereichte Orders schützen nicht. Eine
    stornierungsangefragte Order gilt bis zur bestätigten Stornierung weiterhin als aktiv.

    Die Funktion prüft bewusst nur, ob mindestens eine Schutzorder vorhanden ist, nicht deren
    Preis, Typ oder Mengenabdeckung: Diese Informationen sind im aktuellen ``Order``-Typ nicht
    vollständig modelliert und gehören daher in einen späteren Domain-/Wiring-Schritt.
    """
    if position.status != PositionStatus.OPEN:
        return _OK
    if position.qty == 0:
        return PostTradeRiskDecision(
            False,
            f"Offene Position {position.ticker} hat keine von null verschiedene Menge.",
            "invalid_open_position_qty",
        )

    protective_side = "sell" if position.qty > 0 else "buy"
    for order in active_orders:
        if (order.user_id == position.user_id
                and order.ticker == position.ticker
                and order.side.lower() == protective_side
                and order.status in _ACTIVE_ORDER_STATUSES):
            return _OK

    return PostTradeRiskDecision(
        False,
        f"Offene Position {position.ticker} hat keine aktive Schutzorder.",
        "protective_order_missing",
    )
