"""
Interaktive Website (Phase 1–2): Login, Signal-Feed (Annehmen/Ablehnen/Hebel), Einstellungen,
Watchlist, Reports/Backtest. Läuft parallel zum Telegram-Bot — beide nutzen dieselbe DB und
dieselbe Service-Schicht (stockbot/services/*).

Wird als Router in stockbot/web/dashboard.py eingehängt (ein Server für Dashboard + App).
"""

import os
import json
import time
import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.core import trade_lifecycle
from stockbot.core import logfilter
from stockbot.core.domain import Mode, OrderStatus, RiskProfile, Signal, SignalStatus, TradeIntent
from stockbot.core.evaluator import trade_pnl
from stockbot.backtest import engine as backtest_engine
from stockbot.market import strategies
from stockbot.market import asset_classes
from stockbot.market import analyzer
from stockbot.broker import client as broker
from stockbot.broker import sizing
from stockbot.broker import reconcile as reconcile_mod
from stockbot import config
from stockbot.services import trades as trade_svc
from stockbot.services import settings as settings_svc
from stockbot.services import watchlist as watchlist_svc
from stockbot.web import auth
from stockbot.web import feed_status as feed_status_mod
from stockbot.optimize import lab as lab_mod
from stockbot.execution.oms import OrderManagementSystem
from stockbot.execution import risk_context
from stockbot.core.kill_switch import KillSwitchService

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _load_oms_signal(signal_id: int) -> Signal | None:
    """Bridge vom bisherigen Trade-JSON zum Phase-4-Signalobjekt."""
    trade = db.get_trade_by_id(signal_id)
    if trade is None:
        return None
    sig = trade.get("signal") or {}
    return Signal(
        id=signal_id, strategy_version_id=0, ticker=trade["ticker"],
        direction=trade["direction"], mode=Mode.PAPER, status=SignalStatus.ACCEPTED,
        expires_at=sig.get("expires_at"),
    )


kill_switch_service = KillSwitchService(persistence=db, load_on_init=False)

_oms = OrderManagementSystem(
    signal_loader=_load_oms_signal, context_loader=risk_context.signal_context,
    broker_adapter=broker, persistence=db, audit_sink=db.append_audit_event,
    kill_switch_checker=kill_switch_service.is_new_position_allowed,
)


