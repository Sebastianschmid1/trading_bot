"""
📊 Web-Dashboard für den Stock Signal Bot

Pro Nutzer über einen geheimen Token-Link erreichbar (kein Login nötig):
  GET /dashboard/{token}     → die HTML-Seite (Equity-Kurve, Stats, Trades)
  GET /api/{token}/data      → die Kennzahlen als JSON (von der Seite geladen)

Start (separater Prozess neben bot.py):
  python dashboard.py
  # oder: uvicorn dashboard:app --host 0.0.0.0 --port 8000
"""

import sys
import math
import logging
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stockbot.core import db
from stockbot.market import strategies
from stockbot.core import metrics as metrics_mod
from stockbot.core import mode_reporting
from stockbot.core.settings import validate_config, assert_postgres_backend
from stockbot.core.logging_setup import configure_logging
from stockbot.broker import client as broker
from stockbot import config
from stockbot.config import DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_BASE_URL, BERLIN_TZ
from stockbot.core.evaluator import get_current_price, trade_pnl, effective_leverage

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
    # Ablehnungsgründe im UI sichtbar (PLAN_CHECKLIST.md Phase 3): Terminologie nach
    # Stylekonzept.md §25.2 ("Durch Risikoregel blockiert" statt Ticket-ID/Fachjargon).
    "leverage_blocked": "Durch Risikoregel blockiert (Hebel über Maximum)",
    "submit_failed": "Order konnte nicht gesendet werden",
    "missing_order_id": "Order ohne Broker-Bestätigung",
    "requested": "Verkauf angefragt",
}

TRADE_STATUS_LABELS = {
    "active": "Aktiv",
    "broker_pending": "Broker wartet",
    "broker_closing": "Verkauf läuft",
    "broker_failed": "Broker fehlgeschlagen",
    "closed": "Geschlossen",
}


def broker_status_label(broker_status: str | None) -> str:
    if not broker_status:
        return "—"
    return BROKER_STATUS_LABELS.get(broker_status, broker_status.replace("_", " ").title())


def trade_status_label(status: str | None, broker_status: str | None = None) -> str:
    base = TRADE_STATUS_LABELS.get(status or "", (status or "").replace("_", " ").title() or "—")
    if status == "broker_pending" and broker_status:
        return f"{base} · {broker_status_label(broker_status)}"
    if status == "broker_closing" and broker_status:
        return f"{base} · {broker_status_label(broker_status)}"
    return base

def _berlin_hhmm(ts: str | None) -> str:
    """SQLite-Zeitstempel (UTC, 'YYYY-MM-DD HH:MM:SS') → 'HH:MM' in Berliner Zeit."""
    if not ts:
        return ""
    try:
        return (datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
                .astimezone(BERLIN_TZ).strftime("%H:%M"))
    except Exception:
        return ts[11:16]


def _epoch_ms(ts: str | None) -> float | None:
    """UTC-SQLite-Zeitstempel ('YYYY-MM-DD[ HH:MM:SS]') → Epoch-Millisekunden (UTC-Instant).
    Der Browser rendert daraus die lokale (Berliner) Zeit. None bei fehlendem/ungültigem Wert."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.ensure_strategy_versions_published()   # Gate P5: produktive Strategieversionen persistent verfügbar
    try:
        removed = db.delete_expired_sessions()
        if removed:
            log.info(f"Session-Cleanup: {removed} abgelaufene Web-Session(s) entfernt.")
    except Exception as e:
        log.warning(f"Session-Cleanup fehlgeschlagen: {e}")
    yield


app = FastAPI(title="Stock Signal Bot — Dashboard", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Security-Header (gegen Sniffing/Clickjacking/Referer-Leak) ───────────────

@app.middleware("http")
async def security_headers(request, call_next):
    """Setzt defensive HTTP-Header auf jede Antwort. HSTS nur bei aktivem TLS
    (config.HSTS_ENABLED), damit man sich lokal über http nicht aussperrt."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Fonts und Styles bleiben vollständig lokal; Inline-Styles werden noch von
    # bestehenden Templates verwendet.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data:; "
        "script-src 'self' https://telegram.org https://cdn.jsdelivr.net 'unsafe-inline'; "
        "frame-src https://oauth.telegram.org; base-uri 'self'; form-action 'self'",
    )
    if config.HSTS_ENABLED:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ── Kennzahlen aus der DB aufbauen ──────────────────────────────────────────

