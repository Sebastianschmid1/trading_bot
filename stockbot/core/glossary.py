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

# ── Modus-Präfix für Handelsentscheidungs-Nachrichten (§26.1/§26.2, agent/TG-MODEPREFIX) ────
#: Erste Zeile jeder Nachricht, die zu einer Handelsentscheidung auffordert (Signalnachricht).
#: Live nutzt WÖRTLICH dieselbe Warnung wie die Web-App (`app.html`/`components.html`,
#: `tcMode` bzw. `mode_badge`-Makro) — zwei Schreibweisen derselben Warnung wären laut §32.9
#: selbst ein Defekt, deshalb hier keine eigene Formulierung. Enthält keine Markdown-
#: Sonderzeichen (`_`, `*`, Backtick, `[`) — sicher für `parse_mode="Markdown"` ohne Escaping.
MODE_MESSAGE_PREFIXES = {
    "demo": MODE_LABELS["demo"].upper(),
    "paper": MODE_LABELS["paper"].upper(),
    "live": "LIVE – ECHTES GELD",
}


def mode_message_prefix(mode) -> str:
    """Betriebsmodus (``"demo"`` oder ``Mode``-Enum/-String) → Präfix-Zeile für
    Handelsentscheidungs-Nachrichten (§26.1/§26.2). Unbekannte Werte fallen auf den
    normalen Modus-Label zurück (großgeschrieben, wie die übrigen Präfixe)."""
    key = getattr(mode, "value", mode)
    key = str(key).lower()
    return MODE_MESSAGE_PREFIXES.get(key, mode_label(key).upper())


# ── Broker-Währung (agent/CURRENCY-HONEST) ──────────────────────────────────
#: Alpaca führt Konten ausschließlich in US-Dollar; im Repo gibt es KEINE Wechselkurs-
#: Umrechnung (kein fx_rate, kein Kursabruf für EUR/USD). Trade-Größe, P&L und Kontowerte
#: kommen deshalb 1:1 in USD vom Broker — auch dort, wo intern historisch `_eur`-benannte
#: Felder/Spalten (z. B. `trades.pnl_eur`, `users.trade_size_eur`) sie tragen. Dieser Hinweis
#: ist die EINE Quelle für den erklärenden Satz in Web-App und Telegram (§32.9-Prinzip).
#: KEIN "&" im Text — würde in HTML-Templates (Jinja-Autoescape zu `&amp;`) und im
#: Vergleich mit der rohen Konstante auseinanderlaufen.
BROKER_CURRENCY_NOTE = (
    "Das Alpaca-Konto läuft in US-Dollar — alle Beträge hier (Trade-Größe, Gewinn/Verlust, "
    "Kontowert) stehen deshalb in $, nicht in Euro."
)

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


# ── SL/TP-Modus (§25.2) ─────────────────────────────────────────────────────
#: Schlüssel = ``core.config.SL_TP_MODES`` (aus/passiv/normal/aggressiv) — eine EIGENE
#: Domäne, KEIN Trading-Modus (siehe ``MODE_LABELS`` oben). Die Reports-Matrix
#: (`/app/reports`, `tools/sweep_report.py MODES`) und die Einstellungsseite
#: (`settings.html sl_tp_modes`) zeigen dieselben SL/TP-Varianten, nicht
#: Backtest/Shadow/Demo/Paper/Live — deshalb hier ein eigenes Label-Paar statt
#: `mode_label()` auf einer fachlich falschen Domäne anzuwenden.
SL_TP_MODE_LABELS = {
    "aus":       "Aus",
    "passiv":    "Passiv",
    "normal":    "Normal",
    "aggressiv": "Aggressiv",
}


def sl_tp_mode_label(mode) -> str:
    """SL/TP-Modus (String) → deutsches Label; unbekannte Werte lesbar gemacht."""
    key = getattr(mode, "value", mode)
    return SL_TP_MODE_LABELS.get(str(key).lower(), str(key).title())
