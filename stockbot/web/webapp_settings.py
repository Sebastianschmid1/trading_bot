"""
Einstellungen: Toggles (Auto-Accept, Tagesende-Schließung, Broker-Order), Kill-Switch
(global für den Admin, sonst pro Nutzer), Benachrichtigungskanal, Alpaca-Verbindung,
Dashboard-Token-Rotation und der Demo-Reset.

Kill-Switch und Alpaca-Helfer werden bewusst über `webapp.<name>` angesprochen — Test-
Nähte, siehe Docstring von stockbot/web/webapp.py.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.market import strategies
from stockbot.broker import client as broker
from stockbot import config
from stockbot.services import settings as settings_svc
from stockbot.web import auth
from stockbot.web import webapp

router = webapp.router


@router.get("/app/settings", response_class=HTMLResponse)
def app_settings(request: Request, msg: str = ""):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    toggles = [
        ("set_auto", "Auto-Accept (Signale automatisch starten)", user["auto_accept"]),
        ("set_eod", "Tagesende-Schließung", user["eod_close"]),
    ]
    if webapp._alpaca_ready(user):
        toggles.append(("set_broker", "Echte Broker-Order (Alpaca)", user["broker_exec"]))
    risk_params = [
        ("Handelsmodus", "LIVE · ECHTES GELD" if config.LIVE_TRADING_ENABLED else "PAPER (kein echtes Geld)"),
        ("Maximaler Hebel", f"{config.MAX_LEVERAGE:g}×"),
        ("Optionen", "erlaubt" if config.ALLOW_OPTIONS else "gesperrt"),
        ("Leerverkäufe (Shorts)", "erlaubt" if config.ALLOW_SHORTS else "gesperrt"),
        ("Margin", "erlaubt" if config.ALLOW_MARGIN else "gesperrt"),
    ]
    return webapp._render("settings.html", request, user, active="settings", msg=msg,
                   universes=config.REGION_LABELS, sl_tp_modes=list(config.SL_TP_MODES),
                   has_alpaca=db.has_alpaca_credentials(user["user_id"]),
                   strategies=strategies.production_strategies(), toggles=toggles,
                   risk_params=risk_params, is_admin=webapp._is_admin(user),
                   kill_switch=(webapp.kill_switch_service.global_status if webapp._is_admin(user)
                                else webapp.kill_switch_service.user_status(user["user_id"])))


@router.post("/app/settings/killswitch")
def app_settings_killswitch(request: Request, enabled: str = Form(...), reason: str = Form("")):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    active = enabled == "1"
    actor = f"web:{user['user_id']}"
    if webapp._is_admin(user):
        if active:
            webapp.kill_switch_service.activate_global(
                reason=reason.strip() or "Manuell durch Admin aktiviert", activated_by=actor)
        else:
            webapp.kill_switch_service.deactivate_global(deactivated_by=actor)
    elif active:
        webapp.kill_switch_service.activate_user(
            user["user_id"], reason=reason.strip() or "Durch Nutzer aktiviert",
            activated_by=actor)
    else:
        webapp.kill_switch_service.deactivate_user(user["user_id"], deactivated_by=actor)
    return webapp._redirect("/app/settings?msg=Kill-Switch+aktualisiert.")


@router.post("/app/settings/set")
def app_settings_set(request: Request, action: str = Form(...), value: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    if action in settings_svc.SETTING_ACTIONS:
        settings_svc.apply_setting(user["user_id"], action, value, alpaca_ready=webapp._alpaca_ready(user))
    return webapp._redirect("/app/settings?msg=Gespeichert.")


@router.post("/app/settings/notify")
def app_settings_notify(request: Request, value: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    db.set_notify_channel(user["user_id"], value)
    return webapp._redirect("/app/settings?msg=Benachrichtigungen+aktualisiert.")


@router.post("/app/settings/alpaca")
def app_settings_alpaca(request: Request, api_key: str = Form(""), api_secret: str = Form("")):
    """Speichert oder löscht die Alpaca-API-Zugangsdaten des Nutzers (verschlüsselt in der DB)."""
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    key, secret = api_key.strip(), api_secret.strip()
    if not key or not secret:
        return webapp._redirect("/app/settings?msg=Bitte+API-Key+und+Secret+angeben.")
    db.set_alpaca_credentials(user["user_id"], key, secret)
    return webapp._redirect("/app/settings?msg=Alpaca-Zugangsdaten+gespeichert.")


@router.post("/app/settings/token/rotate")
def app_settings_token_rotate(request: Request):
    """Erzeugt einen neuen Dashboard-Token — der alte Link wird sofort ungültig
    (z. B. nach versehentlichem Teilen oder Leak über Logs/Browser-Verlauf)."""
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    db.rotate_dashboard_token(user["user_id"])
    return webapp._redirect("/app/settings?msg=Neuer+Dashboard-Link+erzeugt+%E2%80%93+der+alte+ist+ung%C3%BCltig.")


@router.post("/app/settings/alpaca/clear")
def app_settings_alpaca_clear(request: Request):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    db.clear_alpaca_credentials(user["user_id"])
    return webapp._redirect("/app/settings?msg=Alpaca-Verbindung+entfernt.")


@router.post("/app/reset")
async def app_reset(request: Request):
    """Setzt den Demo-Trade-Modus des Nutzers zurück: schließt zuerst (falls Broker-Ausführung
    aktiv) alle offenen Alpaca-Positionen und löscht dann alle Trades, Ticks & Mitteilungen.
    Profil, Einstellungen und Alpaca-Verbindung bleiben erhalten."""
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")

    closed, failed = [], []
    if user.get("broker_exec") and webapp._alpaca_ready(user):
        client = webapp._alpaca_client(user)
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
    return webapp._redirect("/app/settings?msg=" + "; ".join(parts) + ".")