async def _csrf_protect(request: Request):
    """CSRF-Schutz: state-ändernde Requests müssen vom eigenen Origin kommen."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.is_same_origin(request):
        raise HTTPException(status_code=403, detail="Ungültiger Origin (CSRF-Schutz).")


router = APIRouter(dependencies=[Depends(_csrf_protect)])

# Import erst jetzt, damit dashboard.py beim Einhängen des Routers bereits auf `router` zugreifen kann.
from stockbot.web.dashboard import build_dashboard_data, broker_status_label, trade_status_label

# ── Alpaca-Helfer (leichtgewichtig, ohne Telegram-Abhängigkeit) ──────────────

def _alpaca_ready(user: dict) -> bool:
    """Ob dieser Nutzer eine EIGENE Alpaca-Anbindung hat.

    Bewusst NICHT `or config.ALPACA_ENABLED`: die globalen Keys sind der Betreiber-Datenzugang
    für den Signal-Scan, kein Handelskonto für fremde Nutzer. Andernfalls genügte das Hinterlegen
    globaler Marktdaten-Keys, damit jeder Nutzer „echte Broker-Order" aktivieren und über das
    Betreiberkonto handeln kann.
    """
    return bool(user and user.get("broker_platform") == "alpaca")


def _alpaca_client(user: dict):
    """Client aus den EIGENEN, verschlüsselt gespeicherten Keys des Nutzers — sonst None.

    Kein Rückfall auf `broker._get_client()` (globale Keys), Begründung siehe `_alpaca_ready`.
    """
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return broker.make_client(creds[0], creds[1], paper=config.ALPACA_PAPER)
    return None


def _alpaca_keys(user: dict) -> tuple[str | None, str | None]:
    """Roh-Keys für **Options-Marktdaten**: eigene des Nutzers, sonst die globalen.

    Der globale Rückfall ist hier korrekt und bleibt: Marktdaten sind nutzerunabhängig und der
    globale Key ist genau dafür da (Betreiber-Datenzugang). Für die **Order-Ausführung** gilt das
    Gegenteil — siehe `_alpaca_client`, das bewusst NICHT zurückfällt.
    """
    if user and user.get("broker_platform") == "alpaca":
        creds = db.get_decrypted_credentials(user["user_id"])
        if creds:
            return creds[0], creds[1]
    return config.ALPACA_API_KEY, config.ALPACA_API_SECRET


def _strategy_catalog(query: str = "") -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    for s in strategies.all_strategies():
        cfg = db.get_strategy_config(s.key) or {"key": s.key, "label": s.label, "description": s.description, "params": {}, "enabled": True}
        blob = " ".join([
            s.key, s.label, s.description,
            cfg.get("label", ""), cfg.get("description", ""), json.dumps(cfg.get("params") or {}, sort_keys=True),
        ]).lower()
        if q and q not in blob:
            continue
        out.append({
            "key": s.key,
            "label": cfg.get("label") or s.label,
            "description": cfg.get("description") or s.description,
            "params": cfg.get("params") or {},
            "enabled": cfg.get("enabled", True),
            "module_label": s.label,
        })
    return out


def _parse_csv_items(text: str) -> list[str]:
    return [x.strip().upper() for x in (text or "").replace("\n", ",").split(",") if x.strip()]


def _pretty_json(value: dict | None) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return "{}"



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


def _planned_order_cost(plan: dict, entry: float) -> float:
    """Geschätzte Broker-Kosten vor Absenden der Order."""
    if not plan or plan.get("kind") == "none":
        return 0.0
    if plan.get("kind") == "option":
        return float(plan.get("premium") or 0.0) * 100.0 * float(plan.get("qty") or 0.0)
    if plan.get("notional") is not None:
        return float(plan.get("notional") or 0.0)
    if plan.get("qty") is not None:
        return float(plan.get("qty") or 0.0) * float(entry or 0.0)
    return 0.0


def _ensure_buying_power(user: dict, ticker: str, client, plan: dict, entry: float) -> dict | None:
    """Blockt Orders, die Alpaca wegen fehlender Buying Power sicher ablehnen würde."""
    needed = _planned_order_cost(plan, entry)
    if needed <= 0:
        return None
    acct = broker.account_summary(client)
    if not acct.get("ok"):
        return None  # Account-Check nicht verfügbar: Alpaca darf final entscheiden.
    buying_power = float(acct.get("buying_power") or 0.0)
    if buying_power + 1e-9 >= needed:
        return None
    db.mark_broker_failed(user["user_id"], ticker, broker_status="insufficient_buying_power")
    return {
        "ok": False,
        "status": "broker_failed",
        "msg": (f"Alpaca Buying Power reicht nicht: verfügbar ${buying_power:.2f}, "
                f"benötigt ca. ${needed:.2f}. Keine Order gesendet."),
    }


def _execute_broker_order_for_web(user: dict, trade: dict) -> dict:
    """Synchrone Broker-Ausführung für die Web-App.

    Hält `broker_pending`, bis Alpaca wirklich `filled` meldet; bei nicht ausgeführter
    Order bleibt der Trade aus der aktiven Demo-Trade-Liste heraus.
    """
    if not trade or not _broker_will_execute(user):
        return {"ok": True, "status": trade.get("status", "active") if trade else "unavailable"}

    client = _alpaca_client(user)
    if client is None:
        db.mark_broker_failed(user["user_id"], trade["ticker"], broker_status="not_submitted")
        return {"ok": False, "status": "broker_failed", "msg": "Alpaca nicht verfügbar."}

    ticker = trade["ticker"]
    leverage = float((trade.get("signal") or {}).get("leverage") or 1.0)
    if leverage > config.MAX_LEVERAGE + 1e-9:
        db.mark_broker_failed(user["user_id"], ticker, broker_status="leverage_blocked")
        return {"ok": False, "status": "broker_failed",
                "msg": f"Order abgelehnt: Hebel {leverage:g}× über erlaubtem Maximum "
                       f"{config.MAX_LEVERAGE:g}× (TSAFE-002)."}
    entry = trade.get("entry") or (trade.get("signal") or {}).get("price")
    if entry is None:
        db.mark_broker_failed(user["user_id"], ticker, broker_status="not_submitted")
        return {"ok": False, "status": "broker_failed", "msg": "Kein Einstiegskurs verfügbar."}

    extended = bool(config.EXTENDED_HOURS and broker.market_open(client) is False)
    plan = sizing.plan_order(float(entry), float(user["trade_size_eur"]), leverage,
                             option_selector=None, extended=extended,
                             roundup_factor=config.SHARE_ROUNDUP_FACTOR)

    if plan["kind"] == "none":
        db.mark_broker_failed(user["user_id"], ticker, broker_status="not_submitted")
        return {"ok": False, "status": "broker_failed", "msg": "Budget reicht nicht für eine Broker-Order."}

    insufficient = _ensure_buying_power(user, ticker, client, plan, float(entry))
    if insufficient:
        return insufficient

    # Aktien-Fallback bei Hebel>1 → effektiver Hebel = 1 (P&L = echte Position, nicht überzeichnet).
    if plan["kind"] == "shares" and leverage > 1.0:
        db.merge_active_trade_signal(user["user_id"], ticker, {"effective_leverage": 1.0})

    if plan["kind"] == "shares" and not plan.get("qty"):
        if extended:
            # Bruchteil außerhalb regulärer Zeit → vormerken; der Bot-Monitor sendet beim nächsten Open.
            db.mark_broker_pending(user["user_id"], ticker, order_id=None, broker_status="queued_regular")
            return {"ok": True, "status": "broker_pending",
                    "msg": ("Order vorgemerkt — Bruchteile gehen nur in der regulären US-Sitzung; "
                            "wird beim nächsten Börsenstart automatisch gesendet.")}
    intent = TradeIntent(
        user_id=user["user_id"], signal_id=int(trade["id"]), requested_action="accept",
        accepted_exit_policy="strategy-default", source_channel="web",
        created_at=datetime.now(timezone.utc).isoformat(),
        idempotency_key=f"web:{user['user_id']}:{trade['id']}:accept",
    )
    oms_result = _oms.submit_intent(
        intent, price=float(entry), trade_size=float(user["trade_size_eur"]), leverage=leverage,
        risk_context={
            "is_live_account": broker._is_live_order(client),
            "is_option": plan["kind"] == "option",
            "extended": extended,
            "roundup_factor": config.SHARE_ROUNDUP_FACTOR,
            "entry_price": float(entry),
            "candidate_notional": float(user["trade_size_eur"]),
            **risk_context.account_context(client, user["user_id"]),
            **risk_context.quote_context(ticker),
        },
        broker_client=client,
    )
    if not oms_result.ok or oms_result.order is None:
        broker_status = (oms_result.code if oms_result.code not in {"broker_rejected", "order_plan_rejected"}
                         else "submit_failed") or "submit_failed"
        db.mark_broker_failed(user["user_id"], ticker, broker_status=broker_status)
        return {"ok": False, "status": "broker_failed",
                "msg": f"Broker-Order nicht angenommen: {oms_result.reason}"}

    order_id = oms_result.order.broker_order_id or ""
    db.mark_broker_pending(user["user_id"], ticker, order_id=order_id, broker_status="accepted")
    fill = broker.get_order_status(order_id, client)
    status = fill.get("status", "unbekannt")
    if oms_result.order.status == OrderStatus.FILLED:
        status = "filled"
    elif oms_result.order.status == OrderStatus.PARTIALLY_FILLED:
        status = "partially_filled"
    if status == "filled":
        db.mark_broker_filled(user["user_id"], ticker, broker_status=status,
                              filled_qty=fill.get("filled_qty", oms_result.order.qty),
                              filled_avg_price=fill.get("filled_avg_price", entry))
        return {"ok": True, "status": "filled", "msg": f"{ticker} gekauft."}
    if status in ("rejected", "canceled", "expired"):
        db.mark_broker_failed(user["user_id"], ticker, broker_status=status)
        return {"ok": False, "status": "broker_failed", "msg": f"Broker-Order nicht ausgeführt ({status})."}
    db.mark_broker_pending(user["user_id"], ticker, order_id=order_id, broker_status=status)
    return {"ok": True, "status": "broker_pending", "msg": f"Broker-Order angenommen, aber noch nicht ausgeführt ({status})."}


def _execute_broker_close_for_web(user: dict, trade: dict) -> dict:
    client = _alpaca_client(user)
    if client is None:
        db.mark_broker_close_failed(user["user_id"], trade["ticker"], broker_status="not_submitted")
        return {"ok": False, "status": "broker_failed_close", "msg": "Alpaca nicht verfügbar."}

    ticker = trade["ticker"]
    symbol = reconcile_mod.bot_symbol(trade)
    res = broker.close_position(symbol, client=client)
    mode = "PAPER" if config.ALPACA_PAPER else "LIVE"
    if not res.get("ok"):
        db.mark_broker_close_failed(user["user_id"], ticker, broker_status=res.get("detail") or "submit_failed")
        return {"ok": False, "status": "broker_failed_close", "msg": f"Alpaca-{mode}: {res.get('detail')}"}

    order_id = res.get("id")
    if not order_id:
        db.mark_broker_close_failed(user["user_id"], ticker, broker_status="missing_order_id")
        return {"ok": False, "status": "broker_failed_close", "msg": f"Alpaca-{mode}: Verkauf ohne Order-ID."}

    fill = broker.get_order_status(order_id, client)
    status = fill.get("status", "unbekannt")
    if status == "filled":
        q = fill.get("filled_qty", 0.0)
        px = float(fill.get("filled_avg_price") or fill.get("avg_fill_price") or trade.get("exit") or trade.get("entry") or 0.0)
        pnl_pct, pnl_eur = trade_pnl(trade, px, user["trade_size_eur"])
        db.close_all(user["user_id"], [{"ticker": ticker, "exit": px, "pnl_eur": pnl_eur, "pnl_pct": pnl_pct}])
        return {"ok": True, "status": "closed", "msg": f"{ticker} verkauft.", "filled_qty": q, "filled_avg_price": px}

    if status in ("rejected", "canceled", "expired", "done_for_day"):
        db.mark_broker_close_failed(user["user_id"], ticker, broker_status=status)
        return {"ok": False, "status": "broker_failed_close", "msg": f"Broker-Verkauf nicht ausgeführt ({status})."}

    db.mark_broker_closing(user["user_id"], ticker, order_id=order_id, broker_status=status)
    return {"ok": True, "status": "broker_closing", "msg": f"Broker-Verkauf angenommen ({status})."}


def _render(name: str, request: Request, user: dict, active: str = "", msg: str = "", **ctx):
    if user and user.get("broker_exec"):
        trade_mode = "paper" if config.ALPACA_PAPER else "live"
    else:
        trade_mode = "demo"
    return templates.TemplateResponse(request, name, {
        "user": user, "active": active, "msg": msg,
        "is_admin": _is_admin(user), "trade_mode": trade_mode, **ctx,
    })


def _redirect(path: str):
    return RedirectResponse(path, status_code=303)


def _q(text: str) -> str:
    """URL-quotet einen Flash-Text für den ?msg=-Redirect."""
    from urllib.parse import quote
    return quote(text or "", safe="")


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
        key = card.get("strategy") or strategies.DEFAULT_STRATEGY
        card["strategy_label"] = strategies.get(key).label
        card["raw_score"] = card.get("raw_score", card.get("strength"))
        card["spark"] = _sparkline(s.get("spark_closes") or [])
        out.append(card)
    return out


def _feed_status_for(user: dict, scanned: list) -> feed_status_mod.FeedStatus:
    """Datenaktualität der auf /app gezeigten Kurse (Stylekonzept §32.3).

    Einzige im Render-Pfad verfügbare, belastbare Kurs-Zeitstempel-Quelle ist der
    Scan-Cache (`_scan_cache[...]["at"]`) — er stempelt genau den Moment, in dem die auf
    den Signalkarten gezeigten Preise geholt wurden. Für DB-Signale (`get_pending_trades`)
    und aktive Trades existiert hier KEIN Kurs-Zeitstempel: der Tages-Scan bzw. der
    60s-Monitor speichert ihn nicht mit dem gerenderten Preis. In dem Fall wird das Alter
    bewusst NICHT geraten, sondern explizit als „unbekannt" ausgewiesen (blockiert nicht).

    Es wird keine neue Quote geholt — der Status wird rein aus vorhandenen Daten abgeleitet.
    """
    entry = _scan_cache.get(user["user_id"])
    if not scanned or not entry:
        return feed_status_mod.unknown()
    # `scanned` ist nur nicht-leer, wenn `_scanned_for` den Cache-Eintrag als gültig
    # akzeptiert hat — sein `at` gehört also zu den gerenderten Preisen.
    profile = RiskProfile(user_id=int(user["user_id"]))
    return feed_status_mod.evaluate(
        time.time() - entry["at"], max_quote_age_seconds=profile.max_quote_age_seconds)


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
            "ticker": t["ticker"],
            "direction": t.get("direction") or sig.get("direction", "long"),
            "price": sig.get("price") or t.get("entry") or 0.0,
            "strength": sig.get("strength"),
            "raw_score": sig.get("raw_score", sig.get("strength")),
            "strategy_label": strategies.get(sig.get("strategy") or strategies.DEFAULT_STRATEGY).label,
            "leverage": sig.get("leverage", 1.0) or 1.0,
            "stop_loss": sig.get("stop_loss"), "take_profit": sig.get("take_profit"),
        })
    active_trades = build_dashboard_data(user)["active_trades"]
    broker_pending = []
    for t in db.get_broker_pending_trades(user["user_id"]):
        sig = t.get("signal", {}) or {}
        broker_status = t.get("broker_status") or "accepted"
        broker_pending.append({
            "ticker": t["ticker"],
            "direction": t.get("direction") or sig.get("direction", "long"),
            "entry": t.get("entry") or sig.get("price") or 0.0,
            "leverage": sig.get("leverage", 1.0) or 1.0,
            "status_text": trade_status_label("broker_pending", broker_status),
            "broker_text": broker_status_label(broker_status),
        })
    broker_closing = []
    for t in db.get_broker_closing_trades(user["user_id"]):
        sig = t.get("signal", {}) or {}
        broker_status = t.get("broker_status") or "requested"
        broker_closing.append({
            "ticker": t["ticker"],
            "direction": t.get("direction") or sig.get("direction", "long"),
            "entry": t.get("entry") or sig.get("price") or 0.0,
            "current": t.get("exit") or t.get("entry") or sig.get("price") or 0.0,
            "leverage": sig.get("leverage", 1.0) or 1.0,
            "status_text": trade_status_label("broker_closing", broker_status),
            "broker_status": broker_status,
            "broker_text": broker_status_label(broker_status),
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
    scanned = _scanned_for(user)
    # §32.3: Datenaktualität + Abrufzeit. Die Abrufzeit ist eine System-/Audit-Zeit und wird
    # deshalb als UTC gerendert und auch so beschriftet (keine Umrechnung ohne Grundlage).
    entry = _scan_cache.get(user["user_id"])
    feed_as_of_utc = (
        datetime.fromtimestamp(entry["at"], timezone.utc).strftime("%H:%M:%S")
        if scanned and entry else None)
    return _render("app.html", request, user, active="home", msg=msg,
                   pending=pending, broker_pending=broker_pending,
                   broker_closing=broker_closing, active_trades=active_trades,
                   asset_classes=asset_classes.all_asset_classes(), asset_pref=asset_pref,
                   scanned=scanned, trade_filter=atf,
                   feed_status=_feed_status_for(user, scanned),
                   feed_as_of_utc=feed_as_of_utc)


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
    und rankt die Treffer innerhalb der Standard-Strategie. Ergebnis (inkl. 7-Tage-Mini-Chart)
    wird kurz gecacht."""
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
                s.setdefault("strategy", strategies.DEFAULT_STRATEGY)
                s.setdefault("raw_score", s.get("strength"))
                s["asset_label"] = cls.label
                merged.append(s)
        # Alle Anlageklassen verwenden hier dieselbe Standard-Strategie und damit dieselbe
        # interne Rohscore-Definition; dies ist ausdrücklich kein Cross-Strategie-Vergleich.
        merged.sort(key=lambda s: s.get("raw_score", s.get("strength", 0)) or 0, reverse=True)
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
    elif res.get("reason") == "entry_cutoff":
        msg, status = "Kein neuer Einstieg mehr — zu kurz vor Handelsschluss.", "entry_cutoff"
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
    elif res.get("reason") == "entry_cutoff":
        msg, status = "Kein neuer Einstieg mehr — zu kurz vor Handelsschluss.", "entry_cutoff"
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
    trade = db.get_trade(user["user_id"], ticker)
    if not trade or trade.get("status") != "active":
        return _redirect("/app?msg=Trade+nicht+mehr+aktiv.")

    if _broker_will_execute(user):
        res = await run_in_threadpool(trade_svc.sell_trade, user["user_id"], ticker, broker_close=True)
        if not res["ok"]:
            return _redirect("/app?msg=Trade+nicht+mehr+aktiv.")
        broker_res = await run_in_threadpool(_execute_broker_close_for_web, user, res["trade"])
        if broker_res.get("status") == "closed":
            msg = f"{ticker} verkauft: {broker_res.get('filled_avg_price', 0.0):.2f}$"
        else:
            msg = broker_res.get("msg") or "Verkauf läuft weiter bei Alpaca."
        return _redirect(f"/app?msg={msg}")

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
        ("set_eod", "Tagesende-Schließung", user["eod_close"]),
    ]
    if _alpaca_ready(user):
        toggles.append(("set_broker", "Echte Broker-Order (Alpaca)", user["broker_exec"]))
    risk_params = [
        ("Handelsmodus", "LIVE · ECHTES GELD" if config.LIVE_TRADING_ENABLED else "PAPER (kein echtes Geld)"),
        ("Maximaler Hebel", f"{config.MAX_LEVERAGE:g}×"),
        ("Optionen", "erlaubt" if config.ALLOW_OPTIONS else "gesperrt"),
        ("Leerverkäufe (Shorts)", "erlaubt" if config.ALLOW_SHORTS else "gesperrt"),
        ("Margin", "erlaubt" if config.ALLOW_MARGIN else "gesperrt"),
    ]
    return _render("settings.html", request, user, active="settings", msg=msg,
                   universes=config.REGION_LABELS, sl_tp_modes=list(config.SL_TP_MODES),
                   has_alpaca=db.has_alpaca_credentials(user["user_id"]),
                   strategies=strategies.production_strategies(), toggles=toggles,
                   risk_params=risk_params, is_admin=_is_admin(user),
                   kill_switch=(kill_switch_service.global_status if _is_admin(user)
                                else kill_switch_service.user_status(user["user_id"])))


