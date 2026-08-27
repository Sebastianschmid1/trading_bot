"""
Auswertung/Reports: Backtest-Sweeps (Strategie/SL-TP-Modus/Hebel), Einzel-/Vergleichs-/
Portfolio-Backtest, Trade-Daten- und Log-Export, Trade-Verlauf sowie der Link auf das
Token-Dashboard.
"""

import csv
import io
import json
import os
import time

from fastapi import Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.core import trade_lifecycle
from stockbot.core import logfilter
from stockbot.backtest import engine as backtest_engine
from stockbot.market import strategies
from stockbot import config
from stockbot.web import auth
from stockbot.web import webapp
# Import erst jetzt, damit dashboard.py beim Einhängen des Routers bereits auf
# `webapp.router` zugreifen kann (siehe Kommentar am Ende von webapp.py).
from stockbot.web.dashboard import broker_status_label

router = webapp.router


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
        return webapp._redirect("/login")
    if years not in REPORT_YEARS:
        years = DEFAULT_REPORT_YEARS
    strategies_report = _load_report("strategies", years)
    matrix = _load_report("matrix", years)

    strat_options = (matrix or {}).get("strategies") or \
        [{"key": r["key"], "label": r["label"]} for r in (strategies_report or {}).get("rows", [])]
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

    return webapp._render("reports.html", request, user, active="reports",
                   strategies=strategies_report, matrix=matrix, rows=rows,
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


# ── Trade-Verlauf (abgeschlossene Trades, neueste zuerst) ─────────────────────

@router.get("/app/history", response_class=HTMLResponse)
def app_history(request: Request):
    """Vollständiger Trade-Verlauf des Nutzers: alle abgeschlossenen Trades als sortier-/
    filterbare Tabelle mit Kennzahlen-Kopf. Reiner Lesezugriff."""
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
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
    return webapp._render("history.html", request, user, active="history", rows=rows, summary=summary)


@router.get("/history")
def app_history_alias():
    return webapp._redirect("/app/history")


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
        return webapp._redirect("/login")
    catalog = _strategy_catalog(q)
    selected = strategy if any(r["key"] == strategy for r in catalog) else (catalog[0]["key"] if catalog else strategy)
    return webapp._render("backtest.html", request, user, active="backtest", q=q, mode=mode,
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
        return webapp._redirect("/login")
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
        return webapp._redirect("/login")
    if not webapp._is_admin(user):
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
        return webapp._redirect("/login")
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
        return webapp._render("backtest.html", request, user, active="backtest", q=q, mode=mode,
                       catalog=catalog, selected=selected, compare_results=results, result=None, form=form)

    if mode == "portfolio":
        # Portfolio-/Hebel-Backtest bleibt long-only (Short-Liquidation noch nicht modelliert).
        result = await run_in_threadpool(backtest_engine.backtest_portfolio, selected,
                                         ticker_list or None, years, top_n, leverage,
                                         trade_size, 10000.0, max_concurrent, max_hold)
    else:
        result = await run_in_threadpool(backtest_engine.run_backtest, selected,
                                         ticker_list or None, years, trade_size, allow_short)
    return webapp._render("backtest.html", request, user, active="backtest", q=q, mode=mode,
                   catalog=catalog, selected=selected, compare_results=None, result=result, form=form)


@router.get("/backtest")
def app_backtest_alias():
    return webapp._redirect("/app/backtest")


@router.get("/reports")
def app_reports_alias():
    return webapp._redirect("/app/reports")


@router.get("/dashboard")
def app_dashboard_alias():
    return webapp._redirect("/app/dashboard")


# ── Dashboard-Verknüpfung ─────────────────────────────────────────────────────

@router.get("/app/dashboard", response_class=HTMLResponse)
def app_dashboard(request: Request):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    token = db.get_or_create_dashboard_token(user["user_id"])
    return webapp._render("dashboard.html", request, user, active="dashboard",
                   dashboard_token=token, dashboard_app_url="/app")
