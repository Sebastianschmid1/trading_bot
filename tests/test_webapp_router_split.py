"""Sichert die Aufteilung von `stockbot/web/webapp.py` in die Fach-Router webapp_auth.py,
webapp_signals.py, webapp_settings.py, webapp_reports.py und webapp_watchlist_lab.py.

* `test_routentabelle_unveraendert` friert Pfad+Methode jeder Route ein, die `webapp.router`
  vor der Aufteilung enthielt — ein vergessener oder verschobener Import fällt damit als
  fehlende/zusätzliche Route auf, nicht erst zur Laufzeit per 404.
* Die Naht-Tests sichern die Eigenschaft, auf der die Aufteilung ruht (siehe Docstring von
  `stockbot/web/webapp.py`): Tests ersetzen Namen wie `webapp._alpaca_ready` oder
  `webapp.kill_switch_service` auf dem **Aggregator-Modul**, obwohl die Funktionen, die sie
  benutzen, in einem Fach-Router stehen — das muss weiterhin erreichbar sein.
"""

from stockbot.web import webapp

# Pfad+Methode jeder Route aus `webapp.router`, wie sie vor der Aufteilung in der
# monolithischen webapp.py registriert waren (45 Dekorator-Routen über 5 Bereiche).
ROUTENTABELLE_VOR_DER_AUFTEILUNG = {
    ("GET", "/login"),
    ("GET", "/auth/token"),
    ("GET", "/auth/telegram"),
    ("POST", "/logout"),
    ("POST", "/logout/all"),
    ("GET", "/app"),
    ("POST", "/app/asset"),
    ("POST", "/app/scan"),
    ("POST", "/app/scan/accept"),
    ("POST", "/app/accept"),
    ("POST", "/app/reject"),
    ("POST", "/app/lev"),
    ("POST", "/app/sell"),
    ("GET", "/app/settings"),
    ("POST", "/app/settings/killswitch"),
    ("POST", "/app/settings/set"),
    ("POST", "/app/settings/notify"),
    ("POST", "/app/settings/alpaca"),
    ("POST", "/app/settings/token/rotate"),
    ("POST", "/app/settings/alpaca/clear"),
    ("POST", "/app/reset"),
    ("GET", "/app/watchlist"),
    ("POST", "/app/watchlist/add"),
    ("POST", "/app/watchlist/remove"),
    ("GET", "/app/reports"),
    ("GET", "/app/reports/equity"),
    ("GET", "/app/lab"),
    ("POST", "/app/lab/run"),
    ("POST", "/app/lab/apply"),
    ("POST", "/app/lab/reject"),
    ("GET", "/lab"),
    ("GET", "/app/history"),
    ("GET", "/history"),
    ("GET", "/app/backtest"),
    ("GET", "/app/backtest/export"),
    ("GET", "/app/export/logs"),
    ("POST", "/app/backtest"),
    ("GET", "/backtest"),
    ("GET", "/reports"),
    ("GET", "/dashboard"),
    ("GET", "/app/dashboard"),
}


def _aktuelle_routentabelle():
    out = set()
    for route in webapp.router.routes:
        for method in route.methods:
            out.add((method, route.path))
    return out


def test_routentabelle_unveraendert():
    aktuell = _aktuelle_routentabelle()
    fehlend = ROUTENTABELLE_VOR_DER_AUFTEILUNG - aktuell
    neu = aktuell - ROUTENTABELLE_VOR_DER_AUFTEILUNG
    assert not fehlend, f"Nach der Aufteilung fehlende Routen: {sorted(fehlend)}"
    assert not neu, f"Nach der Aufteilung neu/verschoben aufgetauchte Routen: {sorted(neu)}"


def test_jede_route_ist_ueber_die_app_erreichbar():
    """Nicht nur der Fach-Router selbst, sondern auch das reale Einhängen in dashboard.py
    (app.include_router) muss jede Route bedienen — sonst wäre der Router-Objekt-Vergleich
    oben ein Test ohne Aussagekraft für den tatsächlichen Server."""
    from starlette.testclient import TestClient
    from stockbot.web.dashboard import app

    client = TestClient(app)
    for method, path in ROUTENTABELLE_VOR_DER_AUFTEILUNG:
        resp = client.request(method, path, follow_redirects=False)
        # Kein Login vorhanden: ein 404 hieße "Route existiert nicht" bzw. falsch verdrahtet.
        # Erwartet ist ein Redirect zum Login (303) oder eine reguläre Antwort — niemals 404.
        assert resp.status_code != 404, f"{method} {path} liefert 404 (Route nicht verdrahtet?)"


# ── Test-Nähte: Namen, die Tests auf `webapp` ersetzen, obwohl die aufrufende Logik in
# einem Fach-Router steht (siehe Docstring von stockbot/web/webapp.py) ──────────────────

def test_seam_namen_bleiben_ueber_webapp_erreichbar():
    for name in (
        "_alpaca_ready", "_alpaca_client", "_alpaca_keys", "_attach_demo_option",
        "_broker_will_execute", "_ensure_buying_power", "_execute_broker_order_for_web",
        "_execute_broker_close_for_web", "kill_switch_service", "_oms", "_scan_cache",
        "_sparkline", "_event_export_rows", "app_settings_killswitch", "_render",
        "_redirect", "_is_admin", "router", "templates", "db", "broker", "config",
        "risk_context", "sizing",
    ):
        assert hasattr(webapp, name), f"webapp.{name} nicht mehr erreichbar — Test-Naht gebrochen."
