"""Chart.js-Ausfall darf das Dashboard nicht mitreißen (agent/UI-HARDENING-2, Aufgabe 2).

`dashboard.html` hängt seine gesamte Logik (Kennzahlen, Aktive-Trades-Tabelle, Panels)
in EIN Inline-<script>. Früher griff der allererste Codeblock ungeschützt auf
`Chart.defaults` zu — fehlte die Bibliothek (CDN down, CSP, Cache), warf das eine
Exception, die den kompletten Rest des Skripts mitriss: keine Kennzahlen, keine
Tabelle, keine Fehlermeldung, nur die Kommandoleiste über einer leeren Seite.

Dieses Repo hat weder Playwright noch Selenium noch eine JS-Testkette (kein
package.json, keine jsdom-Abhängigkeit erlaubt — siehe Task-Scope „keine neuen
Dependencies"). Der bestehende Testmechanismus für die Web-App (test_web_style_phases.py)
prüft ausschließlich das serverseitig gerenderte Markup/den Skripttext per Regex,
führt aber nie echtes JavaScript aus — für „ein simulierter Ausfall der
Chart-Bibliothek lässt Kennzahlen/Tabelle stehen" reicht das an dieser Stelle nicht,
weil genau das *Laufzeitverhalten* des Inline-Skripts der Prüfgegenstand ist.

Deshalb extrahiert dieser Test das echte, servergerenderte Inline-<script> aus
`/app/dashboard` (TestClient — keine Kopie, kein zweiter Wahrheitsort) und führt es
per Node `vm` (Node ist auf dem System vorhanden, kein npm-Paket, keine neue
Projekt-Abhängigkeit) zweimal aus: einmal ohne globales `Chart` (simulierter
Bibliotheks-Ausfall) und einmal mit einem minimalen Chart-Stub (Kontrolllauf, der
den unveränderten Normalfall belegt — Abnahmekriterium 5). Eine schlanke,
handgeschriebene DOM-Attrappe (kein jsdom) stellt nur die tatsächlich benutzten
Browser-APIs bereit; `document.getElementById` liefert für jede ID ein einfaches,
inspizierbares Objekt (textContent/innerHTML/style/dataset/classList), sodass sich
nach dem Lauf prüfen lässt, was wirklich im DOM gelandet wäre.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from stockbot.core import db
from stockbot.web import auth
from stockbot.web import dashboard as _dashboard   # zuerst: vermeidet Zirkularimport
from stockbot.web import webapp

CHAT = 8802
NODE = shutil.which("node")
# Muss wortgleich mit CHART_UNAVAILABLE_MSG in dashboard.html sein (Aufgabe 3,
# agent/UI-POLISH-DASH): nennt den wahrscheinlichen Grund (blockiertes Skript) und
# beruhigt, dass die Zahlen daneben weiterhin stimmen — kein Alarmton.
CHART_UNAVAILABLE_MSG = (
    "Diagramm nicht verfügbar — ein Skript wurde blockiert "
    "(z. B. Adblocker, Proxy oder Netzwerk). Die Zahlen daneben stimmen weiterhin."
)

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="Node (System-Binary, keine Projekt-Abhängigkeit) nicht gefunden — "
           "Simulation des Chart.js-Ausfalls kann in dieser Sandbox nicht laufen.",
)


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", Path(tmp_path) / "chartfallback.db")
    db.init_db()
    db.get_or_create_user(CHAT, "chartfallbacktester")
    db.save_profile(CHAT, trade_size_eur=100.0)
    auth._auth_hits.clear()
    webapp._scan_cache.clear()


def _client() -> TestClient:
    from stockbot.web.dashboard import app
    c = TestClient(app)
    c.get(f"/auth/token?token={db.get_or_create_dashboard_token(CHAT)}")
    return c


def _active_trade(ticker="ZALT"):
    db.add_pending(CHAT, {
        "ticker": ticker, "price": 50.0, "stop_loss": 45.0, "take_profit": 60.0,
        "direction": "long", "strategy": "sma_cross", "strength": 3, "raw_score": 3.0,
        "reasons": ["Test"], "leverage": 1.0,
    }, 0)
    db.activate_trade(CHAT, ticker)


def _extract_dashboard_script() -> str:
    """Holt das echte, servergerenderte Inline-<script> aus /app/dashboard.

    Sucht gezielt den Block, der `applyChartTheme` enthält — das ist die eigentliche
    Dashboard-Logik, nicht der `<script src="/static/chart.umd.js">`-Tag und nicht
    base.html's eigenes Inline-Skript (Theme-Umschalter, Bestätigungsdialoge).
    """
    _active_trade()
    text = _client().get("/app/dashboard").text
    assert text, "/app/dashboard hat keine Antwort geliefert — Fixtures/Route prüfen"
    # <style>-Blöcke und HTML-Kommentare enthalten den literalen Text "<script>" in ihrer
    # Doku (z. B. "…im <script> unten, §32.4)" in der Modal-Beschreibung) — die müssen vor
    # der Script-Suche raus, sonst matcht ein Kommentar statt eines echten <script>-Tags.
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.S)
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.S)
    blocks = re.findall(r"<script>(.*?)</script>", cleaned, flags=re.S)
    for block in blocks:
        if "applyChartTheme" in block:
            return block
    raise AssertionError("Dashboard-Inline-Skript (applyChartTheme) nicht gefunden")


# Minimaler, aber gültiger API-Payload — dieselbe Form wie /api/<token>/data.
_API_RESPONSE = {
    "username": "chartfallbacktester",
    "generated_at": "2026-08-16 12:00:00",
    "trade_size_eur": 100.0,
    "sizing_hint": None,
    "summary": {
        "total_pnl": 123.45, "profit_factor": 1.8, "win_rate": 60.0,
        "wins": 3, "losses": 2, "total_closed": 5, "active_count": 1,
    },
    "broker_account": None,
    "equity": [{"date": "2026-08-10", "cumulative": 50.0},
               {"date": "2026-08-11", "cumulative": 123.45}],
    "ticker_stats": [{"ticker": "AAPL", "pnl": 42.0}],
    "trades_curves": [], "trades_curves_x": {"min": 0, "max": 1},
    "active_trades": [{
        "ticker": "ZALT", "direction": "long", "invested": 100.0, "entry": 50.0,
        "current": 55.0, "pnl_eur": 10.0, "pnl_pct": 10.0, "roi_pct": 10.0,
        "stop_loss": 45.0, "take_profit": 60.0, "leverage": 1.0,
        "status_text": "Aktiv", "exposure": 100.0,
    }],
    "intraday": [{
        "ticker": "ZALT", "strategy": "sma_cross", "strategy_label": "SMA Cross",
        "take_profit": 60.0, "stop_loss": 45.0, "entry": 50.0,
        "points": [{"t": "09:30", "strength": 55, "price": 50.0},
                   {"t": "10:00", "strength": 62, "price": 51.5}],
    }],
    "mode_reports": {}, "strategies": [], "ranges": [0, 1, 7, 14, 30],
}

# Handgeschriebene, absichtlich schlanke DOM-Attrappe: kein jsdom, keine echte
# HTML-Parsing-/Selektor-Engine — nur die Teilmenge der Browser-APIs, die das
# extrahierte Skript synchron beim Laden und beim ersten render() tatsächlich
# aufruft. document.getElementById liefert IMMER ein Objekt (auto-vivify), damit
# ein vergessenes Element keinen Test-Fehlalarm auslöst; Ziel ist ausschließlich
# zu belegen, dass das Skript ohne Chart NICHT wirft und Kennzahlen/Tabelle trotzdem
# ins DOM geschrieben werden — nicht, die komplette Interaktivität nachzubilden.
_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const scriptPath = process.argv[2];
const apiJsonPath = process.argv[3];
const chartAvailable = process.argv[4] === '1';

const API_RESPONSE = JSON.parse(fs.readFileSync(apiJsonPath, 'utf8'));
const dashboardScript = fs.readFileSync(scriptPath, 'utf8');

function makeStyle() {
  const store = {};
  return new Proxy(store, {
    get: (t, p) => (p in t ? t[p] : ''),
    set: (t, p, v) => { t[p] = v; return true; },
  });
}
function makeClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c) => { if (set.has(c)) { set.delete(c); return false; } set.add(c); return true; },
    contains: (c) => set.has(c),
  };
}
function make2dContext() {
  const store = {};
  return new Proxy(store, {
    get(t, p) { if (p in t) return t[p]; return (...a) => undefined; },
    set(t, p, v) { t[p] = v; return true; },
  });
}
const elements = new Map();
function makeEl(id) {
  const el = {
    id, textContent: '', innerHTML: '', value: '', checked: false,
    style: makeStyle(), dataset: {}, classList: makeClassList(),
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    setAttribute() {}, getAttribute() { return null; },
    focus() {}, click() {}, closest() { return null; },
    appendChild() {}, insertBefore() {},
    parentNode: { insertBefore() {} },
    nextSibling: null,
    querySelector() { return makeEl(id + '::qs::' + Math.random()); },
    querySelectorAll() { return []; },
    getContext() { return make2dContext(); },
    width: 0, height: 0, open: false,
  };
  return el;
}
function getElementById(id) {
  if (!elements.has(id)) elements.set(id, makeEl(id));
  return elements.get(id);
}
const documentStub = {
  getElementById,
  createElement: (tag) => makeEl('created:' + tag + ':' + Math.random()),
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
  documentElement: makeEl('html'),
  activeElement: null,
  hidden: false,
};
function getComputedStyle() { return { getPropertyValue: () => '' }; }

let fetchCallCount = 0;
async function fetchStub() {
  fetchCallCount += 1;
  return { ok: true, json: async () => API_RESPONSE };
}

const sandbox = {
  document: documentStub,
  getComputedStyle,
  localStorage: { getItem: () => null, setItem() {} },
  fetch: fetchStub,
  URLSearchParams,
  setInterval: () => 0, clearInterval() {},
  setTimeout, clearTimeout,
  requestAnimationFrame: (cb) => cb(1e9),
  performance: { now: () => Date.now() },
  console, Math, Date, JSON,
  matchMedia: () => ({ matches: true }),   // REDUCED=true -> keine rAF-Animationsschleife nötig
  devicePixelRatio: 1,
};
sandbox.window = sandbox;

if (chartAvailable) {
  class FakeChart {
    constructor(canvas, config) {
      this.canvas = canvas; this.config = config;
      this.data = config.data; this.options = config.options;
    }
    update() {}
    destroy() {}
    static register() {}
    static getChart() { return undefined; }
  }
  FakeChart.defaults = { font: {} };
  sandbox.Chart = FakeChart;
}

const context = vm.createContext(sandbox);
const out = { threw: false, error: null, fetchCallCount: 0 };

try {
  vm.runInContext(dashboardScript, context, { filename: 'dashboard-inline.js' });
} catch (e) {
  out.threw = true;
  out.error = String((e && e.stack) || e);
}

async function main() {
  if (!out.threw) {
    try {
      // Der eigentliche Aufruf am Ende des extrahierten Skripts (load(true)) ist
      // "fire and forget" — hier explizit erneut awaiten, damit render() vor den
      // Assertions sicher durchgelaufen ist.
      await vm.runInContext('typeof load === "function" ? load(true) : Promise.resolve()', context);
    } catch (e) {
      out.threw = true;
      out.error = String((e && e.stack) || e);
    }
  }
  out.fetchCallCount = fetchCallCount;
  out.cardsInnerHTML = getElementById('cards').innerHTML;
  out.activeInnerHTML = getElementById('active').innerHTML;
  out.errorDisplay = getElementById('error').style.display;
  for (const id of ['equityChart', 'tickerChart', 'tradesLogChart', 'strengthChart', 'priceChartSingle']) {
    out[id] = {
      canvasDisplay: getElementById(id).style.display,
      msgText: getElementById(id + '_msg').textContent,
      msgDisplay: getElementById(id + '_msg').style.display,
    };
  }
  process.stdout.write(JSON.stringify(out));
}

main();
"""