@router.post("/app/settings/killswitch")
def app_settings_killswitch(request: Request, enabled: str = Form(...), reason: str = Form("")):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    active = enabled == "1"
    actor = f"web:{user['user_id']}"
    if _is_admin(user):
        if active:
            kill_switch_service.activate_global(
                reason=reason.strip() or "Manuell durch Admin aktiviert", activated_by=actor)
        else:
            kill_switch_service.deactivate_global(deactivated_by=actor)
    elif active:
        kill_switch_service.activate_user(
            user["user_id"], reason=reason.strip() or "Durch Nutzer aktiviert",
            activated_by=actor)
    else:
        kill_switch_service.deactivate_user(user["user_id"], deactivated_by=actor)
    return _redirect("/app/settings?msg=Kill-Switch+aktualisiert.")


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


@router.post("/app/settings/alpaca")
def app_settings_alpaca(request: Request, api_key: str = Form(""), api_secret: str = Form("")):
    """Speichert oder löscht die Alpaca-API-Zugangsdaten des Nutzers (verschlüsselt in der DB)."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    key, secret = api_key.strip(), api_secret.strip()
    if not key or not secret:
        return _redirect("/app/settings?msg=Bitte+API-Key+und+Secret+angeben.")
    db.set_alpaca_credentials(user["user_id"], key, secret)
    return _redirect("/app/settings?msg=Alpaca-Zugangsdaten+gespeichert.")


@router.post("/app/settings/token/rotate")
def app_settings_token_rotate(request: Request):
    """Erzeugt einen neuen Dashboard-Token — der alte Link wird sofort ungültig
    (z. B. nach versehentlichem Teilen oder Leak über Logs/Browser-Verlauf)."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    db.rotate_dashboard_token(user["user_id"])
    return _redirect("/app/settings?msg=Neuer+Dashboard-Link+erzeugt+%E2%80%93+der+alte+ist+ung%C3%BCltig.")


