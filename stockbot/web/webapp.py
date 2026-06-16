"""
Interaktive Website (Phase 1–2): Login, Signal-Feed (Annehmen/Ablehnen/Hebel), Einstellungen,
Watchlist, In-App-Mitteilungen (mit SSE-Live-Feed). Läuft parallel zum Telegram-Bot — beide
nutzen dieselbe DB und dieselbe Service-Schicht (stockbot/services/*).

Wird als Router in stockbot/web/dashboard.py eingehängt (ein Server für Dashboard + App).
"""

import os
import json
import time
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.market import strategies
from stockbot.market import asset_classes
from stockbot.market import analyzer
from stockbot.broker import client as broker
from stockbot.broker import sizing
from stockbot import config
from stockbot.services import trades as trade_svc
from stockbot.services import settings as settings_svc
from stockbot.services import watchlist as watchlist_svc
from stockbot.web import auth
from stockbot.web.dashboard import build_dashboard_data

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def _csrf_protect(request: Request):
    """CSRF-Schutz: state-ändernde Requests müssen vom eigenen Origin kommen."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.is_same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültiger Origin (CSRF-Schutz).")


router = APIRouter(dependencies=[Depends(_csrf_protect)])


# ── Alpaca-Helfer (leichtgewichtig, ohne Telegram-Abhängigkeit) ──────────────

def _alpaca_ready(user: dict) -> bool:
    return bool(user and user.get("broker_platform") == "alpaca") or config.ALPACA_ENABLED


def _alpaca_client(user: dict):
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return broker.make_client(creds[0], creds[1], paper=config.ALPACA_PAPER)
    return broker._get_client()


def _alpaca_keys(user: dict) -> tuple[str | None, str | None]:
    """Roh-Keys (für Options-Marktdaten) des Nutzers oder die globalen .env-Keys."""
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return creds[0], creds[1]
    return config.ALPACA_API_KEY, config.ALPACA_API_SECRET


def _attach_demo_option(user: dict, ticker: str) -> None:
    """Wählt bei Hebel>1 für den gerade aktivierten Trade einen Optionskontrakt fürs Budget und
    schreibt ihn ins Signal (Demo-Options-Simulation). Best-effort; nur falls Alpaca-Daten da."""
    trade = db.get_trade(user["user_id"], ticker)
    if not trade or trade["status"] not in ("active", "broker_pending"):
        return
    entry = trade.get("entry") or (trade.get("signal") or {}).get("price")
    if not entry:
        return
    client = _alpaca_client(user) if _alpaca_ready(user) else None
    key, sec = _alpaca_keys(user)

    def _selector(budget, target_leverage):
        return broker.select_option_for_leverage(
            ticker, float(entry), target_leverage, budget, client=client, api_key=key, api_secret=sec)

    try:
        trade_svc.attach_option_for_trade(user, trade, _selector)
    except Exception as e:
        log.warning(f"[{user['user_id']}] Options-Auswahl (Demo) fehlgeschlagen: {e}")


def _broker_will_execute(user: dict) -> bool:
    return bool(user.get("broker_exec") and _alpaca_ready(user) and _alpaca_client(user) is not None)


def _execute_broker_order_for_web(user: dict, trade: dict) -> dict:
    """Synchrone Broker-Ausführung für die Web-App.

    Hält `broker_pending`, bis Alpaca wirklich `filled` meldet; bei nicht ausgeführter
    Order bleibt der Trade aus der aktiven Demo-Trade-Liste heraus.
    """
    if not trade or not _broker_will_execute(user):
        return {"ok": True, "status": trade.get("status", "active") if trade else "unavailable"}
    client = _alpaca_client(user)
    sig = trade.get("signal", {})
    ticker = trade["ticker"]
    entry = trade.get("entry") or sig.get("price")
    if not entry:
        db.mark_broker_failed(user["user_id"], ticker, broker_status="missing_entry")
        return {"ok": False, "status": "broker_failed", "msg": "Kein Einstiegskurs verfügbar."}

    leverage = float(sig.get("leverage", 1.0) or 1.0)
    budget = float(user["trade_size_eur"])
    extended = bool(config.EXTENDED_HOURS and broker.market_open(client) is False)
    if sig.get("option_symbol") and not extended:
        plan = {"kind": "option", "option_symbol": sig["option_symbol"], "qty": int(sig["contracts"]),
                "premium": float(sig["entry_premium"])}
    else:
        plan = sizing.plan_order(float(entry), budget, leverage,
                                 option_selector=None, extended=extended)

    if plan["kind"] == "none":
        db.mark_broker_failed(user["user_id"], ticker, broker_status="not_submitted")
        return {"ok": False, "status": "broker_failed", "msg": "Budget reicht nicht für eine Broker-Order."}
    if plan["kind"] == "option":
        res = broker.submit_option_buy(plan["option_symbol"], plan["qty"], client)
    elif plan.get("qty"):
        if extended:
            res = broker.submit_buy(ticker, qty=plan["qty"], limit_price=float(entry), extended_hours=True, client=client)
        else:
            res = broker.submit_buy(ticker, qty=plan["qty"], client=client)
    else:
        if extended:
            db.mark_broker_failed(user["user_id"], ticker, broker_status="not_submitted")
            return {"ok": False, "status": "broker_failed", "msg": "Bruchteile sind in erweiterten Handelszeiten nicht möglich."}
        res = broker.submit_buy(ticker, notional=plan["notional"], client=client)

    if not res.get("ok"):
        db.mark_broker_failed(user["user_id"], ticker, broker_status="submit_failed")
        return {"ok": False, "status": "broker_failed", "msg": f"Broker-Order nicht angenommen: {res.get('detail')}"}

    order_id = res.get("id", "")
    db.mark_broker_pending(user["user_id"], ticker, order_id=order_id, broker_status="accepted")
    fill = broker.get_order_status(order_id, client)
    status = fill.get("status", "unbekannt")
    if status == "filled":
        db.mark_broker_filled(user["user_id"], ticker, broker_status=status,
                              filled_qty=fill.get("filled_qty"),
                              filled_avg_price=fill.get("filled_avg_price"))
        return {"ok": True, "status": "filled", "msg": f"{ticker} gekauft."}
    if status in ("rejected", "canceled", "expired"):
        db.mark_broker_failed(user["user_id"], ticker, broker_status=status)
        return {"ok": False, "status": "broker_failed", "msg": f"Broker-Order nicht ausgeführt ({status})."}
    db.mark_broker_pending(user["user_id"], ticker, order_id=order_id, broker_status=status)
    return {"ok": True, "status": "broker_pending", "msg": f"Broker-Order angenommen, aber noch nicht ausgeführt ({status})."}


def _render(name: str, request: Request, user: dict, active: str = "", msg: str = "", **ctx):
    return templates.TemplateResponse(request, name, {
        "user": user, "active": active, "msg": msg,
        "unread": db.unread_count(user["user_id"]) if user else 0, **ctx,
    })


def _redirect(path: str):
    return RedirectResponse(path, status_code=303)


# ── On-Demand-Signal-Scan (Cache + Mini-Chart) ───────────────────────────────

SCAN_TTL_S = 600                       # angeforderte Signale 10 Min cachen (Reload ≠ Neu-Scan)
_scan_cache: dict[int, dict] = {}      # user_id -> {"at": ts, "asset": key, "signals": [...]}


def _sparkline(closes: list, width: int = 120, height: int = 32) -> dict | None:
    """Baut aus Schlusskursen die Punkte einer Inline-SVG-Sparkline (keine JS/Deps nötig).
    Gibt {"points": "x,y x,y …", "up": bool} oder None bei zu wenig Daten."""
    pts = [c for c in (closes or []) if c is not None]
    if len(pts) < 2:
        return None
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    n = len(pts)
    coords = []
    for i, c in enumerate(pts):
        x = round(i / (n - 1) * (width - 2) + 1, 1)
        y = round(height - 1 - (c - lo) / span * (height - 2), 1)
        coords.append(f"{x},{y}")
    return {"points": " ".join(coords), "up": pts[-1] >= pts[0]}


def _scanned_for(user: dict) -> list:
    """Frische, on-demand angeforderte Signale des Nutzers (mit Sparkline-Punkten) oder []."""
    entry = _scan_cache.get(user["user_id"])
    if not entry or time.time() - entry["at"] > SCAN_TTL_S or entry["asset"] != (user.get("asset_pref") or "stocks"):
        return []
    out = []
    for s in entry["signals"]:
        card = dict(s)
        card["spark"] = _sparkline(s.get("spark_closes") or [])
        out.append(card)
    return out


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    if auth.current_user(request):
        return _redirect("/app")
    return templates.TemplateResponse(request, "login.html", {
        "user": None, "active": "", "msg": msg, "unread": 0,
        "telegram_bot": os.getenv("TELEGRAM_BOT_USERNAME"),
        "base_url": config.DASHBOARD_BASE_URL,
    })


@router.get("/auth/token")
def auth_token(request: Request, token: str = ""):
    if not auth.rate_ok(f"token:{request.client.host if request.client else '?'}"):
        return _redirect("/login?msg=Zu+viele+Versuche+%E2%80%93+kurz+warten.")
    u = db.get_user_by_token(token)
    if not u:
        return _redirect("/login?msg=Ung%C3%BCltiger+Token")
    return auth.login_response(_redirect("/app"), u["user_id"])


@router.get("/auth/telegram")
def auth_telegram(request: Request):
    if not auth.rate_ok(f"tg:{request.client.host if request.client else '?'}"):
        return _redirect("/login?msg=Zu+viele+Versuche+%E2%80%93+kurz+warten.")
    uid = auth.verify_telegram_login(dict(request.query_params))
    if not uid or not db.get_user(uid):
        return _redirect("/login?msg=Telegram-Login+fehlgeschlagen")
    return auth.login_response(_redirect("/app"), uid)


@router.post("/logout")
def logout(request: Request):
    return auth.logout_response(request, _redirect("/login"))


@router.post("/logout/all")
def logout_all(request: Request):
    """Beendet ALLE Sessions des Nutzers (z. B. nach verlorenem Gerät)."""
    user = auth.current_user(request)
    if user:
        db.delete_user_sessions(user["user_id"])
    resp = _redirect("/login?msg=Auf+allen+Ger%C3%A4ten+abgemeldet.")
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp


# ── App-Startseite: Signale + aktive Trades ──────────────────────────────────

@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, msg: str = "", atf: str = ""):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    pending = []
    for t in db.get_pending_trades(user["user_id"]):
        sig = t.get("signal", {}) or {}
        pending.append({
            "ticker": t["ticker"], "direction": t.get("direction") or sig.get("direction", "long"),
            "price": sig.get("price") or t.get("entry") or 0.0, "strength": sig.get("strength"),
            "leverage": sig.get("leverage", 1.0) or 1.0,
            "stop_loss": sig.get("stop_loss"), "take_profit": sig.get("take_profit"),
        })
    active_trades = build_dashboard_data(user)["active_trades"]
    broker_pending = []
    for t in db.get_broker_pending_trades(user["user_id"]):
        sig = t.get("signal", {}) or {}
        broker_pending.append({
            "ticker": t["ticker"], "direction": t.get("direction") or sig.get("direction", "long"),
            "entry": t.get("entry") or sig.get("price") or 0.0,
            "leverage": sig.get("leverage", 1.0) or 1.0,
            "broker_status": t.get("broker_status") or "accepted",
            "broker_order_id": t.get("broker_order_id"),
        })
    # Anlageklasse je aktivem Trade ableiten + optional filtern (atf = Klassen-Key oder leer = alle)
    label_by_key = {c.key: c.label for c in asset_classes.all_asset_classes()}
    for t in active_trades:
        t["asset_key"] = asset_classes.classify_ticker(t["ticker"])
        t["asset_label"] = label_by_key.get(t["asset_key"], "Aktien")
    if atf in label_by_key:
        active_trades = [t for t in active_trades if t["asset_key"] == atf]
    asset_pref = user.get("asset_pref") or asset_classes.DEFAULT_ASSET
    return _render("app.html", request, user, active="home", msg=msg,
                   pending=pending, broker_pending=broker_pending, active_trades=active_trades,
                   leverages=config.LEVERAGE_CHOICES,
                   asset_classes=asset_classes.all_asset_classes(), asset_pref=asset_pref,
                   scanned=_scanned_for(user), trade_filter=atf)


@router.post("/app/asset")
def app_set_asset(request: Request, asset: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    cls = asset_classes.get_asset_class(asset)
    db.set_asset_pref(user["user_id"], cls.key)
    return _redirect("/app")


@router.post("/app/scan")
async def app_scan(request: Request, asset: str = Form(None)):
    """Wählt die Anlageklasse (persistiert) UND fordert live Signale dafür an — in einem
    Schritt (kein JS nötig). `asset='all'` scannt ALLE Klassen mit ihrem jeweiligen Profil
    und mischt die Treffer nach Stärke. Ergebnis (inkl. 7-Tage-Mini-Chart) wird kurz gecacht."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")

    # Auswahl übernehmen + merken (gültig: 'all' oder eine registrierte Klasse)
    if asset:
        pref = "all" if asset == "all" else asset_classes.get_asset_class(asset).key
        db.set_asset_pref(user["user_id"], pref)
    else:
        pref = user.get("asset_pref") or asset_classes.DEFAULT_ASSET

    classes = asset_classes.all_asset_classes() if pref == "all" \
        else [asset_classes.get_asset_class(pref)]

    sl_tp_mode = user.get("sl_tp_mode") or "normal"

    def _work():
        merged = []
        for cls in classes:
            tickers = cls.get_tickers(user)
            if not tickers:
                continue
            for s in analyzer.analyze_universe(tickers, profile=cls.profile):
                analyzer.apply_sl_tp_mode(s, sl_tp_mode)     # gewählter SL/TP-Modus wirkt auf alle Strategien
                s["asset_label"] = cls.label
                merged.append(s)
        merged.sort(key=lambda s: s.get("strength", 0) or 0, reverse=True)
        top = merged[: max(1, int(user.get("top_n_signals") or 5))]
        spark = analyzer.price_history_batch([s["ticker"] for s in top], days=7)
        for s in top:
            s["spark_closes"] = spark.get(s["ticker"], {}).get("closes", [])
        return top

    top = await run_in_threadpool(_work)
    _scan_cache[user["user_id"]] = {"at": time.time(), "asset": pref, "signals": top}
    n = len(top)
    return _redirect(f"/app?msg={n}+Signal(e)+gefunden." if n else "/app?msg=Aktuell+keine+Signale.")


