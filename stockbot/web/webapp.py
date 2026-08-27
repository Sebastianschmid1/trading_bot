"""
Fundament der interaktiven Website (Phase 1–2): Templates, der gemeinsame APIRouter
(inkl. CSRF-Schutz), OMS-Instanz, Kill-Switch-Service, Alpaca-/Broker-Order-Helfer
sowie die Render-/Redirect-Helfer, die alle Fach-Router brauchen.

Die eigentlichen Routen liegen — nach Bereich getrennt — in eigenen Modulen, die ganz
unten importiert werden (Registrierung über den `router` hier):
  webapp_auth.py           Anmeldung/Sitzung (Login, Token-/Telegram-Login, Logout)
  webapp_signals.py        Signale und Freigabe (Startseite, Scan, Annehmen/Ablehnen/Verkauf)
  webapp_settings.py       Einstellungen (inkl. Kill-Switch-Toggle, Alpaca-Verbindung, Reset)
  webapp_reports.py        Auswertung/Reports (Reports, Backtest, Export, Trade-Verlauf)
  webapp_watchlist_lab.py  Watchlist und Strategie-Labor

Wie beim Paket `stockbot/core/db` ist dieses Modul zugleich die Test-Naht: Namen wie
`_alpaca_ready`, `_alpaca_client`, `_attach_demo_option`, `_broker_will_execute` und
`kill_switch_service` werden in Tests auf `webapp` ersetzt (`monkeypatch.setattr(webapp,
...)`). Die Fach-Router schlagen sie deshalb bewusst über `webapp.<name>` nach statt sie
zu importieren — sonst würde ein Test-Patch die Fach-Router nicht erreichen (Python löst
einen unqualifizierten Namen immer über die Globals des Moduls auf, in dem er *definiert*
wurde, nicht des Moduls, das ihn aufruft).

Läuft als Router in stockbot/web/dashboard.py eingehängt (ein Server für Dashboard + App).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from stockbot.core import db
from stockbot.core.domain import Mode, OrderStatus, Signal, SignalStatus, TradeIntent
from stockbot.core.evaluator import trade_pnl
from stockbot.broker import client as broker
from stockbot.broker import sizing
from stockbot.broker import reconcile as reconcile_mod
from stockbot import config
from stockbot.services import trades as trade_svc
from stockbot.web import auth
from stockbot.execution.oms import OrderManagementSystem
from stockbot.execution import risk_context
from stockbot.core.kill_switch import KillSwitchService

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# §32.9: das gemeinsame Web↔Telegram-Glossar den Templates als `glossary` bereitstellen,
# damit Aktions-/Status-Begriffe aus einer einzigen Quelle gerendert werden.
from stockbot.core import glossary as glossary
templates.env.globals["glossary"] = glossary


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


def _is_admin(user: dict | None) -> bool:
    """True, wenn der Nutzer der konfigurierte Admin ist (für Log-Download)."""
    return bool(config.ADMIN_CHAT_ID and user and user.get("user_id") == config.ADMIN_CHAT_ID)


def _kill_switch_banner_status(user: dict | None):
    """Liest den Kill-Switch-Zustand NUR zur Anzeige (kein Eingriff in TSAFE-Pfade,
    keine neue Sperrlogik): globaler Kill-Switch zuerst (betrifft alle Nutzer), sonst
    der persönliche Kill-Switch dieses Nutzers — dieselbe ODER-Verknüpfung, die
    `KillSwitchService.is_new_position_allowed` bereits für die Order-Freigabe nutzt,
    hier nur für die appbar-weite Sichtbarkeit (agent/UI-KILLSWITCH-VISIBLE)."""
    if not user:
        return None
    # Der Lesezugriff geht ungecacht in die DB (`_load_active` bei jedem Property-Zugriff) und
    # sitzt hier im Render-Pfad JEDER Seite. Ohne Auffangnetz nimmt ein DB-Schluckauf die ganze
    # Web-App mit — auch die Einstellungsseite, auf der man den Kill-Switch abschalten wuerde.
    # Die Anzeige darf ausfallen, die Bedienbarkeit nicht; die Order-Freigabe selbst haengt
    # unveraendert an `is_new_position_allowed` und wird davon nicht beruehrt.
    try:
        status = kill_switch_service.global_status
        if status is not None and status.active:
            return status
        return kill_switch_service.user_status(user["user_id"])
    except Exception as exc:
        log.warning("Kill-Switch-Anzeige nicht lesbar (%s: %s) — Chip wird ausgelassen.",
                    type(exc).__name__, exc)
        return None


def _render(name: str, request: Request, user: dict, active: str = "", msg: str = "", **ctx):
    if user and user.get("broker_exec"):
        trade_mode = "paper" if config.ALPACA_PAPER else "live"
    else:
        trade_mode = "demo"
    return templates.TemplateResponse(request, name, {
        "user": user, "active": active, "msg": msg,
        "is_admin": _is_admin(user), "trade_mode": trade_mode,
        "kill_switch_status": _kill_switch_banner_status(user), **ctx,
    })


def _redirect(path: str):
    return RedirectResponse(path, status_code=303)


def _q(text: str) -> str:
    """URL-quotet einen Flash-Text für den ?msg=-Redirect."""
    from urllib.parse import quote
    return quote(text or "", safe="")


# Fach-Router registrieren sich per @webapp.router-Dekorator, indem sie dieses Modul
# importieren (siehe Modul-Docstring). Import erst jetzt, ganz am Ende: `router` und alle
# obigen Helfer/Test-Nähte müssen zuerst existieren, bevor ein Fach-Router (der teils
# zirkulär über stockbot.web.dashboard wieder bei `webapp.router` landet) geladen wird.
from stockbot.web import webapp_auth              # noqa: E402,F401
from stockbot.web import webapp_signals           # noqa: E402,F401
from stockbot.web import webapp_settings          # noqa: E402,F401
from stockbot.web import webapp_reports           # noqa: E402,F401
from stockbot.web import webapp_watchlist_lab     # noqa: E402,F401

# Re-Exports: einige Tests ersetzen/lesen diese Namen auf `webapp`, obwohl sie fachlich in
# einem der obigen Router-Module leben (Scan-Cache/Sparkline gehören zu webapp_signals.py,
# der Export-Zeilenbauer und der Kill-Switch-Toggle zu webapp_reports.py/webapp_settings.py).
from stockbot.web.webapp_signals import _scan_cache, _sparkline          # noqa: E402,F401
from stockbot.web.webapp_reports import _event_export_rows               # noqa: E402,F401
from stockbot.web.webapp_settings import app_settings_killswitch         # noqa: E402,F401
