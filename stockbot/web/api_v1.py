"""API v1 (W7): versioniert, Idempotency-Header bei mutierenden Aktionen, RBAC, Pydantic-Schemas,
Trace-ID je Antwort.

`api_v1_router` lässt sich in die bestehende App einhängen; `build_api_v1_app` erzeugt eine
eigenständige, testbare App (mit Trace-ID-Middleware + eigenem Idempotenz-Store). Die Nutzer-
Auflösung `get_api_user` ist via `dependency_overrides` austauschbar (Tests, alternative Auth).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from stockbot.web import rbac
from stockbot.web.api_idempotency import IdempotencyStore
from stockbot.web.rbac import Permission

API_VERSION = "v1"


class TraceIDMiddleware(BaseHTTPMiddleware):
    """Vergibt je Anfrage eine Trace-ID und gibt sie in JEDER Antwort zurück (Header X-Trace-Id)."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response


# ── Schemas ──────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    trace_id: str


class SignalOut(BaseModel):
    ticker: str
    strategy: str
    raw_score: float


class SignalsResponse(BaseModel):
    signals: list[SignalOut]
    trace_id: str


class KillSwitchIn(BaseModel):
    active: bool
    reason: str = ""


class KillSwitchResponse(BaseModel):
    active: bool
    replayed: bool
    trace_id: str


# ── Auth / RBAC-Abhängigkeiten ───────────────────────────────────────────────

def get_api_user(request: Request) -> dict:
    """Standard-Nutzerauflösung über die Session; in Tests via dependency_overrides ersetzbar."""
    from stockbot.web import auth
    user = auth.current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="nicht angemeldet")
    return user


def require_permission(permission: Permission):
    def _dep(user: dict = Depends(get_api_user)) -> dict:
        if not rbac.user_has_permission(user, permission):
            raise HTTPException(status_code=403, detail="keine Berechtigung für diese Aktion")
        return user
    return _dep


def _idempotency_store(request: Request) -> IdempotencyStore:
    store = getattr(request.app.state, "idempotency", None)
    if store is None:
        store = IdempotencyStore()
        request.app.state.idempotency = store
    return store


# ── Router ───────────────────────────────────────────────────────────────────

api_v1_router = APIRouter(prefix="/api/v1", tags=["v1"])


@api_v1_router.get("/health", response_model=HealthResponse)
def health(request: Request):
    return HealthResponse(status="ok", version=API_VERSION, trace_id=request.state.trace_id)


@api_v1_router.get("/signals", response_model=SignalsResponse)
def list_signals(request: Request,
                 user: dict = Depends(require_permission(Permission.READ_SIGNALS))):
    # Scaffold: echte Signalquelle wird in der Integration angebunden.
    return SignalsResponse(signals=[], trace_id=request.state.trace_id)


@api_v1_router.post("/kill-switch", response_model=KillSwitchResponse)
def set_kill_switch(request: Request, body: KillSwitchIn,
                    user: dict = Depends(require_permission(Permission.MANAGE_KILL_SWITCH)),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """Mutierende Aktion — verlangt Idempotency-Key; gleicher Key ⇒ genau EINE Ausführung."""
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key-Header erforderlich")
    store = _idempotency_store(request)

    def run() -> dict:
        # Integration: KillSwitchService aufrufen. Scaffold: Zustand zurückgeben.
        return {"active": body.active}

    result, replayed = store.get_or_run(int(user["user_id"]), idempotency_key, run)
    return KillSwitchResponse(active=result["active"], replayed=replayed,
                              trace_id=request.state.trace_id)


def build_api_v1_app(user_provider=None) -> FastAPI:
    """Eigenständige, testbare API-v1-App (Trace-ID-Middleware + frischer Idempotenz-Store)."""
    app = FastAPI(title="stockbot API", version=API_VERSION)
    app.add_middleware(TraceIDMiddleware)
    app.state.idempotency = IdempotencyStore()
    if user_provider is not None:
        app.dependency_overrides[get_api_user] = user_provider
    app.include_router(api_v1_router)
    return app
