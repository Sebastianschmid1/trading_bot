"""
Signal-Feed und Freigabe: Startseite (/app) mit ausstehenden/aktiven/Broker-Trades,
On-Demand-Signal-Scan (Cache + Sparkline), Annehmen/Ablehnen/Hebel setzen und Verkauf.

Ruft die Broker-Order-Ausführung (`_execute_broker_order_for_web` & Co.) und die
Alpaca-/Kill-Switch-Helfer bewusst über `webapp.<name>` auf statt sie zu importieren —
das sind Test-Nähte, die Tests per `monkeypatch.setattr(webapp, ...)` ersetzen (siehe
Docstring von stockbot/web/webapp.py).
"""

import time
from datetime import datetime, timezone

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from stockbot.core import db
from stockbot.core.domain import RiskProfile
from stockbot.market import strategies
from stockbot.market import asset_classes
from stockbot.market import analyzer
from stockbot.services import trades as trade_svc
from stockbot import config
from stockbot.web import auth
from stockbot.web import feed_status as feed_status_mod
from stockbot.web import webapp
# Import erst jetzt, damit dashboard.py beim Einhängen des Routers bereits auf
# `webapp.router` zugreifen kann (siehe Kommentar am Ende von webapp.py).
from stockbot.web.dashboard import build_dashboard_data, broker_status_label, trade_status_label

router = webapp.router

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


# ── App-Startseite: Signale + aktive Trades ──────────────────────────────────