def _wants_json(request: Request) -> bool:
    """True, wenn die Anfrage per fetch/AJAX kommt (für Inline-Status statt Redirect)."""
    return request.headers.get("x-requested-with", "").lower() == "fetch"


@router.post("/app/scan/accept")
async def app_scan_accept(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    entry = _scan_cache.get(user["user_id"]) or {}
    sig = next((s for s in entry.get("signals", []) if s["ticker"] == ticker), None)
    if not sig:
        msg = "Signal abgelaufen – bitte neu anfordern."
        if _wants_json(request):
            return JSONResponse({"ok": False, "status": "expired", "msg": msg})
        return _redirect("/app?msg=Signal+abgelaufen+%E2%80%93+bitte+neu+anfordern.")
    broker_status = "broker_pending" if _broker_will_execute(user) else "active"
    res = await run_in_threadpool(trade_svc.accept_signal, user["user_id"], {**sig, "_accept_status": broker_status})
    if res["ok"]:
        # accept_signal aktiviert standardmäßig active; bei Broker-Ausführung korrigieren wir den Status über accept_trade unten nicht.
        # Für On-Demand-Signale nutzen wir daher denselben Pfad wie /app/accept: ggf. Status nachträglich vorm Orderversand setzen.
        if broker_status == "broker_pending":
            db.mark_broker_pending(user["user_id"], ticker, order_id=None, broker_status="not_submitted")
        await run_in_threadpool(_attach_demo_option, user, ticker)
        trade = db.get_trade(user["user_id"], ticker)
        broker_res = await run_in_threadpool(_execute_broker_order_for_web, user, trade) if trade else {"status": "unavailable"}
        msg = broker_res.get("msg") or f"{ticker} gestartet."
        status = broker_res.get("status") or "accepted"
    else:
        msg, status = f"{ticker} heute bereits gehandelt.", "unavailable"
    if _wants_json(request):
        return JSONResponse({"ok": res["ok"], "status": status, "msg": msg})
    return _redirect(f"/app?msg={msg}")


@router.post("/app/accept")
async def app_accept(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    broker_status = "broker_pending" if _broker_will_execute(user) else "active"
    res = await run_in_threadpool(trade_svc.accept_trade, user["user_id"], ticker, status=broker_status)
    if res["ok"]:
        await run_in_threadpool(_attach_demo_option, user, ticker)
        broker_res = await run_in_threadpool(_execute_broker_order_for_web, user, res["trade"])
        msg = broker_res.get("msg") or f"{ticker} gestartet."
        status = broker_res.get("status") or ("broker_pending" if broker_status == "broker_pending" else "accepted")
    elif res.get("reason") == "expired":
        msg, status = "Zeitfenster abgelaufen.", "expired"
    else:
        msg, status = "Trade nicht mehr verfügbar.", "unavailable"
    if _wants_json(request):
        return JSONResponse({"ok": res["ok"], "status": status, "msg": msg})
    return _redirect(f"/app?msg={msg}")


@router.post("/app/reject")
def app_reject(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    ok = trade_svc.reject_trade(user["user_id"], ticker)
    return _redirect(f"/app?msg={ticker + ' abgelehnt.' if ok else 'Nicht möglich.'}")


@router.post("/app/lev")
def app_lev(request: Request, ticker: str = Form(...), leverage: float = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    updated = trade_svc.set_pending_leverage(user["user_id"], ticker, leverage)
    return _redirect(f"/app?msg={'Hebel gesetzt.' if updated else 'Hebel nicht mehr änderbar.'}")


@router.post("/app/sell")
async def app_sell(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    res = await run_in_threadpool(trade_svc.sell_trade, user["user_id"], ticker)
    if res["ok"]:
        msg = f"{ticker} verkauft: {res['pnl_eur']:+.2f}€"
    else:
        msg = "Trade nicht mehr aktiv."
    return _redirect(f"/app?msg={msg}")


# ── Einstellungen ─────────────────────────────────────────────────────────────

@router.get("/app/settings", response_class=HTMLResponse)
def app_settings(request: Request, msg: str = ""):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    toggles = [
        ("set_auto", "Auto-Accept (Signale automatisch starten)", user["auto_accept"]),
        ("set_uni", "Voll-Universum (große Aktienliste)", user["auto_universe"]),
        ("set_llm", "KI-Ranking (Claude Haiku)", user["llm_rank"]),
        ("set_eod", "Tagesende-Schließung", user["eod_close"]),
        ("set_window", "15-Min-Annahmefenster (aus = dauerhaft annehmbar)", user.get("signal_window")),
    ]
    if _alpaca_ready(user):
        toggles.append(("set_broker", "Echte Broker-Order (Alpaca)", user["broker_exec"]))
    return _render("settings.html", request, user, active="settings", msg=msg,
                   universes=config.REGION_LABELS, sl_tp_modes=list(config.SL_TP_MODES),
                   leverages=config.LEVERAGE_CHOICES,
                   strategies=strategies.all_strategies(), toggles=toggles)


@router.post("/app/settings/set")
def app_settings_set(request: Request, action: str = Form(...), value: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if action in settings_svc.SETTING_ACTIONS:
        settings_svc.apply_setting(user["user_id"], action, value, alpaca_ready=_alpaca_ready(user))
    return _redirect("/app/settings?msg=Gespeichert.")


@router.post("/app/settings/notify")
def app_settings_notify(request: Request, value: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    db.set_notify_channel(user["user_id"], value)
    return _redirect("/app/settings?msg=Benachrichtigungen+aktualisiert.")


@router.post("/app/reset")
async def app_reset(request: Request):
    """Setzt den Demo-Trade-Modus des Nutzers zurück: schließt zuerst (falls Broker-Ausführung
    aktiv) alle offenen Alpaca-Positionen und löscht dann alle Trades, Ticks & Mitteilungen.
    Profil, Einstellungen und Alpaca-Verbindung bleiben erhalten."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")

    closed, failed = [], []
    if user.get("broker_exec") and _alpaca_ready(user):
        client = _alpaca_client(user)
        if client is not None:
            for p in await run_in_threadpool(broker.list_positions, client):
                res = await run_in_threadpool(broker.close_position, p["symbol"], client)
                (closed if res.get("closed") else failed).append(p["symbol"])

    n = await run_in_threadpool(db.reset_user_trades, user["user_id"])

    parts = [f"Reset: {n} Einträge gelöscht"]
    if closed:
        parts.append(f"{len(closed)} Alpaca-Position(en) geschlossen ({', '.join(closed)})")
    if failed:
        parts.append(f"⚠️ {len(failed)} nicht schließbar ({', '.join(failed)})")
    return _redirect("/app/settings?msg=" + "; ".join(parts) + ".")


# ── Watchlist ─────────────────────────────────────────────────────────────────

@router.get("/app/watchlist", response_class=HTMLResponse)
def app_watchlist(request: Request, msg: str = "", query: str = "", suggestions: str = ""):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    sugg = [s for s in suggestions.split(",") if s]
    return _render("watchlist.html", request, user, active="watchlist", msg=msg,
                   watchlist=user.get("watchlist") or [], query=query, suggestions=sugg)


@router.post("/app/watchlist/add")
async def app_watchlist_add(request: Request, symbol: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    client = _alpaca_client(user) if _alpaca_ready(user) else None
    res = await run_in_threadpool(watchlist_svc.add_to_watchlist, user["user_id"], symbol, client)
    if res["status"] == "added":
        return _redirect(f"/app/watchlist?msg={res['info']['symbol']}+hinzugef%C3%BCgt.")
    sugg = ",".join(res["suggestions"])
    return _redirect(f"/app/watchlist?query={res['symbol']}&suggestions={sugg}")


@router.post("/app/watchlist/remove")
def app_watchlist_remove(request: Request, symbol: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    watchlist_svc.remove_from_watchlist(user["user_id"], symbol)
    return _redirect(f"/app/watchlist?msg={symbol}+entfernt.")


# ── Mitteilungen (In-App + SSE-Live-Feed) ────────────────────────────────────

@router.get("/app/notifications", response_class=HTMLResponse)
def app_notifications(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    items = db.get_notifications(user["user_id"], limit=50)
    db.mark_notifications_read(user["user_id"])
    return _render("notifications.html", request, user, active="notifications", items=items)


@router.get("/app/stream")
async def app_stream(request: Request):
    """Server-Sent-Events: schickt neu hinzukommende Mitteilungen live an den Browser."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    uid = user["user_id"]

    async def gen():
        last = db.get_notifications(uid, limit=1)
        last_id = last[0]["id"] if last else 0
        while not await request.is_disconnected():
            items = [n for n in db.get_notifications(uid, limit=20) if n["id"] > last_id]
            for n in reversed(items):                       # älteste zuerst senden
                last_id = max(last_id, n["id"])
                yield f"data: {json.dumps(n, ensure_ascii=False)}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Reports (Backtest-Sweeps: Strategie / SL-TP-Modus / Hebel) ───────────────

def _load_report(name: str) -> dict | None:
    from stockbot.paths import REPORTS_DIR
    path = REPORTS_DIR / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/app/reports", response_class=HTMLResponse)
def app_reports(request: Request,
                strat: list[str] = Query(default=[]),
                lev: list[str] = Query(default=[]),
                mode: list[str] = Query(default=[])):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    strategies = _load_report("strategies")
    matrix = _load_report("matrix")

    strat_options = (matrix or {}).get("strategies") or \
        [{"key": r["key"], "label": r["label"]} for r in (strategies or {}).get("rows", [])]
    lev_options = [str(x) for x in (matrix or {}).get("leverages", [])]
    mode_options = (matrix or {}).get("modes", [])

    # Auswahl gegen gültige Werte filtern; leere Auswahl = alle (auch in der UI vorausgewählt).
    sel_strat = [s for s in strat if s in {o["key"] for o in strat_options}] or [o["key"] for o in strat_options]
    sel_lev = [l for l in lev if l in lev_options] or lev_options
    sel_mode = [m for m in mode if m in mode_options] or mode_options

    rows = []
    for r in (matrix or {}).get("rows", []):
        if r["key"] in sel_strat and str(r["leverage"]) in sel_lev and r["mode"] in sel_mode:
            rows.append(r)
    rows.sort(key=lambda r: (r.get("return_pct") if r.get("return_pct") is not None else -1e9), reverse=True)

    return _render("reports.html", request, user, active="reports",
                   strategies=strategies, matrix=matrix, rows=rows,
                   strat_options=strat_options, lev_options=lev_options, mode_options=mode_options,
                   sel_strat=sel_strat, sel_lev=sel_lev, sel_mode=sel_mode)


@router.get("/app/reports/equity")
def app_reports_equity(request: Request, key: str = "", lev: str = "", mode: str = ""):
    """Depot-Equity-Kurve einer Matrix-Kombination (für den Klick-auf-Zeile-Chart)."""
    user = auth.current_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    eq = _load_report("equity") or {}
    curve = (eq.get("curves") or {}).get(f"{key}|{lev}|{mode}")
    if curve is None:
        return JSONResponse({"error": "not_found", "points": []}, status_code=404)
    return JSONResponse({"points": curve, "benchmark": eq.get("benchmark") or [],
                         "start_capital": eq.get("start_capital"),
                         "start": eq.get("start"), "end": eq.get("end")})


# ── Dashboard-Verknüpfung ─────────────────────────────────────────────────────

@router.get("/app/dashboard")
def app_dashboard(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    token = db.get_or_create_dashboard_token(user["user_id"])
    return _redirect(f"/dashboard/{token}")
