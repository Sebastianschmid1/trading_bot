"""Paper-Burn-in-Auswertung (W8, Gate P10).

Der Burn-in selbst ist Kalenderzeit — messbar ist er trotzdem. Dieses Modul zieht die
Belege, die Gate P10 verlangt, aus den bereits vorhandenen Tabellen (`orders`,
`order_events`, `outbox_events`) und macht daraus einen abzeichenbaren Report:

* **Fehlerquote** = abgelehnte Orders / eingereichte Orders,
* **doppelte Orders** — zwei Order-Zeilen zu einem Idempotency-Key (muss 0 sein),
* **doppelte Broker-Events** — dieselbe `broker_event_id` mehrfach verbucht (muss 0 sein),
* **Dead-Letter-Events** aus der Outbox (unzugestellte Domänen-Events).

Es wird nichts geschätzt: fehlen Daten, steht dort ``None``/0 und der Report ist
schlicht noch nicht aussagekräftig (``orders_submitted``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from stockbot.core import db

# Terminale Zustände, die eine Order als „nicht ausgeführt" kennzeichnen
# (identisch zur Filterung in `db.burn_in_order_stats`).
FAILED_ORDER_STATUSES = ("rejected", "expired")


@dataclass(frozen=True)
class BurnInReport:
    """Belege für das menschliche Go/No-Go (Tor T5)."""

    since: str
    until: str
    orders_submitted: int
    orders_failed: int
    error_rate: float | None          # None, solange keine Order eingereicht wurde
    duplicate_orders: int
    duplicate_broker_events: int
    dead_letter_events: int
    reconciliation_findings: int

    @property
    def clean(self) -> bool:
        """Gate P10: keine doppelten Orders, keine unzugestellten Events, keine offenen Abweichungen."""
        return (self.duplicate_orders == 0
                and self.duplicate_broker_events == 0
                and self.dead_letter_events == 0
                and self.reconciliation_findings == 0)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["clean"] = self.clean
        return data


def build_burn_in_report(since: str, until: str, *,
                         reconciliation_findings: int = 0,
                         persistence=db) -> BurnInReport:
    """Wertet den Zeitraum ``[since, until]`` (naive UTC-Strings) aus.

    ``reconciliation_findings`` kommt von außen, weil Abweichungen gegen die Broker-Sicht
    laufen und nicht in einer eigenen Tabelle liegen (siehe `execution/reconciliation.py`).
    """
    stats = persistence.burn_in_order_stats(since, until)
    submitted = int(stats["submitted"])
    failed = int(stats["failed"])
    return BurnInReport(
        since=since,
        until=until,
        orders_submitted=submitted,
        orders_failed=failed,
        error_rate=(failed / submitted) if submitted else None,
        duplicate_orders=int(stats["duplicate_orders"]),
        duplicate_broker_events=int(stats["duplicate_broker_events"]),
        dead_letter_events=int(stats["dead_letter_events"]),
        reconciliation_findings=int(reconciliation_findings),
    )


def format_burn_in_report(report: BurnInReport) -> str:
    """Kurzfassung für Log/Telegram/Go-No-Go-Protokoll."""
    rate = "—" if report.error_rate is None else f"{report.error_rate * 100:.2f} %"
    lines = [
        f"Paper-Burn-in {report.since} … {report.until}",
        f"  Orders eingereicht:      {report.orders_submitted}",
        f"  davon abgelehnt:         {report.orders_failed}  (Fehlerquote {rate})",
        f"  doppelte Orders:         {report.duplicate_orders}",
        f"  doppelte Broker-Events:  {report.duplicate_broker_events}",
        f"  Dead-Letter-Events:      {report.dead_letter_events}",
        f"  Reconciliation-Befunde:  {report.reconciliation_findings}",
        f"  Gate P10: {'sauber' if report.clean else 'NICHT sauber — Befunde klären'}",
    ]
    return "\n".join(lines)