def _trade_strategy(t: dict) -> str:
    """Strategie-Schlüssel eines Trades (aus dem gespeicherten Signal; Fallback 'standard')."""
    return (t.get("signal") or {}).get("strategy", "standard")


RANGE_CHOICES = [0, 1, 7, 14, 30]   # 0 = „Alle"; sonst letzte N Tage


def sizing_hint(trade_size: float, trades: list[dict]) -> dict | None:
    """Empfiehlt eine größere Trade-Größe, wenn die aktuelle (z. B. 25 $) unter den Aktienkursen
    liegt und deshalb häufig Bruchteile/Vormerkungen nötig sind.

    Rückgabe {frac_rate, recommended, trade_size} oder None (genug ganze Aktien bezahlbar / zu
    wenig Daten). `recommended` ist so gewählt, dass ~80 % der Signale als ganze Aktie passen."""
    prices = [float((t.get("signal") or {}).get("price")) for t in trades
              if (t.get("signal") or {}).get("price")]
    if len(prices) < 5 or not trade_size:
        return None
    over = sum(1 for p in prices if p > trade_size)
    frac_rate = over / len(prices)
    if frac_rate < 0.30:                      # meistens ganze Aktien bezahlbar → kein Hinweis
        return None
    prices.sort()
    p80 = prices[min(len(prices) - 1, int(0.8 * len(prices)))]
    recommended = int(math.ceil(p80 / 50.0) * 50)   # auf nächste 50 aufrunden
    return {"frac_rate": round(frac_rate * 100), "recommended": recommended,
            "trade_size": round(trade_size)}


def broker_account_snapshot(user: dict) -> dict | None:
    """Live-Konto-Snapshot (Buying Power, Cash) des Nutzers von Alpaca — oder None.

    Nur für Nutzer mit echter Broker-Ausführung (`broker_exec`). Best-effort: bei fehlenden
    Keys/Verbindung/Fehler kommt None zurück (das Dashboard blendet die Kachel dann aus).
    Eigene, schlanke Client-Wahl (keine Webapp-Abhängigkeit → kein Import-Zyklus)."""
    if not (user and user.get("broker_exec")):
        return None
    try:
        client = None
        if user.get("broker_platform") == "alpaca":
            creds = db.get_decrypted_credentials(user["user_id"])
            if creds:
                client = broker.make_client(creds[0], creds[1], paper=config.ALPACA_PAPER)
        if client is None and config.ALPACA_ENABLED:
            client = broker._get_client()
        if client is None:
            return None
        acct = broker.account_summary(client)
        return acct if acct.get("ok") else None
    except Exception as e:
        log.warning(f"[{user.get('user_id')}] Broker-Account-Snapshot fehlgeschlagen: {e}")
        return None


