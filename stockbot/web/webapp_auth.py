"""
Anmeldung und Sitzung: Login-Seite, Token-/Telegram-Login, Logout (einzeln und
auf allen Geräten). Registriert seine Routen auf `webapp.router` (siehe
stockbot/web/webapp.py für den gemeinsamen Unterbau).
"""

import os

from fastapi import Request
from fastapi.responses import HTMLResponse

from stockbot.core import db
from stockbot import config
from stockbot.web import auth
from stockbot.web import webapp

router = webapp.router


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    if auth.current_user(request):
        return webapp._redirect("/app")
    return webapp.templates.TemplateResponse(request, "login.html", {
        "user": None, "active": "", "msg": msg, "unread": 0,
        "telegram_bot": os.getenv("TELEGRAM_BOT_USERNAME"),
        "base_url": config.DASHBOARD_BASE_URL,
    })


@router.get("/auth/token")
def auth_token(request: Request, token: str = ""):
    if not auth.rate_ok(f"token:{request.client.host if request.client else '?'}"):
        return webapp._redirect("/login?msg=Zu+viele+Versuche+%E2%80%93+kurz+warten.")
    u = db.get_user_by_token(token)
    if not u:
        return webapp._redirect("/login?msg=Ung%C3%BCltiger+Token")
    return auth.login_response(webapp._redirect("/app"), u["user_id"])


@router.get("/auth/telegram")
def auth_telegram(request: Request):
    if not auth.rate_ok(f"tg:{request.client.host if request.client else '?'}"):
        return webapp._redirect("/login?msg=Zu+viele+Versuche+%E2%80%93+kurz+warten.")
    uid = auth.verify_telegram_login(dict(request.query_params))
    if not uid or not db.get_user(uid):
        return webapp._redirect("/login?msg=Telegram-Login+fehlgeschlagen")
    return auth.login_response(webapp._redirect("/app"), uid)


@router.post("/logout")
def logout(request: Request):
    return auth.logout_response(request, webapp._redirect("/login"))


@router.post("/logout/all")
def logout_all(request: Request):
    """Beendet ALLE Sessions des Nutzers (z. B. nach verlorenem Gerät)."""
    user = auth.current_user(request)
    if user:
        db.delete_user_sessions(user["user_id"])
    resp = webapp._redirect("/login?msg=Auf+allen+Ger%C3%A4ten+abgemeldet.")
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp
