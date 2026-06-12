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
import logging
from collections import OrderedDict, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse

from stockbot.core import db
from stockbot.market import strategies
from stockbot.core import metrics as metrics_mod
from stockbot.config import DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_BASE_URL
from stockbot.core.evaluator import get_current_price, realized_pnl

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Stock Signal Bot — Dashboard", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


# ── Kennzahlen aus der DB aufbauen ──────────────────────────────────────────

def _trade_strategy(t: dict) -> str:
    """Strategie-Schlüssel eines Trades (aus dem gespeicherten Signal; Fallback 'standard')."""
    return (t.get("signal") or {}).get("strategy", "standard")


RANGE_CHOICES = [0, 1, 7, 14, 30]   # 0 = „Alle"; sonst letzte N Tage


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
    active_view = []
    for t in active:
        cur = _current_price(t)
        leverage = (t["signal"].get("leverage") or 1.0)
        pnl_pct, pnl_eur = realized_pnl(t["entry"], cur, t["direction"],
                                        user["trade_size_eur"], leverage)
        active_view.append({
            "ticker":      t["ticker"],
            "direction":   t["direction"],
            "entry":       t["entry"],
            "current":     cur,
            "leverage":    leverage,
            "pnl_pct":     round(pnl_pct, 2),
            "pnl_eur":     round(pnl_eur, 2),
            "stop_loss":   t["signal"].get("stop_loss"),
            "take_profit": t["signal"].get("take_profit"),
        })

    # Intraday-Verlauf (Stärke + Kurs) je aktivem Ticker — aus den 60s-Ticks
    intraday = []
    for t in active:
        pts = ticks.get(t["ticker"], [])
        intraday.append({
            "ticker": t["ticker"],
            "entry": t["entry"],
            "stop_loss": t["signal"].get("stop_loss"),
            "take_profit": t["signal"].get("take_profit"),
            "points": [
                {"t": p["ts"][11:16] if p.get("ts") else "",   # HH:MM
                 "price": p["price"], "strength": p["strength"]}
                for p in pts
            ],
        })

    # Einzel-Trades als Kursverlauf (%-Wertentwicklung ab Einstieg): historische + offene.
    # Offene Trades = echte Intraday-Kurve (60s-Ticks); abgeschlossene = Einstieg→Ausstieg.
    # x = normierter Verlauf 0..100 (0 = Einstieg, 100 = jetzt/Ausstieg), y = % ab Einstieg (mit Hebel).
    _strat_label = {s.key: s.label for s in strategies.all_strategies()}
    trades_curves = []
    for t in closed:
        if not t.get("entry"):
            continue
        pct = round(t["pnl_pct"] or 0.0, 2)
        trades_curves.append({
            "ticker":    t["ticker"],
            "date":      t["trade_date"],
            "status":    "closed",
            "final_pct": pct,
            "final_eur": round(t["pnl_eur"] or 0.0, 2),
            "strategy":  _strat_label.get(_trade_strategy(t), _trade_strategy(t)),
            "points":    [{"x": 0.0, "y": 0.0}, {"x": 100.0, "y": pct}],
        })
    for t, v in zip(active, active_view):
        entry = v["entry"]
        lev = v["leverage"] or 1.0
        valid = [p for p in ticks.get(t["ticker"], []) if p.get("price") is not None]
        points = [{"x": 0.0, "y": 0.0}]                          # Einstieg = 0 %
        nn = len(valid)
        for i, p in enumerate(valid):
            y = (p["price"] / entry - 1.0) * 100.0 * lev if entry else 0.0
            points.append({"x": round((i + 1) / nn * 100.0, 2), "y": round(y, 2)})
        if nn == 0:                                             # keine Ticks → Einstieg→aktuell
            points.append({"x": 100.0, "y": v["pnl_pct"]})
        trades_curves.append({
            "ticker":    v["ticker"],
            "date":      t["trade_date"],
            "status":    "active",
            "final_pct": v["pnl_pct"],
            "final_eur": v["pnl_eur"],
            "strategy":  _strat_label.get(_trade_strategy(t), _trade_strategy(t)),
            "points":    points,
        })

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
        "equity":        equity,
        "ticker_stats":  ticker_stats,
        "trades_curves": trades_curves,
        "active_trades": active_view,
        "intraday":      intraday,
        "generated_at":  datetime.now().strftime("%d.%m.%Y %H:%M"),
    }


# ── Routen ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (
        "<html><body style='font-family:sans-serif;background:#0f1420;color:#e6e6e6;"
        "padding:2rem'><h2>📊 Stock Signal Bot — Dashboard</h2>"
        "<p>Öffne deinen persönlichen Link über den Befehl <code>/dashboard</code> im Telegram-Bot.</p>"
        "</body></html>"
    )


@app.get("/dashboard/{token}", response_class=HTMLResponse)
def dashboard_page(token: str):
    if not db.get_user_by_token(token):
        raise HTTPException(status_code=404, detail="Ungültiger oder abgelaufener Link.")
    return FileResponse(STATIC_DIR / "dashboard.html")


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


def run():
    """Startet den Dashboard-Webserver (blockierend). Wird von bot.py im Thread oder hier direkt genutzt."""
    import uvicorn
    log.info(f"📊 Dashboard läuft auf http://{DASHBOARD_HOST}:{DASHBOARD_PORT}  (Link: {DASHBOARD_BASE_URL})")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="warning")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run()