def build_dashboard_data(user: dict, strategy: str | None = None, days: int | None = None) -> dict:
    """Aggregiert die Dashboard-Kennzahlen eines Nutzers aus der DB.
    `strategy` = Schlüssel → nur Trades dieser Strategie; None → alle.
    `days` = letzte N Tage (für Kennzahlen/Equity der abgeschlossenen Trades); None/0 = alle."""
    user_id = user["user_id"]
    closed = db.get_closed_trades(user_id)
    active = db.get_active_trades(user_id)
    if strategy:
        closed = [t for t in closed if _trade_strategy(t) == strategy]
        active = [t for t in active if _trade_strategy(t) == strategy]
    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        closed = [t for t in closed if (t.get("trade_date") or "") >= cutoff]

    # Equity-Kurve: tägliches P&L kumuliert
    daily = OrderedDict()
    for t in closed:
        daily[t["trade_date"]] = daily.get(t["trade_date"], 0.0) + (t["pnl_eur"] or 0.0)

    equity, cum = [], 0.0
    for d, pnl in daily.items():
        cum += pnl
        equity.append({"date": d, "cumulative": round(cum, 2), "daily": round(pnl, 2)})

    # Zusammenfassung (inkl. Profitfaktor über metrics)
    total_pnl = round(sum((t["pnl_eur"] or 0.0) for t in closed), 2)
    wins   = sum(1 for t in closed if (t["pnl_eur"] or 0) > 0)
    losses = sum(1 for t in closed if (t["pnl_eur"] or 0) < 0)
    n = len(closed)
    win_rate = round(wins / n * 100, 1) if n else 0.0
    profit_factor = metrics_mod.compute_metrics(closed)["profit_factor"]   # None = ∞ / keine Daten

    # P&L pro Ticker
    by_ticker = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    for t in closed:
        bt = by_ticker[t["ticker"]]
        bt["pnl"]    += (t["pnl_eur"] or 0.0)
        bt["trades"] += 1
        if (t["pnl_eur"] or 0) > 0:
            bt["wins"] += 1
    ticker_stats = sorted(
        [{"ticker": k, "pnl": round(v["pnl"], 2), "trades": v["trades"], "wins": v["wins"]}
         for k, v in by_ticker.items()],
        key=lambda x: x["pnl"], reverse=True,
    )

    # Intraday-Ticks (für aktuellen Kurs + Verlaufs-Charts) — aus dem 60s-Monitor
    ticks = db.get_today_ticks(user_id)

    def _current_price(t: dict) -> float:
        """Aktueller Kurs: letzter 60s-Tick, sonst Live-Abruf (Fallback: Einstieg)."""
        pts = ticks.get(t["ticker"], [])
        if pts:
            return pts[-1]["price"]
        return get_current_price(t["ticker"], t["entry"])

    # Offene/aktive Trades (inkl. aktuellem Kurs + unrealisiertem P&L)
    size = user["trade_size_eur"] or 1.0
    active_view = []
    strategy_labels = {s.key: s.label for s in strategies.all_strategies()}
    for t in active:
        cur = _current_price(t)
        leverage = effective_leverage(t["signal"])   # realisierter Hebel (Aktien-Fallback → 1×)
        # Optionsbewusst (Omega-Näherung im Web); für Aktien das lineare Hebelmodell.
        pnl_pct, pnl_eur = trade_pnl(t, cur, size)
        active_view.append({
            "ticker":      t["ticker"],
            "direction":   t["direction"],
            "status_text": trade_status_label("active"),
            "entry":       t["entry"],
            "current":     cur,
            "leverage":    leverage,
            "option":      t["signal"].get("option_symbol"),
            "invested":    round(size, 2),                    # eingesetzte Margin (dein Geld)
            "exposure":    round(size * leverage, 2),         # Positionsgröße im Markt (Margin × Hebel)
            "pnl_pct":     round(pnl_pct, 2),                 # reine Kursbewegung
            "roi_pct":     round(pnl_eur / size * 100, 2),    # Rendite auf die Margin (inkl. Hebel) = passt zum €
            "pnl_eur":     round(pnl_eur, 2),
            "stop_loss":   t["signal"].get("stop_loss"),
            "take_profit": t["signal"].get("take_profit"),
            "strategy":    _trade_strategy(t),
            "strategy_label": strategy_labels.get(_trade_strategy(t), _trade_strategy(t)),
        })

    # Intraday-Verlauf (strategiespezifischer Rohscore + Kurs) je aktivem Ticker — aus 60s-Ticks
    intraday = []
    for t in active:
        pts = ticks.get(t["ticker"], [])
        intraday.append({
            "ticker": t["ticker"],
            "strategy": _trade_strategy(t),
            "strategy_label": strategy_labels.get(_trade_strategy(t), _trade_strategy(t)),
            "entry": t["entry"],
            "stop_loss": t["signal"].get("stop_loss"),
            "take_profit": t["signal"].get("take_profit"),
            "points": [
                {"t": _berlin_hhmm(p.get("ts")),   # HH:MM (Berliner Zeit)
                 "price": p["price"], "strength": p["strength"]}
                for p in pts
            ],
        })

    # Einzel-Trades als Kursverlauf (%-Wertentwicklung ab Einstieg) auf GEMEINSAMER Zeitachse:
    # x = echter Zeitpunkt (Epoch-ms, UTC-Instant → der Browser zeigt Berliner Zeit), y = % ab
    # Einstieg (mit Hebel). Ein geschlossener Trade endet an seinem Ausstiegs-Zeitpunkt und wird
    # NICHT weiter gezeichnet; ein offener Trade läuft bis "jetzt" (rechter Rand).
    _strat_label = strategy_labels
    events_by_trade = db.get_events_by_trade(user_id)
    now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
    _TERMINAL = ("closed", "broker_failed", "rejected", "expired")

    def _entry_ms(t: dict) -> float | None:
        # Einstieg = Anlage-Zeitpunkt (created_at), Fallback Handelstag.
        return _epoch_ms(t.get("created_at")) or _epoch_ms(t.get("trade_date"))

    def _exit_ms(t: dict, entry_ms: float) -> float:
        # Ausstieg = präziser Schließ-Event (auch bei Demo-Closes vorhanden), sonst broker_updated_at.
        # Ohne belastbaren Ausstieg auf den Einstieg zurückfallen (Altdaten → kurzer Punkt).
        evs = events_by_trade.get(t.get("id")) or []
        term = [e for e in evs if e.get("to_status") in _TERMINAL]
        ms = _epoch_ms(term[-1]["ts"]) if term else _epoch_ms(t.get("broker_updated_at"))
        return ms if (ms is not None and ms >= entry_ms) else entry_ms

    trades_curves = []
    for t in closed:
        if not t.get("entry"):
            continue
        entry_ms = _entry_ms(t)
        if entry_ms is None:
            continue
        pct = round(t["pnl_pct"] or 0.0, 2)
        trades_curves.append({
            "ticker":    t["ticker"],
            "date":      t["trade_date"],
            "status":    "closed",
            "final_pct": pct,
            "final_eur": round(t["pnl_eur"] or 0.0, 2),
            "strategy":  _strat_label.get(_trade_strategy(t), _trade_strategy(t)),
            "points":    [{"x": entry_ms, "y": 0.0}, {"x": _exit_ms(t, entry_ms), "y": pct}],
        })
    for t, v in zip(active, active_view):
        entry = v["entry"]
        lev = v["leverage"] or 1.0
        entry_ms = _entry_ms(t)
        if entry_ms is None:
            continue
        valid = [p for p in ticks.get(t["ticker"], []) if p.get("price") is not None]
        points = [{"x": entry_ms, "y": 0.0}]                     # Einstieg = 0 %
        for p in valid:
            tms = _epoch_ms(p.get("ts"))
            if tms is None:
                continue
            y = (p["price"] / entry - 1.0) * 100.0 * lev if entry else 0.0
            points.append({"x": tms, "y": round(y, 2)})
        # Offener Trade endet "jetzt" (rechter Rand): bis now flach auf den aktuellen Wert verlängern.
        if points[-1]["x"] < now_ms:
            cur = v.get("current")
            y_now = (cur / entry - 1.0) * 100.0 * lev if (entry and cur is not None) else points[-1]["y"]
            points.append({"x": now_ms, "y": round(y_now, 2)})
        trades_curves.append({
            "ticker":    v["ticker"],
            "date":      t["trade_date"],
            "status":    "active",
            "final_pct": v["pnl_pct"],
            "final_eur": v["pnl_eur"],
            "strategy":  _strat_label.get(_trade_strategy(t), _trade_strategy(t)),
            "points":    points,
        })

    # X-Domäne der gemeinsamen Zeitachse: frühester Einstieg → jetzt (rechter Rand = "jetzt").
    _all_x = [p["x"] for c in trades_curves for p in c["points"]]
    trades_curves_x = {"min": min(_all_x), "max": now_ms} if _all_x else {"min": None, "max": None}

    # Strategie-Tabs: „Alle" + die vom Nutzer gewählten Strategien
    user_strat_keys = user.get("strategies") or [
        s.strip() for s in (user.get("strategy") or "").split(",") if s.strip()] or ["standard"]
    strat_tabs = [{"key": "", "label": "Alle"}] + [
        {"key": s.key, "label": s.label}
        for s in strategies.all_strategies() if s.key in user_strat_keys
    ]

    return {
        "username":        user.get("username") or f"user_{user_id}",
        "trade_size_eur":  user["trade_size_eur"],
        "broker_platform": user.get("broker_platform"),
        "broker_account":  broker_account_snapshot(user),
        "sizing_hint":     sizing_hint(user["trade_size_eur"] or 0.0, closed + active),
        "is_active":       user.get("is_active", True),
        "strategy":        strategy or "",
        "strategies":      strat_tabs,
        "days":            days or 0,
        "ranges":          RANGE_CHOICES,
        "summary": {
            "total_pnl":    total_pnl,
            "total_closed": n,
            "wins":         wins,
            "losses":       losses,
            "win_rate":     win_rate,
            "profit_factor": profit_factor,
            "active_count": len(active),
        },
        # Mode-getrennte Reports (RES-002 / Gate P6): Paper aus den Legacy-Trades, Shadow aus den
        # persistierten Shadow-Snapshots (W3.5). Strukturell nie über Modi hinweg vermischt.
        "mode_reports":  mode_reporting.build_mode_reports(
            closed, shadow_snapshots=db.get_shadow_snapshots()),
        "equity":        equity,
        "ticker_stats":  ticker_stats,
        "trades_curves": trades_curves,
        "trades_curves_x": trades_curves_x,
        "active_trades": active_view,
        "intraday":      intraday,
        "generated_at":  datetime.now().strftime("%d.%m.%Y %H:%M"),
        "status_texts": {
            "active": TRADE_STATUS_LABELS["active"],
            "broker_pending": TRADE_STATUS_LABELS["broker_pending"],
            "broker_closing": TRADE_STATUS_LABELS["broker_closing"],
            "broker_failed": TRADE_STATUS_LABELS["broker_failed"],
            "closed": TRADE_STATUS_LABELS["closed"],
        },
    }