@router.post("/app/settings/alpaca/clear")
def app_settings_alpaca_clear(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    db.clear_alpaca_credentials(user["user_id"])
    return _redirect("/app/settings?msg=Alpaca-Verbindung+entfernt.")


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

# ── Reports (Backtest-Sweeps: Strategie / SL-TP-Modus / Hebel) ───────────────

# Verfügbare Report-Zeiträume (je eigener Backtest-Sweep, Dateien data/reports/<name>_<Y>y.json).
REPORT_YEARS = [1, 3, 5, 8, 15]
DEFAULT_REPORT_YEARS = 5


def _load_report(name: str, years: int | None = None) -> dict | None:
    """Lädt einen Report — bevorzugt die Jahres-Variante (<name>_<Y>y.json),
    sonst die Legacy-Datei ohne Jahr-Suffix (<name>.json)."""
    from stockbot.paths import REPORTS_DIR
    candidates = []
    if years is not None:
        candidates.append(REPORTS_DIR / f"{name}_{years}y.json")
    candidates.append(REPORTS_DIR / f"{name}.json")
    for path in candidates:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


@router.get("/app/reports", response_class=HTMLResponse)
def app_reports(request: Request,
                years: int = Query(default=DEFAULT_REPORT_YEARS),
                strat: list[str] = Query(default=[]),
                lev: list[str] = Query(default=[]),
                mode: list[str] = Query(default=[])):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if years not in REPORT_YEARS:
        years = DEFAULT_REPORT_YEARS
    strategies = _load_report("strategies", years)
    matrix = _load_report("matrix", years)

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
                   sel_strat=sel_strat, sel_lev=sel_lev, sel_mode=sel_mode,
                   report_years=REPORT_YEARS, sel_years=years)


