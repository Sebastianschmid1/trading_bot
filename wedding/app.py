"""FastAPI-App der Hochzeits-Foto-Galerie „Amelie & Tobi".

Eigenständiger Dienst — kein Import aus `stockbot`. Konfiguration ausschließlich
über Umgebungsvariablen (mit entwicklungstauglichen Defaults); `create_app()`
nimmt dieselben Werte zusätzlich als Parameter entgegen, damit Tests ohne
Env-Gefummel auskommen.

Alle erzeugten Links werden mit `request.scope["root_path"]` präfixt, damit die
App hinter Caddy auch unter einem Unterpfad (`/hochzeit`) funktioniert.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wedding.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    LoginRateLimiter,
    can_upload,
    display_name_for,
    load_or_create_secret,
    load_users,
    make_session_cookie,
    read_session_cookie,
    verify_password,
)
from wedding.storage import (
    ALLOWED_EXTENSIONS,
    MAX_FILES_PER_REQUEST,
    PhotoRejected,
    PhotoStore,
    content_type_for,
)

log = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = "./data/wedding"
DEFAULT_MAX_BYTES = 30 * 1024 * 1024
#: Benutzername des Nur-Ansehen-Zugangs (Direktlink `/gast`).
GUEST_USERNAME = "gast"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        log.warning("%s=%r ist keine Zahl — nutze %d.", name, raw, default)
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Settings:
    """Laufzeit-Konfiguration der Galerie."""

    data_dir: Path
    users_file: Path
    secret_file: Path
    max_bytes: int
    cookie_secure: bool


def settings_from_env(
    *,
    data_dir: str | Path | None = None,
    users_file: str | Path | None = None,
    secret_file: str | Path | None = None,
    max_bytes: int | None = None,
    cookie_secure: bool | None = None,
) -> Settings:
    """Baut die Settings aus Env-Vars; explizite Parameter haben Vorrang."""
    resolved_data_dir = Path(data_dir or os.getenv("WEDDING_DATA_DIR") or DEFAULT_DATA_DIR)
    resolved_users = Path(
        users_file or os.getenv("WEDDING_USERS_FILE") or (resolved_data_dir / "users.json")
    )
    resolved_secret = Path(
        secret_file or os.getenv("WEDDING_SECRET_FILE") or (resolved_data_dir / "secret")
    )
    return Settings(
        data_dir=resolved_data_dir,
        users_file=resolved_users,
        secret_file=resolved_secret,
        max_bytes=max_bytes if max_bytes is not None else _env_int("WEDDING_MAX_BYTES", DEFAULT_MAX_BYTES),
        cookie_secure=(
            cookie_secure if cookie_secure is not None else _env_flag("WEDDING_COOKIE_SECURE", False)
        ),
    )


# --------------------------------------------------------------------------- #
# Hilfen
# --------------------------------------------------------------------------- #
def _base(request: Request) -> str:
    """Der Prefix, unter dem die App nach außen hängt (`""` oder `/hochzeit`)."""
    return str(request.scope.get("root_path") or "").rstrip("/")


def _url(request: Request, path: str) -> str:
    return f"{_base(request)}{path}"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unbekannt"


def _current_user(request: Request) -> str | None:
    """Benutzername aus dem Session-Cookie — oder ``None``."""
    username = read_session_cookie(
        request.app.state.session_secret, request.cookies.get(SESSION_COOKIE)
    )
    if username is None:
        return None
    # Nutzer, die es nicht mehr gibt, gelten als ausgeloggt.
    if username not in load_users(request.app.state.settings.users_file):
        return None
    return username


def _guest_entry(request: Request) -> dict | None:
    """users.json-Eintrag des geteilten Gäste-Zugangs — oder ``None``, wenn es keinen gibt."""
    return load_users(request.app.state.settings.users_file).get(GUEST_USERNAME)


def _set_session_cookie(response, request: Request, username: str) -> None:
    settings: Settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(request.app.state.session_secret, username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=_base(request) or "/",
    )


# --------------------------------------------------------------------------- #
# App-Factory
# --------------------------------------------------------------------------- #
def create_app(
    *,
    data_dir: str | Path | None = None,
    users_file: str | Path | None = None,
    secret_file: str | Path | None = None,
    max_bytes: int | None = None,
    cookie_secure: bool | None = None,
) -> FastAPI:
    """Erzeugt die Galerie-App. Parameter überschreiben die Env-Konfiguration."""
    settings = settings_from_env(
        data_dir=data_dir,
        users_file=users_file,
        secret_file=secret_file,
        max_bytes=max_bytes,
        cookie_secure=cookie_secure,
    )
    store = PhotoStore(settings.data_dir)
    store.ensure_dirs()

    application = FastAPI(title="Hochzeit — Amelie & Tobi", docs_url=None, redoc_url=None)
    application.state.settings = settings
    application.state.store = store
    application.state.session_secret = load_or_create_secret(settings.secret_file)
    application.state.rate_limiter = LoginRateLimiter()

    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    application.mount(
        "/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static"
    )

    def render(request: Request, template: str, context: dict, status_code: int = 200):
        merged = {"base": _base(request), **context}
        return templates.TemplateResponse(request, template, merged, status_code=status_code)

    # ------------------------------------------------------------- Healthz --
    @application.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:  # noqa: D401 — ohne Auth, für systemd/Monitoring
        """Liveness-Probe."""
        return "ok"

    # --------------------------------------------------------------- Login --
    @application.get("/login")
    async def login_form(request: Request):
        if _current_user(request) is not None:
            return RedirectResponse(_url(request, "/"), status_code=303)
        return render(request, "login.html", {"error": None, "guest": False})

    @application.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
    ):
        limiter: LoginRateLimiter = request.app.state.rate_limiter
        key = _client_key(request)
        if limiter.is_blocked(key):
            return render(
                request,
                "login.html",
                {
                    "error": "Zu viele Versuche. Bitte eine Minute warten und dann nochmal probieren.",
                    "guest": False,
                },
                status_code=429,
            )

        users = load_users(request.app.state.settings.users_file)
        name = username.strip().lower()
        entry = users.get(name)
        if entry is None or not verify_password(entry, password):
            limiter.record_failure(key)
            log.info("Fehlgeschlagener Login für %r von %s.", name, key)
            return render(
                request,
                "login.html",
                {"error": "Name oder Passwort stimmt nicht. Versuch's nochmal!", "guest": False},
                status_code=401,
            )

        limiter.reset(key)
        response = RedirectResponse(_url(request, "/"), status_code=303)
        _set_session_cookie(response, request, name)
        return response

    @application.get("/gast")
    async def guest_form(request: Request):
        """Vereinfachte Anmeldung für den geteilten Gäste-Zugang: nur ein Passwortfeld."""
        if _guest_entry(request) is None:
            raise HTTPException(status_code=404, detail="Es gibt keinen Gäste-Zugang.")
        if _current_user(request) is not None:
            return RedirectResponse(_url(request, "/"), status_code=303)
        return render(request, "login.html", {"error": None, "guest": True})

    @application.post("/gast")
    async def guest_submit(request: Request, password: str = Form("")):
        entry = _guest_entry(request)
        if entry is None:
            raise HTTPException(status_code=404, detail="Es gibt keinen Gäste-Zugang.")

        # Bewusst derselbe Limiter und derselbe Schlüssel wie beim normalen Login:
        # sonst wäre /gast ein Schlupfloch, um das Limit von /login zu umgehen.
        limiter: LoginRateLimiter = request.app.state.rate_limiter
        key = _client_key(request)
        if limiter.is_blocked(key):
            return render(
                request,
                "login.html",
                {
                    "error": "Zu viele Versuche. Bitte eine Minute warten und dann nochmal probieren.",
                    "guest": True,
                },
                status_code=429,
            )

        if not verify_password(entry, password):
            limiter.record_failure(key)
            log.info("Fehlgeschlagener Gäste-Login von %s.", key)
            return render(
                request,
                "login.html",
                {
                    "error": "Das Passwort stimmt nicht — schau nochmal auf der Einladung nach!",
                    "guest": True,
                },
                status_code=401,
            )

        limiter.reset(key)
        response = RedirectResponse(_url(request, "/"), status_code=303)
        _set_session_cookie(response, request, GUEST_USERNAME)
        return response

    @application.post("/logout")
    async def logout(request: Request):
        response = RedirectResponse(_url(request, "/login"), status_code=303)
        response.delete_cookie(SESSION_COOKIE, path=_base(request) or "/")
        return response

    # ------------------------------------------------------------- Galerie --
    @application.get("/")
    async def gallery(request: Request):
        username = _current_user(request)
        if username is None:
            return RedirectResponse(_url(request, "/login"), status_code=303)
        users = load_users(request.app.state.settings.users_file)
        photos = request.app.state.store.list_photos()
        return render(
            request,
            "gallery.html",
            {
                "username": username,
                "display_name": display_name_for(users, username),
                "can_upload": can_upload(users, username),
                "photos": photos,
                "max_mb": max(1, request.app.state.settings.max_bytes // (1024 * 1024)),
                "max_files": MAX_FILES_PER_REQUEST,
                "allowed_extensions": ", ".join(
                    ext.lstrip(".").upper() for ext in ALLOWED_EXTENSIONS
                ),
            },
        )

    # -------------------------------------------------------------- Upload --
    @application.post("/upload")
    async def upload(request: Request, files: list[UploadFile] = File(default=[])):
        username = _current_user(request)
        if username is None:
            return JSONResponse(
                {"error": "Bitte neu anmelden.", "login": _url(request, "/login")}, status_code=401
            )

        settings: Settings = request.app.state.settings
        store: PhotoStore = request.app.state.store
        users = load_users(settings.users_file)
        # Nur-Ansehen-Zugang: serverseitig blocken, nicht bloß im Template verstecken.
        if not can_upload(users, username):
            return JSONResponse(
                {"error": "Dieser Zugang kann Fotos nur ansehen.", "results": []}, status_code=403
            )

        if not files:
            return JSONResponse({"error": "Keine Datei ausgewählt.", "results": []}, status_code=400)
        if len(files) > MAX_FILES_PER_REQUEST:
            return JSONResponse(
                {
                    "error": f"Bitte höchstens {MAX_FILES_PER_REQUEST} Fotos auf einmal hochladen.",
                    "results": [],
                },
                status_code=400,
            )

        display = display_name_for(users, username)

        results = []
        accepted = 0
        for item in files:
            original = (item.filename or "").strip()
            if not original:
                continue
            try:
                photo = await store.save_upload(
                    item,
                    uploader=username,
                    display_name=display,
                    max_bytes=settings.max_bytes,
                )
            except PhotoRejected as exc:
                results.append({"original_name": original, "ok": False, "error": str(exc)})
                continue
            accepted += 1
            results.append({"original_name": original, "ok": True, "name": photo.name})

        if not results:
            return JSONResponse({"error": "Keine Datei ausgewählt.", "results": []}, status_code=400)
        status = 200 if accepted else 400
        return JSONResponse({"uploaded": accepted, "results": results}, status_code=status)

    # --------------------------------------------------------------- Fotos --
    @application.get("/photos/{name}")
    async def photo(request: Request, name: str):
        if _current_user(request) is None:
            return RedirectResponse(_url(request, "/login"), status_code=303)
        path = request.app.state.store.photo_path(name)
        if path is None:
            raise HTTPException(status_code=404, detail="Foto nicht gefunden.")
        return FileResponse(path, media_type=content_type_for(name))

    @application.post("/photos/{name}/delete")
    async def delete_photo(request: Request, name: str):
        username = _current_user(request)
        if username is None:
            return RedirectResponse(_url(request, "/login"), status_code=303)
        # Ein Gast besitzt zwar nie eigene Fotos, aber der Check gehört trotzdem hierher.
        if not can_upload(load_users(request.app.state.settings.users_file), username):
            raise HTTPException(status_code=403, detail="Dieser Zugang kann Fotos nur ansehen.")
        try:
            request.app.state.store.delete(name, username=username)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Foto nicht gefunden.") from None
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="Nur eigene Fotos können gelöscht werden."
            ) from None
        return RedirectResponse(_url(request, "/"), status_code=303)

    return application


#: Modul-Level-Instanz für `uvicorn wedding.app:app`.
app = create_app()
