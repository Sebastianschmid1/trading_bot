"""§32.9 — Begriffs-Parität Web ↔ Telegram über das gemeinsame Glossar.

Nagelt fest, dass Web-App und Telegram-Bot denselben Zustand *identisch* benennen: beide
Kanäle ziehen die Status-/Modus-/Aktionsbegriffe aus `stockbot.core.glossary` (eine Quelle,
keine Parallelwelt). Divergierende Formulierungen für denselben Zustand wären laut §32.9 ein
Defekt.
"""
import pytest

from stockbot.core import glossary


# ── Web und Telegram teilen dieselbe Quelle (nicht nur denselben Wert) ────────

def test_web_reuses_glossary_status_labels():
    """Die Web-Label-Funktionen sind *dieselben Objekte* wie im Glossar."""
    dashboard = pytest.importorskip("stockbot.web.dashboard")
    assert dashboard.broker_status_label is glossary.broker_status_label
    assert dashboard.trade_status_label is glossary.trade_status_label
    assert dashboard.BROKER_STATUS_LABELS is glossary.BROKER_STATUS_LABELS
    assert dashboard.TRADE_STATUS_LABELS is glossary.TRADE_STATUS_LABELS


def test_telegram_reuses_glossary_broker_label():
    """Der Telegram-Pfad rendert Broker-Status über dieselbe Glossar-Funktion."""
    bot = pytest.importorskip("stockbot.tgbot.bot")
    assert bot.broker_status_label is glossary.broker_status_label


# ── Parität für Broker-/Order-Status ─────────────────────────────────────────

@pytest.mark.parametrize("code,label", [
    ("filled", "Ausgeführt"),
    ("rejected", "Abgelehnt"),
    ("canceled", "Abgebrochen"),
    ("expired", "Abgelaufen"),
    ("leverage_blocked", "Durch Risikoregel blockiert (Hebel über Maximum)"),
    # OBS-001: echte Risk-Gate-Ablehngründe werden als broker_status persistiert und
    # müssen sachliches Deutsch zeigen, nicht den rohen OMS-Code.
    ("max_positions_reached", "Durch Risikoregel blockiert (Positionslimit erreicht)"),
    ("daily_loss_limit_reached", "Durch Risikoregel blockiert (Tagesverlust-Limit erreicht)"),
    ("quote_stale", "Durch Risikoregel blockiert (Kurs veraltet)"),
])
def test_broker_status_labels_are_german_not_raw_codes(code, label):
    """Beide Kanäle zeigen sachliches Deutsch (§25.2), nie den rohen Broker-Code."""
    assert glossary.broker_status_label(code) == label
    # Der frühere Telegram-Defekt war das rohe englische Codewort — darf nicht mehr auftauchen.
    assert glossary.broker_status_label(code) != code


def test_broker_status_label_falls_back_for_unknown_code():
    assert glossary.broker_status_label("some_new_code") == "Some New Code"


def test_broker_status_label_empty_is_dash():
    assert glossary.broker_status_label(None) == "—"
    assert glossary.broker_status_label("") == "—"


# ── Parität für Trade-Status ─────────────────────────────────────────────────

@pytest.mark.parametrize("status,label", [
    ("active", "Aktiv"),
    ("broker_pending", "Broker wartet"),
    ("broker_closing", "Verkauf läuft"),
    ("broker_failed", "Broker fehlgeschlagen"),
    ("closed", "Geschlossen"),
])
def test_trade_status_labels(status, label):
    assert glossary.trade_status_label(status) == label


def test_trade_status_label_appends_broker_status():
    assert glossary.trade_status_label("broker_pending", "accepted") == \
        "Broker wartet · Broker hat angenommen"
    assert glossary.trade_status_label("broker_closing", "filled") == \
        "Verkauf läuft · Ausgeführt"


# ── Risk-Gate-Ablehnung & Signal-Verfall ─────────────────────────────────────

def test_risk_gate_rejection_text():
    """Der geteilte Ablehntext (§25.2) — Web-JS-Default und Broker-Label speisen sich daraus."""
    assert glossary.RISK_BLOCKED == "Durch Risikoregel blockiert"
    assert glossary.broker_status_label("leverage_blocked").startswith(glossary.RISK_BLOCKED)


def test_signal_expired_text_matches_risk_and_oms():
    """Der Verfallstext ist identisch zur Begründung in Risk-Gate und OMS."""
    from stockbot.core import risk
    from stockbot.core.domain import SignalStatus
    from datetime import datetime, timezone, timedelta
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    decision = risk.pretrade_check(
        leverage=1.0, is_option=False, is_live_account=False,
        signal_status=SignalStatus.ACCEPTED, signal_expires_at=past)
    assert decision.reason == glossary.SIGNAL_EXPIRED


# ── Aktionsbegriff & Modus ───────────────────────────────────────────────────

def test_action_review_trade_constant():
    assert glossary.ACTION_REVIEW_TRADE == "Trade prüfen"


@pytest.mark.parametrize("mode,label", [
    ("paper", "Paper"),
    ("shadow", "Shadow"),
    ("demo", "Demo"),
    ("live", "Live"),
    ("backtest", "Backtest"),
])
def test_mode_labels(mode, label):
    assert glossary.mode_label(mode) == label


def test_mode_label_accepts_enum():
    from stockbot.core.domain import Mode
    assert glossary.mode_label(Mode.PAPER) == "Paper"
    assert glossary.mode_label(Mode.SHADOW) == "Shadow"
