"""
Interaktive Website (Phase 1–2): Login, Signal-Feed (Annehmen/Ablehnen/Hebel), Einstellungen,
Watchlist, In-App-Mitteilungen (mit SSE-Live-Feed). Läuft parallel zum Telegram-Bot — beide
nutzen dieselbe DB und dieselbe Service-Schicht (stockbot/services/*).

Wird als Router in stockbot/web/dashboard.py eingehängt (ein Server für Dashboard + App).
"""

import os
import json
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.market import strategies
from stockbot.broker import client as broker
from stockbot import config
from stockbot.services import trades as trade_svc
from stockbot.services import settings as settings_svc
from stockbot.services import watchlist as watchlist_svc
from stockbot.web import auth
from stockbot.web.dashboard import build_dashboard_data

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


# ── Alpaca-Helfer (leichtgewichtig, ohne Telegram-Abhängigkeit) ──────────────

def _alpaca_ready(user: dict) -> bool:
    return bool(user and user.get("broker_platform") == "alpaca") or config.ALPACA_ENABLED


def _alpaca_client(user: dict):
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return broker.make_client(creds[0], creds[1], paper=config.ALPACA_PAPER)
    return broker._get_client()


def _render(name: str, request: Request, user: dict, active: str = "", msg: str = "", **ctx):
    return templates.TemplateResponse(request, name, {
        "user": user, "active": active, "msg": msg,
        "unread": db.unread_count(user["user_id"]) if user else 0, **ctx,
    })


def _redirect(path: str):
    return RedirectResponse(path, status_code=303)


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
def auth_token(token: str = ""):
    u = db.get_user_by_token(token)
    if not u:
        return _redirect("/login?msg=Ung%C3%BCltiger+Token")
    return auth.login_response(_redirect("/app"), u["user_id"])


@router.get("/auth/telegram")
def auth_telegram(request: Request):
    uid = auth.verify_telegram_login(dict(request.query_params))
    if not uid or not db.get_user(uid):
        return _redirect("/login?msg=Telegram-Login+fehlgeschlagen")
    return auth.login_response(_redirect("/app"), uid)


@router.post("/logout")
def logout(request: Request):
    return auth.logout_response(request, _redirect("/login"))


# ── App-Startseite: Signale + aktive Trades ──────────────────────────────────

@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, msg: str = ""):
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
    return _render("app.html", request, user, active="home", msg=msg,
                   pending=pending, active_trades=active_trades, leverages=config.LEVERAGE_CHOICES)


@router.post("/app/accept")
def app_accept(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    res = trade_svc.accept_trade(user["user_id"], ticker)
    msg = (f"{ticker} gestartet." if res["ok"]
           else ("Zeitfenster abgelaufen." if res.get("reason") == "expired"
                 else "Trade nicht mehr verfügbar."))
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


# ── Dashboard-Verknüpfung ─────────────────────────────────────────────────────

@router.get("/app/dashboard")
def app_dashboard(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    token = db.get_or_create_dashboard_token(user["user_id"])
    return _redirect(f"/dashboard/{token}")
