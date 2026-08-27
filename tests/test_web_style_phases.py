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
    # Über die id suchen statt „erstes <dialog> der Seite" anzunehmen: seit UI-A11Y
    # (Scan-Overlay) trägt app.html ein zweites, frueher im Dokument stehendes <dialog>.
    match = re.search(r'<dialog\b[^>]*\bid="tradeConfirm"[^>]*>', text)
    assert match, "kein <dialog id=\"tradeConfirm\" …> gefunden"
    tag = match.group(0)
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


def _active_trade(ticker="TSLA"):
    """Ein aktiver Trade, damit „Position schließen" (Exit) auf der Seite auftaucht."""
    db.add_pending(CHAT, {
        "ticker": ticker, "price": 300.0, "direction": "long", "strategy": "sma_cross",
        "strength": 3, "raw_score": 3.0, "reasons": ["Test"], "leverage": 1.0,
    }, 0)
    db.activate_trade(CHAT, ticker)


def _entry_buttons(text: str) -> list[str]:
    """Submit-Buttons der Einstiegs-Formulare (js-accept)."""
    out = []
    for form in re.findall(r'<form[^>]*js-accept.*?</form>', text, re.S):
        out += re.findall(r'<button[^>]*type="submit"[^>]*>', form)
    return out


def _exit_buttons(text: str) -> list[str]:
    """Submit-Buttons der Ausstiegs-Formulare (/app/sell)."""
    out = []
    for form in re.findall(r'<form[^>]*action="/app/sell".*?</form>', text, re.S):
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
    buttons = _entry_buttons(text)
    assert buttons, "keine Einstiegs-Buttons gefunden"
    assert all("disabled" not in b for b in buttons)


def test_feed_chip_warns_past_the_gate_limit_but_still_allows_orders():
    _pending_signal()
    _scan_cache_aged(90)          # über der 60-s-Gate-Grenze, unter der UI-Blockgrenze
    text = _client().get("/app").text
    assert 'data-feed-state="delayed"' in text
    assert "chip--caution" in text
    assert "verzögert" in text
    assert 'id="feedStaleAlert"' not in text
    assert all("disabled" not in b for b in _entry_buttons(text))


def test_stale_feed_disables_entry_buttons_and_states_the_reason():
    _pending_signal()
    _scan_cache_aged(300)                             # über UI_STALE_SECONDS (180 s)
    text = _client().get("/app").text
    assert 'data-feed-state="stale"' in text
    assert "chip--warn" in text
    assert "veraltet – keine neuen Trades" in text
    # Begründung sichtbar, als Alert ausgezeichnet, nennt das gemessene Alter
    alert = re.search(r'<div class="alert alert--danger" role="alert" id="feedStaleAlert">.*?</div>\s*</div>',
                      text, re.S)
    assert alert, "Begründung mit role=alert fehlt"
    assert "300 s alt" in alert.group(0)
    buttons = _entry_buttons(text)
    assert buttons
    assert all("disabled" in b for b in buttons), "Einstiegs-Buttons sind nicht gesperrt"
    # „Ablehnen" ist nicht orderrelevant und bleibt bedienbar
    reject = re.search(r'<form[^>]*action="/app/reject".*?</form>', text, re.S)
    assert reject and "disabled" not in reject.group(0)


def test_stale_feed_never_blocks_closing_a_position():
    """Ein blockierter Ausstieg wäre gefährlicher als ein Einstieg auf altem Kurs."""
    _pending_signal()
    _active_trade()
    # 400 s: über UI_STALE_SECONDS, aber noch innerhalb der Scan-Cache-TTL (600 s) —
    # danach verschwinden die Signalkarten ohnehin und der Status ist wieder „unbekannt".
    _scan_cache_aged(400)
    text = _client().get("/app").text
    assert 'data-feed-state="stale"' in text
    entries = _entry_buttons(text)
    exits = _exit_buttons(text)
    assert entries and all("disabled" in b for b in entries)
    assert exits, "kein Button zum Schließen der Position gefunden"
    assert all("disabled" not in b for b in exits), "Exit darf NIE gesperrt sein"
    assert "Position schließen" in text