@router.get("/app/reports/equity")
def app_reports_equity(request: Request, key: str = "", lev: str = "", mode: str = "",
                       years: int = DEFAULT_REPORT_YEARS):
    """Depot-Equity-Kurve einer Matrix-Kombination (für den Klick-auf-Zeile-Chart)."""
    user = auth.current_user(request)
    if not user:
        return JSONResponse({"error": "auth"}, status_code=401)
    eq = _load_report("equity", years if years in REPORT_YEARS else DEFAULT_REPORT_YEARS) or {}
    curve = (eq.get("curves") or {}).get(f"{key}|{lev}|{mode}")
    if curve is None:
        return JSONResponse({"error": "not_found", "points": []}, status_code=404)
    return JSONResponse({"points": curve, "benchmark": eq.get("benchmark") or [],
                         "start_capital": eq.get("start_capital"),
                         "start": eq.get("start"), "end": eq.get("end")})


# ── Strategie-Labor (selbst-lernende KI-Strategie) ───────────────────────────

def _lab_context() -> dict:
    """Sammelt Zustand/Vorschlag/Hypothesen des Labors + die Einordnung der KI-Strategie
    gegenüber Buy & Hold aus dem letzten Sweep-Report (falls vorhanden)."""
    state = lab_mod.load_state()
    pending = lab_mod.load_pending()
    hyps = lab_mod.load_hypotheses(limit=40)
    # Einordnung „KI vs. fix": ai_adaptive-Zeile + Benchmark aus dem Strategien-Report.
    rep = _load_report("strategies", DEFAULT_REPORT_YEARS) or {}
    rows = {r["key"]: r for r in rep.get("rows", [])}
    ranked = sorted((r for r in rep.get("rows", []) if r.get("key") != "buyhold_sp500"),
                    key=lambda r: (r.get("return_pct") if r.get("return_pct") is not None else -1e9),
                    reverse=True)
    ai_rank = next((i + 1 for i, r in enumerate(ranked) if r.get("key") == "ai_adaptive"), None)
    return {
        "state": state, "pending": pending, "hyps": hyps,
        "running": state.get("status") == "running" or lab_mod.is_running(),
        "ai_row": rows.get("ai_adaptive"), "bench_row": rows.get("buyhold_sp500"),
        "ai_rank": ai_rank, "n_strats": len(ranked), "report_years": DEFAULT_REPORT_YEARS,
        "web_limit": lab_mod.WEB_LIMIT,
        "lab_schedule": {
            "enabled": config.LAB_DAILY_OPTIMIZATION,
            "days": config.LAB_DAILY_DAYS,
            "hour": config.LAB_DAILY_HOUR,
            "minute": config.LAB_DAILY_MIN,
        },
    }