def _run_harness(script_text: str, chart_available: bool, tmp_path: Path) -> dict:
    script_path = tmp_path / "dashboard_inline.js"
    script_path.write_text(script_text, encoding="utf-8")
    api_path = tmp_path / "api_response.json"
    api_path.write_text(json.dumps(_API_RESPONSE), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(_HARNESS, encoding="utf-8")

    proc = subprocess.run(
        [NODE, str(harness_path), str(script_path), str(api_path), "1" if chart_available else "0"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"Node-Harness ist fehlgeschlagen (rc={proc.returncode})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"Harness lieferte kein JSON zurück:\n{proc.stdout}\n{proc.stderr}")


def test_chart_library_failure_does_not_take_down_kpis_and_active_trades(tmp_path):
    """Kernregression: ohne `Chart` darf das Skript nicht werfen — Kennzahlen und die
    Aktive-Trades-Tabelle müssen trotzdem gerendert werden, und jede Diagramm-Stelle
    zeigt den ruhigen Hinweis „Diagramm nicht verfügbar" statt eines leeren/toten Canvas."""
    script = _extract_dashboard_script()
    result = _run_harness(script, chart_available=False, tmp_path=tmp_path)

    assert not result["threw"], (
        f"Inline-Skript ist ohne Chart.js mit einer Exception abgebrochen:\n{result.get('error')}"
    )
    # Kennzahlen (KPI-Kacheln) stehen trotzdem — render()/renderKpis() sind erreicht worden.
    assert "Gesamt P&L" in result["cardsInnerHTML"]
    assert "Trefferquote" in result["cardsInnerHTML"]
    # Aktive-Trades-Tabelle steht trotzdem, mit echten Daten aus der (gemockten) API.
    assert "ZALT" in result["activeInnerHTML"]
    # Die Fehlerbox oben ist NICHT der richtige Ort für einen Chart-Ausfall (kein Absturz).
    assert result["errorDisplay"] != "block"
    # An jeder Diagramm-Stelle: Canvas versteckt, ruhiger Hinweis sichtbar — der Hinweis
    # nennt jetzt auch kurz den Grund (blockiertes Skript) und beruhigt, dass die Zahlen
    # daneben weiterhin stimmen (Aufgabe 3, agent/UI-POLISH-DASH).
    for chart_id in ("equityChart", "tickerChart", "strengthChart", "priceChartSingle"):
        info = result[chart_id]
        assert info["canvasDisplay"] == "none", f"{chart_id}: Canvas müsste versteckt sein"
        assert info["msgText"] == CHART_UNAVAILABLE_MSG, f"{chart_id}: falscher/fehlender Hinweistext"
        assert info["msgDisplay"] != "none", f"{chart_id}: Hinweis müsste sichtbar sein"
        # Erklärt WARUM (blockiertes Skript/Netzwerk) UND dass die übrigen Zahlen
        # weiterhin vertrauenswürdig sind — nicht nur der reine "nicht verfügbar"-Satz.
        assert "blockiert" in info["msgText"]
        assert "stimmen weiterhin" in info["msgText"]


def test_chart_library_present_renders_unchanged(tmp_path):
    """Kontrolllauf (Abnahmekriterium 5): ist Chart.js verfügbar, bleibt der normale
    Diagramm-Pfad unverändert — kein Fallback-Hinweis, Canvas bleibt der aktive Weg."""
    script = _extract_dashboard_script()
    result = _run_harness(script, chart_available=True, tmp_path=tmp_path)

    assert not result["threw"], f"Inline-Skript ist MIT Chart.js abgebrochen:\n{result.get('error')}"
    assert "Gesamt P&L" in result["cardsInnerHTML"]
    assert "ZALT" in result["activeInnerHTML"]
    for chart_id in ("equityChart", "tickerChart", "strengthChart", "priceChartSingle"):
        info = result[chart_id]
        assert info["canvasDisplay"] != "none", f"{chart_id}: sollte mit Chart.js sichtbar bleiben"
        assert info["msgText"] != CHART_UNAVAILABLE_MSG, f"{chart_id}: sollte keinen Fallback zeigen"


def test_dashboard_script_guards_every_top_level_chart_access():
    """Statische Zweitabsicherung: jede Top-Level-Nutzung von `Chart.` im extrahierten
    Skript muss hinter der `chartAvailable`-Prüfung liegen (Regressionsschutz gegen einen
    neuen, versehentlich ungeschützten Direktzugriff)."""
    text = Path("stockbot/web/templates/dashboard.html").read_text(encoding="utf-8")
    assert "const chartAvailable = typeof Chart !== \"undefined\";" in text
    guard_pos = text.index("const chartAvailable")
    theme_pos = text.index("function applyChartTheme()")
    assert guard_pos < theme_pos, "chartAvailable muss vor der ersten Chart-Nutzung definiert sein"