def test_unknown_data_age_is_explicit_and_does_not_block():
    # Ohne Scan-Ergebnis gibt es keinen belastbaren Kurs-Zeitstempel → nicht raten.
    _pending_signal()
    text = _client().get("/app").text
    assert 'data-feed-state="unknown"' in text
    assert "Datenalter unbekannt" in text
    assert "chip--caution" in text
    assert 'id="feedStaleAlert"' not in text
    assert all("disabled" not in b for b in _entry_buttons(text))


# ── Style §32.5: „Daten unsicher/degradiert" als eigener Zustand ─────────────

def _price_fetch_fails(monkeypatch):
    """Simuliert die fail-open-Stelle `core/evaluator.get_current_price`: der Abruffehler
    wird dort abgefangen und der Fallback zurueckgegeben (stockbot/web/dashboard.py:
    `_current_price`). Kein Netzzugriff im Test."""
    monkeypatch.setattr(_dashboard, "get_current_price", lambda ticker, fallback: fallback)


def test_degraded_data_is_a_warning_banner_and_not_the_error_banner(monkeypatch):
    _pending_signal()
    _active_trade()                       # TSLA, Einstieg 300.0
    _price_fetch_fails(monkeypatch)
    text = _client().get("/app").text
    assert 'data-feed-state="degraded"' in text
    assert "Daten unsicher" in text
    banner = re.search(
        r'<div class="alert alert--warning" role="alert" id="feedDegradedAlert">.*?</div>\s*</div>',
        text, re.S)
    assert banner, "Warn-Banner (--warning, role=alert) fehlt"
    assert "TSLA" in banner.group(0)                  # nennt konkret die unsicheren Daten
    assert "Neue Einstiege sind gesperrt" in banner.group(0)
    assert "schließen" in banner.group(0)             # Exits bleiben möglich
    # Vom Fehler-/veraltet-Zustand (--danger) im Markup unterscheidbar
    assert 'id="feedStaleAlert"' not in text


def test_degraded_data_blocks_entries_but_never_exits(monkeypatch):
    _pending_signal()
    _active_trade()
    _price_fetch_fails(monkeypatch)
    text = _client().get("/app").text
    entries = _entry_buttons(text)
    exits = _exit_buttons(text)
    assert entries and all("disabled" in b for b in entries), "Einstiege nicht gesperrt"
    assert exits, "kein Button zum Schließen der Position gefunden"
    assert all("disabled" not in b for b in exits), "Exit darf NIE gesperrt sein"


def test_degraded_data_shows_no_estimated_value(monkeypatch):
    """§32.5: statt eines optimistischen Ersatzwerts steht dort „nicht verfügbar"/„—"."""
    _pending_signal()
    _active_trade()
    _price_fetch_fails(monkeypatch)
    text = re.sub(r"\s+", " ", _client().get("/app").text)
    tail = text[text.index("Aktive Trades"):]
    assert "→ aktuell nicht verfügbar" in tail
    assert "→ aktuell $" not in tail                  # nie der Einstieg als Ersatzkurs
    assert not re.search(r'class="(pos|neg)"', tail)  # kein geschätztes P&L (0,00 €)
    assert 'data-price=""' in tail                    # auch der Dialog bekommt keinen Wert
    assert 'data-pnl="—"' in tail


def test_stale_takes_precedence_over_degraded(monkeypatch):
    """Beide sperren Einstiege; „veraltet" ist die schärfere Aussage und behält --danger."""
    _pending_signal()
    _active_trade()
    _scan_cache_aged(400)
    _price_fetch_fails(monkeypatch)
    text = _client().get("/app").text
    assert 'data-feed-state="stale"' in text
    assert 'id="feedStaleAlert"' in text
    assert 'id="feedDegradedAlert"' not in text
    assert "nicht verfügbar" in text                  # Ersatzwert bleibt trotzdem verboten
    assert all("disabled" not in b for b in _exit_buttons(text))


# ── Style-Phase 4: Kill-Switch ───────────────────────────────────────────────

def test_kill_switch_shows_state_chip_and_reason_field():
    text = _client().get("/app/settings").text
    assert "Kill-Switch aktivieren" in text
    assert "Einstiege erlaubt" in text or "Einstiege blockiert" in text
    assert "Grund (wird protokolliert)" in text


