"""Gemeinsames Web ↔ Telegram-Glossar (Stylekonzept.md §32.9).

Einzige Quelle für die geteilten **Status-, Modus- und Aktionsbegriffe**, damit Web-App und
Telegram-Bot denselben Zustand *identisch* benennen (DoD §30, ergänzt §25/§26/§30).
Divergierende Formulierungen für denselben Zustand sind laut §32.9 ein Defekt — deshalb
leben diese Strings hier zentral und werden nicht in `web/dashboard.py`, den Templates oder
`tgbot/bot.py` dupliziert.

Reine Anzeige-Strings, **keine Logik**: nichts hier ändert Verhalten, Order-Routing oder
Risiko-Pfade. Terminologie folgt §25.2 (sachlich, kein Ticket-/Fachjargon).
"""
from __future__ import annotations

# ── Aktionsbegriffe (§11.2/§12.4) ───────────────────────────────────────────
#: Primäre Einstiegs-Aktion — bewusst „prüfen" statt „kaufen" (§11.2, §32.11).
ACTION_REVIEW_TRADE = "Trade prüfen"

# ── Risk-Gate / Ablehnung (§25.2) ───────────────────────────────────────────
#: Generischer Ablehntext, wenn das Risk-Gate einen Einstieg blockiert.
RISK_BLOCKED = "Durch Risikoregel blockiert"
#: Abgelaufenes Signal — identisch zur Risk-/OMS-Begründung.
SIGNAL_EXPIRED = "Signal ist abgelaufen."

# ── Modus (§5) ──────────────────────────────────────────────────────────────
#: Betriebsmodus, Schlüssel = ``core.domain.Mode``-Werte.
MODE_LABELS = {
    "backtest": "Backtest",
    "shadow": "Shadow",
    "paper": "Paper",
    "live": "Live",
}

# ── Trade-Status (§25.2) ────────────────────────────────────────────────────
TRADE_STATUS_LABELS = {
    "active": "Aktiv",
    "broker_pending": "Broker wartet",
    "broker_closing": "Verkauf läuft",
    "broker_failed": "Broker fehlgeschlagen",
    "closed": "Geschlossen",
}

# ── Broker-/Order-Status (§25.2) ────────────────────────────────────────────
# Ablehnungsgründe im UI sichtbar (PLAN_CHECKLIST.md Phase 3): Terminologie nach
# Stylekonzept.md §25.2 („Durch Risikoregel blockiert" statt Ticket-ID/Fachjargon).
BROKER_STATUS_LABELS = {
    "accepted": "Broker hat angenommen",
    "filled": "Ausgeführt",
    "canceled": "Abgebrochen",
    "rejected": "Abgelehnt",
    "expired": "Abgelaufen",
    "repriced": "Neu bepreist",
    "not_submitted": "Noch nicht gesendet",
    "reconciled_missing_position": "Als verkauft erkannt",
    "adopted_orphan": "Aus Broker übernommen",
    "insufficient_buying_power": "Buying Power zu gering",
    "queued_regular": "Vorgemerkt (wartet auf reguläre Sitzung)",
    "queue_expired": "Vorgemerkt — verfallen (Signal veraltet)",
    "leverage_blocked": f"{RISK_BLOCKED} (Hebel über Maximum)",
    "submit_failed": "Order konnte nicht gesendet werden",
    "missing_order_id": "Order ohne Broker-Bestätigung",
    "requested": "Verkauf angefragt",
}


def broker_status_label(broker_status: str | None) -> str:
    """Broker-/Order-Statuscode → sachliches deutsches Label (§25.2). Unbekannte Codes
    werden lesbar gemacht (``snake_case`` → „Title Case")."""
    if not broker_status:
        return "—"
    return BROKER_STATUS_LABELS.get(broker_status, broker_status.replace("_", " ").title())


def trade_status_label(status: str | None, broker_status: str | None = None) -> str:
    """Trade-Status → Label; für Broker-Zwischenzustände wird der Broker-Status angehängt."""
    base = TRADE_STATUS_LABELS.get(status or "", (status or "").replace("_", " ").title() or "—")
    if status in ("broker_pending", "broker_closing") and broker_status:
        return f"{base} · {broker_status_label(broker_status)}"
    return base


def mode_label(mode) -> str:
    """Betriebsmodus (``Mode``-Enum oder String) → deutsches Label (§5)."""
    key = getattr(mode, "value", mode)
    return MODE_LABELS.get(str(key).lower(), str(key).title())
