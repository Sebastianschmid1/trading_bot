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
#: Betriebsmodus, Schlüssel = ``core.domain.Mode``-Werte + ``"demo"``.
#:
#: ``"demo"`` ist ein eigener, UI-seitiger Schlüssel (kein ``Mode``-Enum-Wert): den setzt
#: ``webapp._render`` genau dann als ``trade_mode``, wenn für den Nutzer keine
#: Broker-Ausführung aktiv ist (§UI-ONBOARDING/Befund 4). Dieser Zustand hieß in der UI an
#: vier Stellen unterschiedlich (Badge „SHADOW", Chip „Shadow", Dialogzeile „DEMO", Panel
#: „Aktive Demo-Trades") — ein Defekt nach §32.9. Entscheidung: „Demo" gewinnt, weil es aus
#: Kundensicht (fremdes, wachsendes Publikum, PRODUCT.md „Users") verständlicher ist als der
#: interne Fachbegriff „Shadow". ``"shadow"`` bleibt UNVERÄNDERT bestehen — das ist der
#: getestete ``core.domain.Mode``-Wert und die Bezeichnung der persistierten
#: Paper-/Shadow-Modus-Reports (RES-002, ``mode_reporting``); beide sind ein anderer,
#: technischer Datenpartitions-Begriff und nicht Teil dieser Vereinheitlichung.
MODE_LABELS = {
    "backtest": "Backtest",
    "shadow": "Shadow",
    "demo": "Demo",
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
    # Risk-Gate-Ablehnungen (OBS-001): der echte Ablehngrund aus dem OMS wird als
    # broker_status persistiert statt des generischen „submit_failed" — damit Dashboard,
    # Tagesreport und Journal denselben sachlichen Grund zeigen (§25.2).
    "kill_switch_active": f"{RISK_BLOCKED} (Kill-Switch aktiv)",
    "max_positions_reached": f"{RISK_BLOCKED} (Positionslimit erreicht)",
    "ticker_position_exists": f"{RISK_BLOCKED} (Position bereits offen)",
    "daily_loss_limit_reached": f"{RISK_BLOCKED} (Tagesverlust-Limit erreicht)",
    "exposure_cap": f"{RISK_BLOCKED} (Exposure-Limit erreicht)",
    "daily_new_exposure": f"{RISK_BLOCKED} (Tages-Exposure-Limit erreicht)",
    "budget_exhausted": f"{RISK_BLOCKED} (Budget erschöpft)",
    "buying_power": f"{RISK_BLOCKED} (Buying Power zu gering)",
    "zero_quantity": f"{RISK_BLOCKED} (Menge zu klein)",
    "market_closed": f"{RISK_BLOCKED} (Markt geschlossen)",
    "quote_required": f"{RISK_BLOCKED} (Kurs fehlt)",
    "quote_stale": f"{RISK_BLOCKED} (Kurs veraltet)",
    "quote_unavailable": f"{RISK_BLOCKED} (Kurs nicht verfügbar)",
    "spread_wide": f"{RISK_BLOCKED} (Spread zu weit)",
    "liquidity_low": f"{RISK_BLOCKED} (Liquidität zu gering)",
    "signal_expired": f"{RISK_BLOCKED} (Signal abgelaufen)",
    "signal_invalid": f"{RISK_BLOCKED} (Signal ungültig)",
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
