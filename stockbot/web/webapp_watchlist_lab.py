"""
Watchlist und Strategie-Labor: eigene Ticker verwalten (mit Ticker-Symbol-Validierung/
-Vorschlägen) sowie das selbst-lernende KI-Strategie-Labor (Lauf starten, Vorschlag
übernehmen/verwerfen, Einordnung gegenüber Buy & Hold).
"""

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from stockbot import config
from stockbot.services import watchlist as watchlist_svc
from stockbot.optimize import lab as lab_mod
from stockbot.web import auth
from stockbot.web import webapp
from stockbot.web.webapp_reports import _load_report, DEFAULT_REPORT_YEARS

router = webapp.router


# ── Watchlist ─────────────────────────────────────────────────────────────────

@router.get("/app/watchlist", response_class=HTMLResponse)
def app_watchlist(request: Request, msg: str = "", query: str = "", suggestions: str = ""):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    sugg = [s for s in suggestions.split(",") if s]
    return webapp._render("watchlist.html", request, user, active="watchlist", msg=msg,
                   watchlist=user.get("watchlist") or [], query=query, suggestions=sugg)


@router.post("/app/watchlist/add")
async def app_watchlist_add(request: Request, symbol: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    client = webapp._alpaca_client(user) if webapp._alpaca_ready(user) else None
    res = await run_in_threadpool(watchlist_svc.add_to_watchlist, user["user_id"], symbol, client)
    if res["status"] == "added":
        return webapp._redirect(f"/app/watchlist?msg={res['info']['symbol']}+hinzugef%C3%BCgt.")
    sugg = ",".join(res["suggestions"])
    return webapp._redirect(f"/app/watchlist?query={res['symbol']}&suggestions={sugg}")


@router.post("/app/watchlist/remove")
def app_watchlist_remove(request: Request, symbol: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    watchlist_svc.remove_from_watchlist(user["user_id"], symbol)
    return webapp._redirect(f"/app/watchlist?msg={symbol}+entfernt.")


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
        return webapp._redirect("/login")
    return webapp._render("lab.html", request, user, active="lab", msg=msg,
                   is_admin=webapp._is_admin(user), **_lab_context())


@router.post("/app/lab/run")
async def app_lab_run(request: Request):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    if not webapp._is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf das Labor starten.")
    if lab_mod.is_running():
        return webapp._redirect("/app/lab?msg=" + webapp._q("Ein Lauf läuft bereits."))
    started = await run_in_threadpool(lab_mod.start_background_cycle, lab_mod.WEB_LIMIT)
    m = f"Optimierungslauf gestartet ({lab_mod.WEB_LIMIT} Werte, läuft im Hintergrund)." if started \
        else "Ein Lauf läuft bereits."
    return webapp._redirect("/app/lab?msg=" + webapp._q(m))


@router.post("/app/lab/apply")
async def app_lab_apply(request: Request):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    if not webapp._is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf Vorschläge übernehmen.")
    res = await run_in_threadpool(lab_mod.apply_pending)
    return webapp._redirect("/app/lab?msg=" + webapp._q(res["msg"]))


@router.post("/app/lab/reject")
async def app_lab_reject(request: Request):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    if not webapp._is_admin(user):
        raise HTTPException(status_code=403, detail="Nur der Admin darf Vorschläge verwerfen.")
    res = await run_in_threadpool(lab_mod.reject_pending)
    return webapp._redirect("/app/lab?msg=" + webapp._q(res["msg"]))


@router.get("/lab")
def app_lab_alias():
    return webapp._redirect("/app/lab")