# ── Routen ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (
        "<html><body style='font-family:sans-serif;background:#0f1420;color:#e6e6e6;"
        "padding:2rem'><h2>📈 Stock Signal Bot</h2>"
        "<p><a href='/login' style='color:#4f8cff'>➡️ Zur Web-App anmelden</a> "
        "(Signale annehmen, Einstellungen, Watchlist).</p>"
        "<p>Oder öffne deinen persönlichen Dashboard-Link über <code>/dashboard</code> im Telegram-Bot.</p>"
        "</body></html>"
    )


@app.get("/dashboard/{token}", response_class=HTMLResponse)
def dashboard_page(token: str, request: Request):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Ungültiger oder abgelaufener Link.")
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "active": "dashboard",
        "unread": db.unread_count(user["user_id"]),
        "dashboard_token": token,
        "dashboard_app_url": f"/auth/token?token={token}",
    })


@app.get("/api/{token}/data")
def dashboard_data(token: str, strategy: str | None = None, days: int | None = None):
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=404, detail="Ungültiger Token.")
    return JSONResponse(build_dashboard_data(user, strategy=strategy or None, days=days or None))


@app.get("/api/{token}/analyze/{ticker}")
def dashboard_analyze(token: str, ticker: str):
    """Detail-Analyse einer Aktie: Modell-Einflussfaktoren der letzten 7 Tage
    (für den Klick auf einen Trade im Dashboard)."""
    if not db.get_user_by_token(token):
        raise HTTPException(status_code=404, detail="Ungültiger Token.")
    from stockbot.market import analyzer
    return JSONResponse(analyzer.factor_history(ticker.upper(), days=7))


# Interaktive Web-App (Login, Signale, Einstellungen, Watchlist, Mitteilungen) einhängen.
# Import am Ende, damit build_dashboard_data/app bereits definiert sind (kein Zyklus).
from stockbot.web.webapp import router as _app_router   # noqa: E402
app.include_router(_app_router)


def run():
    """Startet den Dashboard-Webserver (blockierend). Wird von bot.py im Thread oder hier direkt genutzt."""
    validate_config()
    assert_postgres_backend()   # Prod läuft Postgres-only (Scheibe 9); SQLite nur Dev/Test
    import uvicorn
    log.info(f"📊 Dashboard läuft auf http://{DASHBOARD_HOST}:{DASHBOARD_PORT}  (Link: {DASHBOARD_BASE_URL})")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="warning")


if __name__ == "__main__":
    configure_logging(
        log_format=config.LOG_FORMAT, service="stockbot-dashboard",
        pseudonym_key=config.ENCRYPTION_KEY,
    )
    run()