# ── agent/UI-HARDENING-2: Kill-Switch-Abschalten & Link-Erneuern hinter dialog ──
# (§18.1 statt nativem confirm() — Kill-Switch abschalten hebt die Einstiegssperre
# auf und ist damit sicherheitsrelevant; Link-Erneuern ist die harmlosere zweite
# Stelle derselben Art. Muster gespiegelt von alpacaClearConfirm/resetConfirm.)

def test_kill_switch_off_form_uses_dialog_not_native_confirm():
    c = _client()
    r = c.post("/app/settings/killswitch", data={"enabled": "1", "reason": "Testgrund"},
               follow_redirects=False)
    assert r.status_code in (302, 303, 307, 308)
    text = re.sub(r"\s+", " ", c.get("/app/settings").text)

    assert "onsubmit=" not in text                       # kein natives confirm() mehr am Formular
    assert 'data-confirm-dialog="killSwitchOffConfirm"' in text
    match = re.search(r'<dialog\b[^>]*\bid="killSwitchOffConfirm"[^>]*>', text)
    assert match, 'kein <dialog id="killSwitchOffConfirm" …> gefunden'
    tag = match.group(0)
    assert 'class="dialog dialog--live"' in tag
    assert 'role="dialog"' in tag and 'aria-modal="true"' in tag

    dialog = text[text.index('id="killSwitchOffConfirm"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    # Folgen konkret benannt (nicht nur der generische Warnsatz von oben im Formular):
    assert "neue Positionen" in dialog and ("eröffnen" in dialog or "möglich" in dialog)
    assert "Schutz-Verkäufe" in dialog
    assert "Stop-Loss" in dialog or "Take-Profit" in dialog
    # Gleiches Fokus-/Aktions-Muster wie die drei bestehenden dialog-Stellen:
    assert re.search(r'<button[^>]*data-confirm-cancel[^>]*autofocus[^>]*>', dialog)
    assert 'data-confirm-ok' in dialog


def test_kill_switch_activation_needs_no_confirm_dialog():
    """Nur das Abschalten (Einstiegssperre aufheben) ist sicherheitsrelevant — das
    Aktivieren (Sperre setzen) bleibt ein normaler Submit ohne Bestätigungsdialog."""
    text = re.sub(r"\s+", " ", _client().get("/app/settings").text)
    assert 'data-confirm-dialog="killSwitchOffConfirm"' not in text
    assert 'id="killSwitchOffConfirm"' not in text


# ── agent/UI-KILLSWITCH-VISIBLE: Kill-Switch-Chip in der geteilten appbar ────

def test_active_kill_switch_shows_appbar_chip_on_every_shared_page():
    """Bei aktivem Kill-Switch ist der Hinweis auf JEDER Seite sichtbar, die die
    gemeinsame appbar nutzt (nicht nur auf /app/settings) — Kern-Akzeptanzkriterium."""
    c = _client()
    r = c.post("/app/settings/killswitch", data={"enabled": "1", "reason": "Marktvolatilität"},
               follow_redirects=False)
    assert r.status_code in (302, 303, 307, 308)
    for path in ("/app", "/app/watchlist", "/app/history"):
        text = c.get(path).text
        assert "Kill-Switch aktiv" in text, f"kein Kill-Switch-Hinweis auf {path}"
        # Beide Fakten in Klartext (§ Acceptance Criteria 3): keine neuen Positionen,
        # Schutz-Verkäufe laufen unveraendert weiter.
        assert "keine neuen Positionen" in text
        assert "Schutz-Verkäufe laufen weiter" in text
        assert 'href="/app/settings"' in text          # verlinkt zur Abschalt-Stelle
        assert "Marktvolatilität" in text               # kurzer Grund steht im Chip selbst


def test_inactive_kill_switch_shows_no_appbar_chip():
    """Ohne aktiven Kill-Switch bleibt die appbar unveraendert — kein Dauer-Hinweis."""
    text = _client().get("/app").text
    assert "Kill-Switch aktiv" not in text


def test_kill_switch_chip_disappears_after_deactivation():
    c = _client()
    c.post("/app/settings/killswitch", data={"enabled": "1", "reason": "Test"}, follow_redirects=False)
    assert "Kill-Switch aktiv" in c.get("/app/watchlist").text
    c.post("/app/settings/killswitch", data={"enabled": "0"}, follow_redirects=False)
    assert "Kill-Switch aktiv" not in c.get("/app/watchlist").text


def test_kill_switch_long_reason_stays_in_title_not_inline_label():
    """Der Grund gehört mindestens als title dazu; nur ein KURZER Grund landet zusaetzlich
    sichtbar im Chip-Text selbst (sonst sprengt er die Pille in der appbar)."""
    c = _client()
    long_reason = "Ungewöhnlich hohe Volatilität an mehreren Märkten gleichzeitig beobachtet"
    assert len(long_reason) > 40
    c.post("/app/settings/killswitch", data={"enabled": "1", "reason": long_reason},
           follow_redirects=False)
    text = c.get("/app").text
    assert f"Grund: {long_reason}" in text              # title-Attribut traegt den vollen Grund
    assert f"· {long_reason}" not in text                # aber nicht zusaetzlich im Chip-Label


def test_dashboard_page_still_renders_with_active_kill_switch():
    """Regressions-Check: dashboard.html ist bewusst nicht angefasst (eigene .ck-bar,
    siehe DESIGN.md) — die Seite muss trotzdem weiter fehlerfrei rendern."""
    c = _client()
    c.post("/app/settings/killswitch", data={"enabled": "1", "reason": "Test"}, follow_redirects=False)
    assert c.get("/app/dashboard").status_code == 200


def test_kill_switch_chip_reuses_existing_chip_component_not_a_new_one():
    """§ Scope: keine neue Komponente — derselbe chip()/chip--warn wie in settings.html."""
    c = _client()
    c.post("/app/settings/killswitch", data={"enabled": "1", "reason": "Test"}, follow_redirects=False)
    text = c.get("/app").text
    assert 'class="chip chip--warn"' in text


def test_token_rotate_form_uses_dialog_not_native_confirm():
    text = re.sub(r"\s+", " ", _client().get("/app/settings").text)
    assert 'onsubmit="return confirm(' not in text
    assert 'data-confirm-dialog="tokenRotateConfirm"' in text
    match = re.search(r'<dialog\b[^>]*\bid="tokenRotateConfirm"[^>]*>', text)
    assert match, 'kein <dialog id="tokenRotateConfirm" …> gefunden'
    tag = match.group(0)
    assert 'class="dialog"' in tag
    assert 'role="dialog"' in tag and 'aria-modal="true"' in tag
    dialog = text[text.index('id="tokenRotateConfirm"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert "ungültig" in dialog                           # nennt die Folge (alter Link bricht)
    assert re.search(r'<button[^>]*data-confirm-cancel[^>]*autofocus[^>]*>', dialog)
    assert 'data-confirm-ok' in dialog


def test_no_native_confirm_dialogs_remain_in_web_templates():
    """Repo-weiter Beleg für Abnahmekriterium 3: kein natives confirm() mehr in den
    Templates (Kommentare, die das Wort nennen, sind erlaubt)."""
    templates_dir = Path("stockbot/web/templates")
    offenders = []
    for f in templates_dir.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"onsubmit\s*=\s*\"[^\"]*confirm\(", text):
            offenders.append(f"{f}: {m.group(0)}")
    assert not offenders, f"native confirm()-Bestätigungen gefunden: {offenders}"


# ── agent/UI-HARDENING-2: Chart.js lokal statt CDN ───────────────────────────

def test_dashboard_loads_chartjs_locally_not_from_cdn():
    text = _client().get("/app/dashboard").text
    assert "cdn.jsdelivr.net" not in text
    assert '<script src="/static/chart.umd.js"></script>' in text
    # Muss VOR dem großen Inline-Skript stehen, damit `Chart` beim Parsen schon da ist.
    assert text.index('/static/chart.umd.js') < text.index("applyChartTheme")


def test_no_jsdelivr_references_left_in_web_templates():
    templates_dir = Path("stockbot/web/templates")
    offenders = [str(f) for f in templates_dir.glob("*.html")
                 if "jsdelivr" in f.read_text(encoding="utf-8")]
    assert not offenders, f"jsdelivr-Referenzen in Templates gefunden: {offenders}"


def test_csp_no_longer_allows_jsdelivr():
    """Die verwaiste, öffentlich (ohne Login) ausgelieferte Legacy-Seite
    stockbot/web/static/dashboard.html wurde entfernt (agent/WEB-LEGACY-CLEANUP) —
    sie war der einzige Grund für cdn.jsdelivr.net in der CSP. Chart.js lädt
    seit agent/UI-HARDENING-2 überall lokal (/static/chart.umd.js)."""
    dashboard_py = Path("stockbot/web/dashboard.py").read_text(encoding="utf-8")
    assert "jsdelivr" not in dashboard_py
    r = _client().get("/app/dashboard")
    assert "jsdelivr" not in r.headers["content-security-policy"]


def test_legacy_static_dashboard_is_gone():
    """Die Alt-Kopie unter static/ war ohne Anmeldung über /static/... erreichbar
    und wich vom gepflegten templates/dashboard.html ab — entfernt, siehe oben."""
    assert not Path("stockbot/web/static/dashboard.html").exists()
    r = _client().get("/static/dashboard.html")
    assert r.status_code == 404


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


# ── design-lead-Nachtrag (2026-08-27): Dashboard-Hierarchie, keine Hero-Kachel ───────

def test_no_hero_kachel_remains_in_dashboard():
    """Die Iris-Hero-Kachel fällt ersatzlos weg (design-lead-Nachtrag): die P&L-Kachel war
    der einzige gesättigte Farbblock der Ansicht und zog den Blick zuerst auf den Gewinn,
    obwohl Risiko hier bereits ereignisgetrieben kodiert ist (Kill-Switch-Chip, #error,
    #sizing-hint)."""
    text = Path("stockbot/web/templates/dashboard.html").read_text(encoding="utf-8")
    assert "card-hero" not in text
    assert "big" not in re.search(r"function renderKpis\(d\).*?\n    \}", text, flags=re.S).group(0)


def test_kpi_order_follows_risk_status_performance_history():
    """Reihenfolge der neun kpis.push()-Aufrufe (design-lead-Nachtrag): Risiko (aktive
    Exponierung) -> Status (verfügbares Kapital) -> Performance -> Historie. Ohne
    Broker-Anbindung (kein `if (ba)`-Block) fallen bp/cash weg, der Rest bleibt in
    derselben Reihenfolge stehen — eine direkte Konsequenz der linearen
    Push-Reihenfolge im Quelltext, hier explizit für beide Fälle belegt."""
    text = Path("stockbot/web/templates/dashboard.html").read_text(encoding="utf-8")
    block = re.search(r"function renderKpis\(d\).*?const host = document\.getElementById", text, flags=re.S)
    assert block, "renderKpis()-Funktion nicht gefunden"
    keys = re.findall(r'kpis\.push\(\{\s*key:\s*"(\w+)"', block.group(0))
    assert keys == ["active", "size", "bp", "cash", "pnl", "pf", "wr", "wl", "closed"]

    without_broker = [k for k in keys if k not in ("bp", "cash")]
    assert without_broker == ["active", "size", "pnl", "pf", "wr", "wl", "closed"]


def test_pnl_loss_coloring_survives_hero_removal():
    """Blocker aus der Design-Vorgabe: `cls()` färbt Gewinn/Verlust unabhängig von der
    (jetzt entfernten) Hero-Optik — `cls` hing nie an `big`. Regressionsschutz: wird rot,
    wenn jemand die Verlustfärbung beim Entfernen der Hero-Kachel versehentlich mitnimmt."""
    text = Path("stockbot/web/templates/dashboard.html").read_text(encoding="utf-8")
    assert re.search(r'const cls\s*=\s*\(n\)\s*=>\s*n\s*>=\s*0\s*\?\s*"green"\s*:\s*"red";', text)
    pnl_push = re.search(r'kpis\.push\(\{[^}]*key:\s*"pnl"[^}]*\}\)', text)
    assert pnl_push, "pnl-Kachel nicht gefunden"
    assert "cls: cls(s.total_pnl)" in pnl_push.group(0)
    assert "big" not in pnl_push.group(0)
    assert 'valEl.className = "value " + (k.cls || "");' in text
