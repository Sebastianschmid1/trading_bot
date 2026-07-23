"""Style-Phasen 3–5 (W7): die abgenommenen Komponenten sind in den echten Seiten verdrahtet.

Prüft nicht das Aussehen, sondern die verbindlichen Punkte aus dem Stylekonzept:
kein grüner Kaufbutton (§11.2), Pflicht-Bestätigungsdialog (§18.1) samt Fokus- und
Anti-Fehlklick-Verhalten (§32.4), sachliche
Microcopy (§25), Skip-Link/Fokus/Bottom-Nav (§23/§23.4) und das Modus-Report-Panel
(RES-002 / W3.4).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.web import auth
from stockbot.web import dashboard as _dashboard   # zuerst: vermeidet Zirkularimport
from stockbot.web import webapp

CHAT = 8801


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "style.db")
    db.init_db()
    db.get_or_create_user(CHAT, "styletester")
    db.save_profile(CHAT, trade_size_eur=100.0)
    auth._auth_hits.clear()
    webapp._scan_cache.clear()


def _client():
    from stockbot.web.dashboard import app
    c = TestClient(app)
    c.get(f"/auth/token?token={db.get_or_create_dashboard_token(CHAT)}")
    return c


def _pending_signal(ticker="AAPL"):
    db.add_pending(CHAT, {
        "ticker": ticker, "price": 100.0, "stop_loss": 95.0, "take_profit": 110.0,
        "direction": "long", "strategy": "sma_cross", "strength": 3, "raw_score": 3.0,
        "reasons": ["Test"], "volume_comment": "-",
    }, 0)


# ── Style-Phase 5: gemeinsames Layout ────────────────────────────────────────

def test_base_layout_ships_components_and_a11y_affordances():
    r = _client().get("/app")
    assert r.status_code == 200
    assert "/static/components.css" in r.text        # Komponenten-CSS wirklich geladen
    assert "/static/tokens.css" in r.text
    assert 'class="skip-link"' in r.text             # Sprungmarke zum Inhalt
    assert 'id="main"' in r.text
    assert 'aria-label="Hauptnavigation"' in r.text


def test_mobile_bottom_nav_and_touch_targets_are_declared():
    text = Path("stockbot/web/templates/base.html").read_text(encoding="utf-8")
    assert "@media (max-width: 640px)" in text
    assert "min-height:44px" in text                 # Touch-Ziele ≥ 44px (§23)
    assert "position:fixed; bottom:0" in text        # Bottom-Navigation (§23.4)
    assert "prefers-reduced-motion" in text


# ── Style-Phase 3/4: Signalseite ─────────────────────────────────────────────

def test_signal_card_asks_to_review_instead_of_offering_a_buy_button():
    _pending_signal()
    r = _client().get("/app")
    assert r.status_code == 200
    assert "Trade prüfen" in r.text                  # §11.2 — prüfen, nicht kaufen
    assert "Top Pick" not in r.text
    assert "btn2--primary" in r.text
    assert "button class=\"green\"" not in r.text    # kein grüner Kaufbutton mehr


def test_entry_flow_requires_the_mandatory_confirmation_dialog():
    _pending_signal()
    text = _client().get("/app").text
    assert 'id="tradeConfirm"' in text
    assert "js-confirm" in text
    # Feste Feldreihenfolge aus §18.1 — nur innerhalb des Dialogs geprüft
    dialog = text[text.index('id="tradeConfirm"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    order = [dialog.index(x) for x in ("Modus", "Instrument", "Entry", "Stop", "Risiko", "Größe")]
    assert order == sorted(order)
    assert 'dataset.confirmed' in text or 'confirmed = "1"' in text


# ── Style §32.4: Dialog- & Fokus-Verhalten ───────────────────────────────────

def _app_page() -> str:
    _pending_signal()
    r = _client().get("/app")
    assert r.status_code == 200
    return re.sub(r"\s+", " ", r.text)          # whitespace-robust vergleichen


def test_confirm_dialog_is_declared_as_modal_dialog():
    text = _app_page()
    tag = text[text.index("<dialog"):]
    tag = tag[:tag.index(">") + 1]
    assert 'id="tradeConfirm"' in tag
    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert 'aria-labelledby="tcTitle"' in tag


def test_initial_focus_is_cancel_and_never_the_confirm_button():
    text = _app_page()
    cancel = re.search(r'<button[^>]*id="tcCancel"[^>]*>', text)
    ok = re.search(r'<button[^>]*id="tcOk"[^>]*>', text)
    assert cancel and ok
    assert "autofocus" in cancel.group(0)        # Initialfokus auf „Abbrechen"
    assert "autofocus" not in ok.group(0)        # Bestätigen ist NIE initial fokussiert
    # …und wird zusätzlich explizit beim Öffnen gesetzt (autofocus allein reicht nicht)
    assert re.search(r"cancelBtn\.focus\(\)|getElementById\(\"tcCancel\"\)\.focus\(\)", text)


def test_confirm_button_cannot_be_triggered_by_enter():
    # Anti-Fehlklick (§32.4): ein versehentliches Enter darf keine Order auslösen.
    text = _app_page()
    guard = re.search(
        r'okBtn\.addEventListener\("keydown".{0,200}?"Enter".{0,80}?preventDefault\(\)', text)
    assert guard, "Enter-Guard auf dem Bestätigen-Button fehlt"


def test_dialog_traps_focus_and_returns_it_to_the_trigger():
    text = _app_page()
    # Fokus-Trap: Tab-Handler auf dem Dialog, der am Rand umschaltet
    trap = re.search(r'dlg\.addEventListener\("keydown".{0,600}?"Tab".{0,600}?preventDefault\(\)', text)
    assert trap, "Fokus-Trap (Tab-Handler auf dem Dialog) fehlt"
    # Fokus-Rückgabe beim Schließen auf das auslösende Element
    assert "function restoreFocus()" in text
    assert 'dlg.addEventListener("close", restoreFocus)' in text
    assert "lastFocus" in text


def test_confirm_button_locks_against_double_submit():
    text = _app_page()
    assert re.search(r'okBtn\.addEventListener\("click".{0,120}?okBtn\.disabled = true', text)
    assert "okBtn.disabled = false" in text      # beim nächsten Öffnen wieder aktiv


def test_microcopy_stays_factual():
    _pending_signal()
    text = _client().get("/app").text
    for hype in ("Jetzt zuschlagen", "Nicht verpassen", "🚀"):
        assert hype not in text
    assert "Durch Risikoregel blockiert" in text     # §25


# ── Style §32.3: Datenaktualität, gekoppelt an das Quote-Freshness-Gate ──────

def _scan_cache_aged(age_seconds: float, ticker="MSFT"):
    """Legt ein Scan-Ergebnis mit definiertem Alter in den Cache (kein echter Scan)."""
    webapp._scan_cache[CHAT] = {
        "at": time.time() - age_seconds,
        "asset": db.get_user(CHAT).get("asset_pref") or "stocks",
        "signals": [{
            "ticker": ticker, "price": 210.0, "direction": "long", "strategy": "sma_cross",
            "strength": 3, "raw_score": 3.0, "stop_loss": 200.0, "take_profit": 230.0,
            "leverage": 1.0, "spark_closes": [],
        }],
    }


def _submit_buttons(text: str) -> list[str]:
    """Alle Submit-Buttons in orderrelevanten (js-confirm) Formularen."""
    out = []
    for form in re.findall(r'<form[^>]*js-confirm.*?</form>', text, re.S):
        out += re.findall(r'<button[^>]*type="submit"[^>]*>', form)
    return out


def test_feed_chip_is_rendered_fresh_and_leaves_order_buttons_enabled():
    _pending_signal()
    _scan_cache_aged(1)
    text = _client().get("/app").text
    assert 'id="feedStatus"' in text
    assert 'data-feed-state="fresh"' in text
    assert "chip--go" in text
    assert "UTC" in text                              # §32.3: Zeitangabe beschriftet
    assert 'id="feedStaleAlert"' not in text
    buttons = _submit_buttons(text)
    assert buttons, "keine orderrelevanten Buttons gefunden"
    assert all("disabled" not in b for b in buttons)


def test_feed_chip_warns_when_data_is_delayed_but_still_allows_orders():
    _pending_signal()
    _scan_cache_aged(40)                              # 40 s bei Grenze 60 s → verzögert
    text = _client().get("/app").text
    assert 'data-feed-state="delayed"' in text
    assert "chip--caution" in text
    assert "verzögert" in text
    assert 'id="feedStaleAlert"' not in text
    assert all("disabled" not in b for b in _submit_buttons(text))


def test_stale_feed_disables_order_buttons_and_states_the_reason():
    _pending_signal()
    _scan_cache_aged(300)                             # weit über der 60-s-Gate-Grenze
    text = _client().get("/app").text
    assert 'data-feed-state="stale"' in text
    assert "chip--warn" in text
    assert "veraltet – Orders blockiert" in text
    # Begründung sichtbar, als Alert ausgezeichnet, nennt das gemessene Alter
    alert = re.search(r'<div class="alert2 alert2--danger" role="alert" id="feedStaleAlert">.*?</div>\s*</div>',
                      text, re.S)
    assert alert, "Begründung mit role=alert fehlt"
    assert "300 s alt" in alert.group(0)
    buttons = _submit_buttons(text)
    assert buttons
    assert all("disabled" in b for b in buttons), "orderrelevante Buttons sind nicht gesperrt"
    # „Ablehnen" ist nicht orderrelevant und bleibt bedienbar
    reject = re.search(r'<form[^>]*action="/app/reject".*?</form>', text, re.S)
    assert reject and "disabled" not in reject.group(0)


def test_unknown_data_age_is_explicit_and_does_not_block():
    # Ohne Scan-Ergebnis gibt es keinen belastbaren Kurs-Zeitstempel → nicht raten.
    _pending_signal()
    text = _client().get("/app").text
    assert 'data-feed-state="unknown"' in text
    assert "Datenalter unbekannt" in text
    assert "chip--caution" in text
    assert 'id="feedStaleAlert"' not in text
    assert all("disabled" not in b for b in _submit_buttons(text))


# ── Style-Phase 4: Kill-Switch ───────────────────────────────────────────────

def test_kill_switch_shows_state_chip_and_reason_field():
    text = _client().get("/app/settings").text
    assert "Kill-Switch aktivieren" in text
    assert "Einstiege erlaubt" in text or "Einstiege blockiert" in text
    assert "Grund (wird protokolliert)" in text


# ── W3.4 / RES-002: Modus-Report-Panel ───────────────────────────────────────

def test_dashboard_renders_mode_report_panel_from_existing_json():
    c = _client()
    page = c.get("/app/dashboard")
    assert page.status_code == 200
    assert 'id="modeReports"' in page.text
    assert "Modus-Report" in page.text
    assert "renderModeReports" in page.text

    data = c.get(f"/api/{db.get_or_create_dashboard_token(CHAT)}/data")
    assert data.status_code == 200
    reports = json.loads(data.text)["mode_reports"]
    assert set(reports) == {"paper", "shadow"}       # nie über Modi hinweg vermischt