@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, msg: str = "", atf: str = ""):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
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
    # §UI-ONBOARDING/Befund 3: Erstnutzer-Karte NUR, solange es weder je einen Trade
    # (aktiv ODER abgeschlossen) noch eine Alpaca-Verbindung gibt — sonst hat der Nutzer
    # längst einen Bezugspunkt und die Karte wäre nur noch Lärm. Vor dem atf-Filter (unten)
    # gemessen, sonst würde ein leerer Anlageklassen-Filter fälschlich als "leeres Konto" zählen.
    show_onboarding = not (
        active_trades or db.get_closed_trades(user["user_id"]) or webapp._alpaca_ready(user))
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
    feed = _feed_status_for(user, scanned)
    # §32.5: Konnte der aktuelle Kurs einer angezeigten Position gar nicht geholt werden
    # (dashboard._current_price ⇒ `price_degraded`), ist das kein „unbekanntes Alter",
    # sondern eine ausgefallene Datenquelle — eigener Zustand, Einstiege gesperrt.
    # „veraltet" (stale) hat Vorrang: es blockiert bereits und ist die schaerfere Aussage.
    degraded_tickers = [t["ticker"] for t in active_trades if t.get("price_degraded")]
    if degraded_tickers and not feed.blocks_orders:
        feed = feed_status_mod.degraded(
            f"Für {', '.join(degraded_tickers)} ist der aktuelle Kurs nicht abrufbar, "
            f"damit sind auch Bewertung und unrealisiertes Ergebnis dieser Positionen "
            f"unbekannt.")
    return webapp._render("app.html", request, user, active="home", msg=msg,
                   pending=pending, broker_pending=broker_pending,
                   broker_closing=broker_closing, active_trades=active_trades,
                   asset_classes=asset_classes.all_asset_classes(), asset_pref=asset_pref,
                   scanned=scanned, trade_filter=atf,
                   feed_status=feed, feed_as_of_utc=feed_as_of_utc,
                   show_onboarding=show_onboarding,
                   next_analysis_minutes=config.INTRADAY_SCAN_INTERVAL_SEC // 60)


@router.post("/app/asset")
def app_set_asset(request: Request, asset: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    cls = asset_classes.get_asset_class(asset)
    db.set_asset_pref(user["user_id"], cls.key)
    return webapp._redirect("/app")


@router.post("/app/scan")
async def app_scan(request: Request, asset: str = Form(None)):
    """Wählt die Anlageklasse (persistiert) UND fordert live Signale dafür an — in einem
    Schritt (kein JS nötig). `asset='all'` scannt ALLE Klassen mit ihrem jeweiligen Profil
    und rankt die Treffer innerhalb der Standard-Strategie. Ergebnis (inkl. 7-Tage-Mini-Chart)
    wird kurz gecacht."""
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")

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
    return webapp._redirect(f"/app?msg={n}+Signal(e)+gefunden." if n else "/app?msg=Aktuell+keine+Signale.")


def _wants_json(request: Request) -> bool:
    """True, wenn die Anfrage per fetch/AJAX kommt (für Inline-Status statt Redirect)."""
    return request.headers.get("x-requested-with", "").lower() == "fetch"


@router.post("/app/scan/accept")
async def app_scan_accept(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    entry = _scan_cache.get(user["user_id"]) or {}
    sig = next((s for s in entry.get("signals", []) if s["ticker"] == ticker), None)
    if not sig:
        msg = "Signal abgelaufen – bitte neu anfordern."
        if _wants_json(request):
            return JSONResponse({"ok": False, "status": "expired", "msg": msg})
        return webapp._redirect("/app?msg=Signal+abgelaufen+%E2%80%93+bitte+neu+anfordern.")
    broker_status = "broker_pending" if webapp._broker_will_execute(user) else "active"
    res = await run_in_threadpool(trade_svc.accept_signal, user["user_id"], {**sig, "_accept_status": broker_status})
    if res["ok"]:
        # accept_signal aktiviert standardmäßig active; bei Broker-Ausführung korrigieren wir den Status über accept_trade unten nicht.
        # Für On-Demand-Signale nutzen wir daher denselben Pfad wie /app/accept: ggf. Status nachträglich vorm Orderversand setzen.
        if broker_status == "broker_pending":
            db.mark_broker_pending(user["user_id"], ticker, order_id=None, broker_status="not_submitted")
        await run_in_threadpool(webapp._attach_demo_option, user, ticker)
        trade = db.get_trade(user["user_id"], ticker)
        broker_res = await run_in_threadpool(webapp._execute_broker_order_for_web, user, trade) if trade else {"status": "unavailable"}
        msg = broker_res.get("msg") or f"{ticker} gestartet."
        status = broker_res.get("status") or "accepted"
    elif res.get("reason") == "entry_cutoff":
        msg, status = "Kein neuer Einstieg mehr — zu kurz vor Handelsschluss.", "entry_cutoff"
    else:
        msg, status = f"{ticker} heute bereits gehandelt.", "unavailable"
    if _wants_json(request):
        return JSONResponse({"ok": res["ok"], "status": status, "msg": msg})
    return webapp._redirect(f"/app?msg={msg}")


@router.post("/app/accept")
async def app_accept(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    broker_status = "broker_pending" if webapp._broker_will_execute(user) else "active"
    res = await run_in_threadpool(trade_svc.accept_trade, user["user_id"], ticker, status=broker_status)
    if res["ok"]:
        await run_in_threadpool(webapp._attach_demo_option, user, ticker)
        broker_res = await run_in_threadpool(webapp._execute_broker_order_for_web, user, res["trade"])
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
    return webapp._redirect(f"/app?msg={msg}")


@router.post("/app/reject")
def app_reject(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    ok = trade_svc.reject_trade(user["user_id"], ticker)
    return webapp._redirect(f"/app?msg={ticker + ' abgelehnt.' if ok else 'Nicht möglich.'}")


@router.post("/app/lev")
def app_lev(request: Request, ticker: str = Form(...), leverage: float = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    updated = trade_svc.set_pending_leverage(user["user_id"], ticker, leverage)
    return webapp._redirect(f"/app?msg={'Hebel gesetzt.' if updated else 'Hebel nicht mehr änderbar.'}")


@router.post("/app/sell")
async def app_sell(request: Request, ticker: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return webapp._redirect("/login")
    trade = db.get_trade(user["user_id"], ticker)
    if not trade or trade.get("status") != "active":
        return webapp._redirect("/app?msg=Trade+nicht+mehr+aktiv.")

    if webapp._broker_will_execute(user):
        res = await run_in_threadpool(trade_svc.sell_trade, user["user_id"], ticker, broker_close=True)
        if not res["ok"]:
            return webapp._redirect("/app?msg=Trade+nicht+mehr+aktiv.")
        broker_res = await run_in_threadpool(webapp._execute_broker_close_for_web, user, res["trade"])
        if broker_res.get("status") == "closed":
            msg = f"{ticker} verkauft: {broker_res.get('filled_avg_price', 0.0):.2f}$"
        else:
            msg = broker_res.get("msg") or "Verkauf läuft weiter bei Alpaca."
        return webapp._redirect(f"/app?msg={msg}")

    res = await run_in_threadpool(trade_svc.sell_trade, user["user_id"], ticker)
    if res["ok"]:
        # agent/CURRENCY-HONEST: pnl_eur traegt tatsaechlich USD (kein FX-Kurs im Repo,
        # Alpaca liefert nur USD) — Anzeige entsprechend als $ statt Euro.
        msg = f"{ticker} verkauft: {res['pnl_eur']:+.2f}$"
    else:
        msg = "Trade nicht mehr aktiv."
    return webapp._redirect(f"/app?msg={msg}")
