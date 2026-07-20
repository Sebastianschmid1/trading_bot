"""Style-Phasen 3–5 (W7): die abgenommenen Komponenten sind in den echten Seiten verdrahtet.

Prüft nicht das Aussehen, sondern die verbindlichen Punkte aus dem Stylekonzept:
kein grüner Kaufbutton (§11.2), Pflicht-Bestätigungsdialog (§18.1), sachliche
Microcopy (§25), Skip-Link/Fokus/Bottom-Nav (§23/§23.4) und das Modus-Report-Panel
(RES-002 / W3.4).
"""
from __future__ import annotations

import json
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


def test_microcopy_stays_factual():
    _pending_signal()
    text = _client().get("/app").text
    for hype in ("Jetzt zuschlagen", "Nicht verpassen", "🚀"):
        assert hype not in text
    assert "Durch Risikoregel blockiert" in text     # §25


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