@router.get("/app/lab", response_class=HTMLResponse)
def app_lab(request: Request, msg: str = ""):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    return _render("lab.html", request, user, active="lab", msg=msg,
                   is_admin=_is_admin(user), **_lab_context())


@router.post("/app/lab/run")
async def app_lab_run(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf das Labor starten.")
    if lab_mod.is_running():
        return _redirect("/app/lab?msg=" + _q("Ein Lauf läuft bereits."))
    started = await run_in_threadpool(lab_mod.start_background_cycle, lab_mod.WEB_LIMIT)
    m = f"Optimierungslauf gestartet ({lab_mod.WEB_LIMIT} Werte, läuft im Hintergrund)." if started \
        else "Ein Lauf läuft bereits."
    return _redirect("/app/lab?msg=" + _q(m))


@router.post("/app/lab/apply")
async def app_lab_apply(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf Vorschläge übernehmen.")
    res = await run_in_threadpool(lab_mod.apply_pending)
    return _redirect("/app/lab?msg=" + _q(res["msg"]))


@router.post("/app/lab/reject")
async def app_lab_reject(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf Vorschläge verwerfen.")
    res = await run_in_threadpool(lab_mod.reject_pending)
    return _redirect("/app/lab?msg=" + _q(res["msg"]))


@router.get("/lab")
def app_lab_alias():
    return _redirect("/app/lab")


# ── Trade-Verlauf (abgeschlossene Trades, neueste zuerst) ─────────────────────

@router.get("/app/history", response_class=HTMLResponse)
def app_history(request: Request):
    """Vollständiger Trade-Verlauf des Nutzers: alle abgeschlossenen Trades als sortier-/
    filterbare Tabelle mit Kennzahlen-Kopf. Reiner Lesezugriff."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    closed = db.get_closed_trades(user["user_id"])
    rows = []
    for t in reversed(closed):                       # neueste zuerst
        sig = t.get("signal") or {}
        pnl = t.get("pnl_eur") or 0.0
        lev = sig.get("effective_leverage")
        if lev is None:
            lev = sig.get("leverage")
        rows.append({
            "date":      t.get("trade_date") or (t.get("created_at") or "")[:10],
            "ticker":    t.get("ticker"),
            "direction": t.get("direction") or "long",
            "entry":     t.get("entry"),
            "exit":      t.get("exit"),
            "pnl_eur":   round(pnl, 2),
            "pnl_pct":   round(t.get("pnl_pct") or 0.0, 2),
            "leverage":  float(lev) if lev else 1.0,
            "strategy":  sig.get("strategy") or "standard",
            "broker":    broker_status_label(t.get("broker_status")),
        })
    n = len(rows)
    wins = sum(1 for r in rows if r["pnl_eur"] > 0)
    losses = sum(1 for r in rows if r["pnl_eur"] < 0)
    summary = {
        "n": n, "wins": wins, "losses": losses,
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "total_pnl": round(sum(r["pnl_eur"] for r in rows), 2),
    }
    return _render("history.html", request, user, active="history", rows=rows, summary=summary)


@router.get("/history")
def app_history_alias():
    return _redirect("/app/history")


# ── Trade-Daten-Export ────────────────────────────────────────────────────────

EXPORT_FIELDS = [
    "ticker", "direction", "status", "trade_date", "created_at",
    "entry", "exit", "pnl_pct", "pnl_eur",
    "signal_price", "strength", "leverage", "effective_leverage", "stop_loss", "take_profit", "strategy",
    "broker_status", "broker_order_id", "broker_filled_qty", "broker_filled_avg_price", "broker_updated_at",
    # Status-Dauern (Sekunden) aus dem Event-Log — wie lange in welchem Status (Teil A).
    "pending_sec", "time_to_fill_sec", "hold_sec", "total_lifetime_sec",
]

# Status-Event-Export: ein Datensatz je Statuswechsel inkl. Dauer im vorherigen Status.
EVENT_FIELDS = ["ts", "trade_date", "ticker", "from_status", "to_status",
                "broker_status", "duration_prev_sec", "note"]


def _export_trade_row(trade: dict, durations: dict | None = None) -> dict:
    sig = trade.get("signal") or {}
    d = durations or {}
    return {
        "ticker": trade.get("ticker"),
        "direction": trade.get("direction"),
        "status": trade.get("status"),
        "trade_date": trade.get("trade_date"),
        "created_at": trade.get("created_at"),
        "entry": trade.get("entry"),
        "exit": trade.get("exit"),
        "pnl_pct": trade.get("pnl_pct"),
        "pnl_eur": trade.get("pnl_eur"),
        "signal_price": sig.get("price"),
        "strength": sig.get("strength"),
        "leverage": sig.get("leverage"),
        "effective_leverage": sig.get("effective_leverage"),
        "stop_loss": sig.get("stop_loss"),
        "take_profit": sig.get("take_profit"),
        "strategy": sig.get("strategy") or sig.get("strategy_key"),
        "broker_status": trade.get("broker_status"),
        "broker_order_id": trade.get("broker_order_id"),
        "broker_filled_qty": trade.get("broker_filled_qty"),
        "broker_filled_avg_price": trade.get("broker_filled_avg_price"),
        "broker_updated_at": trade.get("broker_updated_at"),
        "pending_sec": d.get("pending_sec"),
        "time_to_fill_sec": d.get("time_to_fill_sec"),
        "hold_sec": d.get("hold_sec"),
        "total_lifetime_sec": d.get("total_lifetime_sec"),
    }


def _event_export_rows(user_id: int, ts_from: str | None, ts_to: str | None) -> list[dict]:
    """Status-Events des Nutzers als Export-Zeilen, mit Dauer seit dem vorigen Event je Trade."""
    events = db.get_trade_events_between(user_id, ts_from, ts_to)
    last_ts: dict[int, str] = {}
    rows = []
    for e in events:
        prev = last_ts.get(e["trade_id"])
        dur = None
        if prev:
            a, b = trade_lifecycle.parse_ts(prev), trade_lifecycle.parse_ts(e["ts"])
            if a and b:
                dur = int((b - a).total_seconds())
        last_ts[e["trade_id"]] = e["ts"]
        rows.append({
            "ts": e["ts"], "trade_date": e["trade_date"], "ticker": e["ticker"],
            "from_status": e["from_status"], "to_status": e["to_status"],
            "broker_status": e["broker_status"], "duration_prev_sec": dur, "note": e["note"],
        })
    return rows


def _is_admin(user: dict | None) -> bool:
    """True, wenn der Nutzer der konfigurierte Admin ist (für Log-Download)."""
    return bool(config.ADMIN_CHAT_ID and user and user.get("user_id") == config.ADMIN_CHAT_ID)


def _csv_response(rows: list[dict], fields: list[str], filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/app/backtest", response_class=HTMLResponse)
def app_backtest(request: Request, q: str = "", mode: str = "single", strategy: str = "high52_wide"):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    catalog = _strategy_catalog(q)
    selected = strategy if any(r["key"] == strategy for r in catalog) else (catalog[0]["key"] if catalog else strategy)
    return _render("backtest.html", request, user, active="backtest", q=q, mode=mode,
                   catalog=catalog, selected=selected, result=None, compare_results=None,
                   form={"tickers": "", "years": 2, "top_n": 10, "leverage": 5.0, "trade_size": 1000.0,
                         "max_concurrent": 10, "max_hold": 20, "allow_short": False})


@router.get("/app/backtest/export")
def app_backtest_export(request: Request, format: str = "csv", kind: str = "trades",
                        date_from: str = Query("", alias="from"),
                        date_to: str = Query("", alias="to")):
    """Trade-Daten exportieren. `kind=trades` (Standard, inkl. Status-Dauern) oder `kind=events`
    (ein Datensatz je Statuswechsel). `from`/`to` = 'YYYY-MM-DD' Zeitraum (inklusiv)."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    uid = user["user_id"]
    df = (date_from or "").strip() or None
    dt = (date_to or "").strip() or None
    fmt = (format or "csv").strip().lower()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if fmt not in ("csv", "json"):
        return JSONResponse({"error": "unsupported_format", "allowed": ["csv", "json"]}, status_code=400)

    if kind == "events":
        rows = _event_export_rows(uid, df, dt)
        if fmt == "json":
            return JSONResponse(
                {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(rows), "events": rows},
                headers={"Content-Disposition": f'attachment; filename="trade-events-{stamp}.json"'})
        return _csv_response(rows, EVENT_FIELDS, f"trade-events-{stamp}.csv")

    trades = db.get_all_trades_between(uid, df, dt)
    events_by_trade = db.get_events_by_trade(uid)
    rows = [_export_trade_row(t, trade_lifecycle.compute_durations(events_by_trade.get(t["id"], [])))
            for t in trades]
    if fmt == "json":
        enriched = [dict(t, **{k: r[k] for k in ("pending_sec", "time_to_fill_sec", "hold_sec",
                                                 "total_lifetime_sec")})
                    for t, r in zip(trades, rows)]
        return JSONResponse(
            {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(trades), "trades": enriched},
            headers={"Content-Disposition": f'attachment; filename="trading-data-{stamp}.json"'})
    return _csv_response(rows, EXPORT_FIELDS, f"trading-data-{stamp}.csv")


@router.get("/app/export/logs")
def app_export_logs(request: Request, date_from: str = Query("", alias="from"),
                    date_to: str = Query("", alias="to"), level: str = ""):
    """Bot-Logdatei (gefiltert nach Zeitraum/Level) als Download. NUR Admin (logs/bot.log
    enthält die Aktivität aller Nutzer)."""
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Log-Download ist dem Admin vorbehalten.")
    path = config.LOG_FILE
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Keine Logdatei gefunden ({path}).")
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    filtered = logfilter.filter_log_lines(lines, (date_from or "").strip() or None,
                                           (date_to or "").strip() or None, (level or "").strip() or None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = "\n".join(filtered) + ("\n" if filtered else "")
    return Response(
        content=body, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="bot-log-{stamp}.log"'},
    )


@router.post("/app/backtest", response_class=HTMLResponse)
async def app_backtest_run(request: Request,
                           mode: str = Form("single"),
                           strategy: str = Form("high52_wide"),
                           compare_keys: str = Form(""),
                           tickers: str = Form(""),
                           years: int = Form(2),
                           top_n: int = Form(10),
                           leverage: float = Form(5.0),
                           trade_size: float = Form(1000.0),
                           max_concurrent: int = Form(10),
                           max_hold: int = Form(20),
                           allow_short: bool = Form(False),
                           q: str = Form("")):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    catalog = _strategy_catalog(q)
    selected = strategy if any(r["key"] == strategy for r in catalog) else (catalog[0]["key"] if catalog else strategy)
    ticker_list = _parse_csv_items(tickers)
    compare_list = [k for k in _parse_csv_items(compare_keys) if any(r["key"] == k for r in catalog)]
    if not compare_list and selected:
        compare_list = [selected]
    form = {"tickers": tickers, "years": years, "top_n": top_n, "leverage": leverage,
            "trade_size": trade_size, "max_concurrent": max_concurrent, "max_hold": max_hold,
            "allow_short": allow_short, "compare_keys": ", ".join(compare_list)}

    if mode == "compare":
        results = await run_in_threadpool(backtest_engine.compare_strategies, compare_list or [selected],
                                          ticker_list or None, years, trade_size, allow_short)
        return _render("backtest.html", request, user, active="backtest", q=q, mode=mode,
                       catalog=catalog, selected=selected, compare_results=results, result=None, form=form)

    if mode == "portfolio":
        # Portfolio-/Hebel-Backtest bleibt long-only (Short-Liquidation noch nicht modelliert).
        result = await run_in_threadpool(backtest_engine.backtest_portfolio, selected,
                                         ticker_list or None, years, top_n, leverage,
                                         trade_size, 10000.0, max_concurrent, max_hold)
    else:
        result = await run_in_threadpool(backtest_engine.run_backtest, selected,
                                         ticker_list or None, years, trade_size, allow_short)
    return _render("backtest.html", request, user, active="backtest", q=q, mode=mode,
                   catalog=catalog, selected=selected, compare_results=None, result=result, form=form)


@router.get("/backtest")
def app_backtest_alias():
    return _redirect("/app/backtest")


@router.get("/reports")
def app_reports_alias():
    return _redirect("/app/reports")


@router.get("/dashboard")
def app_dashboard_alias():
    return _redirect("/app/dashboard")


# ── Dashboard-Verknüpfung ─────────────────────────────────────────────────────

@router.get("/app/dashboard", response_class=HTMLResponse)
def app_dashboard(request: Request):
    user = auth.current_user(request)
    if not user:
        return _redirect("/login")
    token = db.get_or_create_dashboard_token(user["user_id"])
    return _render("dashboard.html", request, user, active="dashboard",
                   dashboard_token=token, dashboard_app_url="/app")
