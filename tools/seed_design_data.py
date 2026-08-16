#!/usr/bin/env python3
"""CLI: befüllt eine LOKALE SQLite-Entwicklungs-DB mit reproduzierbaren Demo-Daten für die
visuelle Design-Abnahme der Web-App durch den `design-lead`-Agenten (DESIGN.md).

`data/bot.db` ist auf einem frischen Checkout leer (0 Nutzer, 0 Trades) — die App zeigt dann
neun leere Seiten und man kommt nicht einmal am Login vorbei. Dieses Skript legt EINEN
Demo-Nutzer mit einem breiten Zustandsraum an Trades an (offen in Gewinn/Verlust, geschlossen
über mehrere Tage, abgelehnte Signale mit echten Betriebs-Ablehngründen, Layout-Randfälle,
ein bewusst leerer Bereich, aktiver Kill-Switch) und gibt am Ende die Dashboard-URL samt
Token aus.

Sicherheitsauflage (nicht verhandelbar, siehe AGENTS.md):
  * Läuft NUR gegen SQLite. Ist `config.DB_BACKEND` != "sqlite", bricht das Skript ab, BEVOR
    irgendetwas geschrieben wird.
  * Der Zielpfad ist standardmäßig `data/design_seed.db` — NIEMALS `data/bot.db` (eine
    bestehende Entwickler-DB wird nie überschrieben). `--db-path` erlaubt einen anderen Pfad,
    aber niemals den Standardpfad der laufenden App.
  * Kein Netzwerkzugriff, kein Broker-Aufruf, keine echten Credentials — der Demo-Nutzer
    bekommt keine Broker-Keys (kein `broker_platform`/`broker_api_key`/`broker_api_secret`).

Idempotent (mehrfach ausführbar, gleicher Zustand, keine Dubletten): alle Zeilen dieses
Demo-Nutzers werden vor dem Neuaufbau gelöscht und deterministisch neu geschrieben. Trade-Daten
liegen relativ zum aktuellen UTC-Handelstag (`db.today_utc_date()`), damit die Equity-Kurve bei
jedem Lauf innerhalb der Standard-Fenster (7/14/30 Tage) sichtbar bleibt — die Werte selbst
(Kurse, P&L, Gründe) sind feste Konstanten, keine Zufallszahlen.

Nutzung:
    ENCRYPTION_KEY=<beliebiger Fernet-Key, siehe unten> python tools/seed_design_data.py

`stockbot.config` verlangt beim Import einen `ENCRYPTION_KEY` (verschlüsselt hier nur
Wegwerf-Demodaten — der Demo-Nutzer hat ohnehin keine Broker-Keys). Einmalig erzeugen mit:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Der Wert gilt nur für den jeweiligen Lauf (Umgebungsvariable) — nie committen, nie ausgeben.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from stockbot import config  # noqa: E402  (Backend-Guard braucht den Import zuerst)

# ── Sicherheits-Guard: SQLite-only, VOR jedem weiteren Import mit Schreibzugriff ────────────


def _refuse_unless_sqlite() -> None:
    """Bricht hart ab, wenn `DB_BACKEND` nicht `sqlite` ist. Schreibt dabei nichts."""
    if config.DB_BACKEND != "sqlite":
        raise SystemExit(
            "ABBRUCH: DB_BACKEND="
            f"'{config.DB_BACKEND}' — dieses Skript darf ausschließlich gegen eine lokale "
            "SQLite-Datei laufen (Sicherheitsauflage, siehe AGENTS.md/CLAUDE.md). Es wurde "
            "NICHTS geschrieben. Setze DB_BACKEND=sqlite (oder lass die Variable unset) und "
            "starte das Skript erneut."
        )


_refuse_unless_sqlite()

# Ab hier ist DB_BACKEND=sqlite bestätigt — jetzt erst der DB-Seam-Import (öffnet SQLite).
from stockbot.core import db  # noqa: E402
from stockbot.paths import PROJECT_ROOT  # noqa: E402

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "design_seed.db"
FORBIDDEN_DB_PATH = (PROJECT_ROOT / "data" / "bot.db").resolve()

USER_ID = 999000001          # klar erkennbarer Fake-Chat-ID, kollidiert nicht mit echten Nutzern
USERNAME = "design_demo"
TRADE_SIZE_EUR = 500.0
LEVERAGE = 1.0

# Ablehngründe aus dem echten Betrieb (core/glossary.py BROKER_STATUS_LABELS,
# core/data_quality.py, core/risk.py) — genau die drei aus dem Auftrag.
REASON_SPREAD_WIDE = "spread_wide"
REASON_MAX_POSITIONS = "max_positions_reached"
REASON_QUOTE_STALE = "quote_stale"


def _dt(day_iso: str, hhmmss: str) -> str:
    """Baut einen naiven UTC-Zeitstempel-String im DB-Vertrag 'YYYY-MM-DD HH:MM:SS'."""
    return f"{day_iso} {hhmmss}"


def _signal(ticker: str, *, direction: str = "long", price: float, strength: float,
            strategy: str = "standard", leverage: float = LEVERAGE,
            stop_loss: float | None = None, take_profit: float | None = None) -> dict:
    sig = {
        "ticker": ticker, "direction": direction, "price": price, "strength": strength,
        "strategy": strategy, "leverage": leverage,
    }
    if stop_loss is not None:
        sig["stop_loss"] = stop_loss
    if take_profit is not None:
        sig["take_profit"] = take_profit
    return sig


def _wipe_user_data(transaction, user_id: int) -> None:
    """Löscht alle Zeilen des Demo-Nutzers vor dem Neuaufbau (macht das Skript idempotent —
    ein zweiter Lauf am selben Tag erzeugt exakt denselben inhaltlichen Zustand, keine
    Dubletten). Betrifft ausschließlich den Fake-Nutzer `USER_ID`."""
    transaction.execute("DELETE FROM trade_ticks WHERE user_id = :uid", {"uid": user_id})
    transaction.execute("DELETE FROM trade_events WHERE user_id = :uid", {"uid": user_id})
    transaction.execute("DELETE FROM trades WHERE user_id = :uid", {"uid": user_id})
    transaction.execute(
        "DELETE FROM kill_switches WHERE scope = 'user' AND user_id = :uid", {"uid": user_id}
    )


def _insert_trade(transaction, *, user_id: int, ticker: str, trade_date: str, direction: str,
                   status: str, signal: dict, message_id: int, created_at: str,
                   entry: float | None = None, exit_price: float | None = None,
                   pnl_eur: float | None = None, pnl_pct: float | None = None,
                   broker_status: str | None = None, broker_order_id: str | None = None,
                   broker_updated_at: str | None = None) -> int:
    return transaction.insert_id(
        """INSERT INTO trades
               (user_id, trade_date, ticker, direction, signal_json, message_id, status,
                entry, exit, pnl_eur, pnl_pct, broker_status, broker_order_id,
                broker_updated_at, created_at)
           VALUES
               (:user_id, :trade_date, :ticker, :direction, :signal_json, :message_id, :status,
                :entry, :exit, :pnl_eur, :pnl_pct, :broker_status, :broker_order_id,
                :broker_updated_at, :created_at)""",
        {"user_id": user_id, "trade_date": trade_date, "ticker": ticker, "direction": direction,
         "signal_json": json.dumps(signal, default=str), "message_id": message_id,
         "status": status, "entry": entry, "exit": exit_price, "pnl_eur": pnl_eur,
         "pnl_pct": pnl_pct, "broker_status": broker_status, "broker_order_id": broker_order_id,
         "broker_updated_at": broker_updated_at, "created_at": created_at},
    )


def _insert_event(transaction, *, trade_id: int, user_id: int, ticker: str, trade_date: str,
                   from_status: str | None, to_status: str, ts: str,
                   broker_status: str | None = None, note: str | None = None) -> None:
    """Wie `db._log_event`, aber mit frei wählbarem Zeitstempel (historische Demo-Events) —
    `_log_event` selbst schreibt immer `_utc_timestamp()` (jetzt), das passt nicht für Trades,
    die mehrere Tage in der Vergangenheit liegen."""
    transaction.execute(
        """INSERT INTO trade_events (trade_id, user_id, ticker, trade_date,
                                      from_status, to_status, broker_status, ts, note)
           VALUES (:trade_id, :user_id, :ticker, :trade_date, :from_status, :to_status,
                   :broker_status, :ts, :note)""",
        {"trade_id": trade_id, "user_id": user_id, "ticker": ticker, "trade_date": trade_date,
         "from_status": from_status, "to_status": to_status, "broker_status": broker_status,
         "ts": ts, "note": note},
    )


def _insert_tick(transaction, *, user_id: int, ticker: str, trade_date: str, ts: str,
                  price: float, strength: float) -> None:
    transaction.execute(
        """INSERT INTO trade_ticks (user_id, trade_date, ticker, ts, price, strength)
           VALUES (:user_id, :trade_date, :ticker, :ts, :price, :strength)""",
        {"user_id": user_id, "trade_date": trade_date, "ticker": ticker, "ts": ts,
         "price": price, "strength": strength},
    )


def seed(db_path: Path) -> dict:
    """Baut die Demo-Daten unter `db_path` auf und gibt eine kleine Zusammenfassung zurück
    (für den Report/CLI-Output). Prüft den Backend-Guard erneut (nicht nur beim Modul-Import),
    damit auch ein direkter Aufruf dieser Funktion (z. B. aus Tests) nie gegen Postgres
    schreiben kann, selbst wenn `config.DB_BACKEND` sich nach dem Import geändert hat."""
    _refuse_unless_sqlite()
    resolved = db_path.resolve()
    if resolved == FORBIDDEN_DB_PATH:
        raise SystemExit(
            "ABBRUCH: Zielpfad ist die echte Entwickler-DB (data/bot.db) — das würde eine "
            "bestehende DB überschreiben. Es wurde NICHTS geschrieben. Nutze --db-path mit "
            "einer eigenen Datei (Default: data/design_seed.db)."
        )

    db.DB_FILE = resolved
    db.init_db()
    db.ensure_strategy_versions_published()   # wie beim echten App-Start (dashboard.py lifespan)

    db.get_or_create_user(USER_ID, username=USERNAME)
    db.save_profile(USER_ID, trade_size_eur=TRADE_SIZE_EUR)   # onboarding 'complete', KEINE Broker-Keys
    token = db.get_or_create_dashboard_token(USER_ID)

    today = db.today_utc_date()

    def day(offset: int) -> str:
        return (today - timedelta(days=offset)).isoformat()

    mid = 9000   # message_id-Zähler, rein kosmetisch

    with db._database().transaction() as tx:
        _wipe_user_data(tx, USER_ID)

        # ── Offene Trades (heute) ────────────────────────────────────────────────
        # Gewinn deutlich sichtbar (nicht ±0,1 %): NVDA von 120,00 auf zuletzt 138,50 (+15,4 %).
        mid += 1
        nvda_sig = _signal("NVDA", price=120.0, strength=68.0, stop_loss=110.0, take_profit=150.0)
        nvda_id = _insert_trade(
            tx, user_id=USER_ID, ticker="NVDA", trade_date=day(0), direction="long",
            status="active", signal=nvda_sig, message_id=mid, entry=120.0,
            created_at=_dt(day(0), "09:35:00"))
        _insert_event(tx, trade_id=nvda_id, user_id=USER_ID, ticker="NVDA", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "09:34:30"))
        _insert_event(tx, trade_id=nvda_id, user_id=USER_ID, ticker="NVDA", trade_date=day(0),
                      from_status="pending", to_status="active", ts=_dt(day(0), "09:35:00"))
        for ts, price, strength in (
            ("09:35:00", 120.00, 55.0), ("11:00:00", 126.40, 60.0),
            ("13:00:00", 131.75, 64.0), ("15:30:00", 138.50, 70.0),
        ):
            _insert_tick(tx, user_id=USER_ID, ticker="NVDA", trade_date=day(0),
                        ts=_dt(day(0), ts), price=price, strength=strength)

        # Verlust deutlich sichtbar: TSLA von 260,00 auf zuletzt 205,00 (-21,2 %).
        mid += 1
        tsla_sig = _signal("TSLA", price=260.0, strength=42.0, stop_loss=220.0, take_profit=300.0)
        tsla_id = _insert_trade(
            tx, user_id=USER_ID, ticker="TSLA", trade_date=day(0), direction="long",
            status="active", signal=tsla_sig, message_id=mid, entry=260.0,
            created_at=_dt(day(0), "09:40:00"))
        _insert_event(tx, trade_id=tsla_id, user_id=USER_ID, ticker="TSLA", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "09:39:30"))
        _insert_event(tx, trade_id=tsla_id, user_id=USER_ID, ticker="TSLA", trade_date=day(0),
                      from_status="pending", to_status="active", ts=_dt(day(0), "09:40:00"))
        for ts, price, strength in (
            ("09:40:00", 260.00, 50.0), ("11:00:00", 245.30, 40.0),
            ("13:00:00", 228.10, 33.0), ("15:30:00", 205.00, 25.0),
        ):
            _insert_tick(tx, user_id=USER_ID, ticker="TSLA", trade_date=day(0),
                        ts=_dt(day(0), ts), price=price, strength=strength)

        # ── Pending (unentschiedenes Signal, heute) ─────────────────────────────
        mid += 1
        amzn_sig = _signal("AMZN", price=185.0, strength=72.5)
        amzn_id = _insert_trade(
            tx, user_id=USER_ID, ticker="AMZN", trade_date=day(0), direction="long",
            status="pending", signal=amzn_sig, message_id=mid,
            created_at=_dt(day(0), "15:45:00"))
        _insert_event(tx, trade_id=amzn_id, user_id=USER_ID, ticker="AMZN", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "15:45:00"))

        # ── Abgelehnte Signale mit den echten Betriebs-Ablehngründen ────────────
        # Produktionspfad (webapp._execute_broker_order_for_web → db.mark_broker_failed):
        # der Trade wurde erst aktiviert (entry gesetzt), dann vom Risk-Gate geblockt.
        for ticker, entry_price, reason, hh in (
            ("AMD", 142.30, REASON_SPREAD_WIDE, "10:05"),
            ("MSFT", 418.20, REASON_MAX_POSITIONS, "10:10"),
            ("GOOGL", 176.40, REASON_QUOTE_STALE, "10:15"),
        ):
            mid += 1
            sig = _signal(ticker, price=entry_price, strength=58.0)
            created = _dt(day(0), f"{hh}:00")
            failed_at = _dt(day(0), f"{hh[:2]}:{int(hh[3:]) + 1:02d}:00")
            tid = _insert_trade(
                tx, user_id=USER_ID, ticker=ticker, trade_date=day(0), direction="long",
                status="broker_failed", signal=sig, message_id=mid, entry=entry_price,
                broker_status=reason, broker_updated_at=failed_at, created_at=created)
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=day(0),
                          from_status=None, to_status="pending", ts=created)
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=day(0),
                          from_status="pending", to_status="active", ts=created)
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=day(0),
                          from_status="active", to_status="broker_failed", ts=failed_at,
                          broker_status=reason)

        # Einfache manuelle Ablehnung ohne Maschinen-Grund (reject_trade-Pfad, nie aktiviert).
        mid += 1
        pltr_sig = _signal("PLTR", price=28.40, strength=39.0)
        pltr_id = _insert_trade(
            tx, user_id=USER_ID, ticker="PLTR", trade_date=day(0), direction="long",
            status="rejected", signal=pltr_sig, message_id=mid,
            created_at=_dt(day(0), "10:20:00"))
        _insert_event(tx, trade_id=pltr_id, user_id=USER_ID, ticker="PLTR", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "10:20:00"))
        _insert_event(tx, trade_id=pltr_id, user_id=USER_ID, ticker="PLTR", trade_date=day(0),
                      from_status="pending", to_status="rejected", ts=_dt(day(0), "10:23:00"))

        # ── Broker-Zwischenzustände (heute) — füllen die eigenen Home-Karten ────
        mid += 1
        shop_sig = _signal("SHOP", price=78.40, strength=61.0)
        shop_id = _insert_trade(
            tx, user_id=USER_ID, ticker="SHOP", trade_date=day(0), direction="long",
            status="broker_pending", signal=shop_sig, message_id=mid, entry=78.40,
            broker_status="accepted", broker_order_id="seed-shop-1",
            broker_updated_at=_dt(day(0), "10:31:00"), created_at=_dt(day(0), "10:30:00"))
        _insert_event(tx, trade_id=shop_id, user_id=USER_ID, ticker="SHOP", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "10:30:00"))
        _insert_event(tx, trade_id=shop_id, user_id=USER_ID, ticker="SHOP", trade_date=day(0),
                      from_status="pending", to_status="broker_pending", ts=_dt(day(0), "10:31:00"),
                      broker_status="accepted")

        mid += 1
        uber_sig = _signal("UBER", price=71.20, strength=57.0)
        uber_id = _insert_trade(
            tx, user_id=USER_ID, ticker="UBER", trade_date=day(0), direction="long",
            status="broker_closing", signal=uber_sig, message_id=mid, entry=71.20,
            broker_status="requested", broker_order_id="seed-uber-1",
            broker_updated_at=_dt(day(0), "10:41:00"), created_at=_dt(day(0), "09:31:00"))
        _insert_event(tx, trade_id=uber_id, user_id=USER_ID, ticker="UBER", trade_date=day(0),
                      from_status=None, to_status="pending", ts=_dt(day(0), "09:31:00"))
        _insert_event(tx, trade_id=uber_id, user_id=USER_ID, ticker="UBER", trade_date=day(0),
                      from_status="pending", to_status="active", ts=_dt(day(0), "09:32:00"))
        _insert_event(tx, trade_id=uber_id, user_id=USER_ID, ticker="UBER", trade_date=day(0),
                      from_status="active", to_status="broker_closing", ts=_dt(day(0), "10:41:00"),
                      broker_status="requested")

        # ── Geschlossene Trades über mehrere Tage (Verlauf/Charts) ──────────────
        # entry/exit/pnl_pct sind bei den normalen Zeilen konsistent zu TRADE_SIZE_EUR=500
        # gerechnet; die vier als Randfall markierten Zeilen sind bewusst künstlich (siehe
        # Kommentar je Zeile) und dienen dem Layout-Stresstest, nicht der Realismus-Prüfung.
        closed_rows = [
            # (ticker, offset, entry, exit, pnl_pct, pnl_eur, hinweis)
            ("AAPL", 1, 225.00, 231.75, 3.00, 15.00, None),
            ("MSFT", 1, 430.00, 410.50, -4.53, -22.67, None),
            ("GOOGL", 2, 175.00, 181.30, 3.60, 18.00, None),
            ("SUPERCALIFRAGILISTICEXPIALIDOCIOUSAG", 2, 12.50, 13.10, 4.80, 24.00,
             "randfall: sehr langer Ticker-/Firmenname"),
            ("BRKA", 3, 400000.00, 523456.78, 30.86, 123456.78,
             "randfall: sechsstelliger Betrag"),
            ("SPY", 4, 550.00, 480.00, -12.73, -87654.32,
             "randfall: negativer Extremwert"),
            ("IWM", 5, 200.00, 200.00, 0.00, 0.00, "randfall: Wert exakt 0"),
            ("NFLX", 6, 650.00, 670.00, 3.08, 15.40, None),
            ("META", 7, 590.00, 560.00, -5.08, -25.40, None),
            ("AMD", 8, 155.00, 161.00, 3.87, 19.35, None),
            ("ORCL", 9, 175.00, 168.00, -4.00, -20.00, None),
        ]
        for ticker, offset, entry_price, exit_price, pnl_pct, pnl_eur, _hint in closed_rows:
            mid += 1
            d = day(offset)
            sig = _signal(ticker, price=entry_price, strength=60.0)
            created = _dt(d, "09:31:00")
            closed_at = _dt(d, "20:00:00")
            tid = _insert_trade(
                tx, user_id=USER_ID, ticker=ticker, trade_date=d, direction="long",
                status="closed", signal=sig, message_id=mid, entry=entry_price,
                exit_price=exit_price, pnl_eur=pnl_eur, pnl_pct=pnl_pct,
                broker_updated_at=closed_at, created_at=created)
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=d,
                          from_status=None, to_status="pending", ts=_dt(d, "09:30:30"))
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=d,
                          from_status="pending", to_status="active", ts=created)
            _insert_event(tx, trade_id=tid, user_id=USER_ID, ticker=ticker, trade_date=d,
                          from_status="active", to_status="closed", ts=closed_at)

        # Ein paar Intraday-Ticks auch für geschlossene Tage (Auftrag: "inkl. trade_ticks,
        # sonst bleiben die Kurven leer") — exemplarisch für drei Tage, nicht für alle elf.
        for ticker, offset, entry_price, exit_price in (
            ("AAPL", 1, 225.00, 231.75), ("BRKA", 3, 400000.00, 523456.78),
            ("SPY", 4, 550.00, 480.00),
        ):
            d = day(offset)
            mid_price = round((entry_price + exit_price) / 2, 2)
            for ts, price in (("10:00:00", entry_price), ("14:00:00", mid_price),
                              ("19:55:00", exit_price)):
                _insert_tick(tx, user_id=USER_ID, ticker=ticker, trade_date=d,
                            ts=_dt(d, ts), price=price, strength=55.0)

        # ── Kill-Switch (Nutzer-Scope) aktiv — Warnanzeige in /app/settings ─────
        tx.execute(
            """INSERT INTO kill_switches
                   (scope, user_id, active, reason, activated_by, activated_at)
               VALUES ('user', :uid, 1, :reason, :by, :at)""",
            {"uid": USER_ID,
             "reason": "Design-Abnahme: Demo-Kill-Switch für die Warnanzeige (kein echter Vorfall)",
             "by": "tools/seed_design_data.py", "at": _dt(day(0), "08:00:00")},
        )

        # ── Bewusst leerer Bereich: Watchlist bleibt leer (users.watchlist-Default '') ──
        # → /app/watchlist zeigt den gestalteten Leerzustand.

    with db._database().transaction() as tx:
        status_counts = {
            row["status"]: row["n"]
            for row in tx.all(
                "SELECT status, COUNT(*) AS n FROM trades WHERE user_id = :uid GROUP BY status",
                {"uid": USER_ID},
            )
        }

    return {
        "db_path": resolved, "user_id": USER_ID, "token": token,
        "status_counts": status_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Befüllt eine lokale SQLite-Design-Seed-DB mit reproduzierbaren Demo-Daten.")
    parser.add_argument(
        "--db-path", type=Path, default=DEFAULT_DB_PATH,
        help=f"Zielpfad der SQLite-Datei (Default: {DEFAULT_DB_PATH}). NIEMALS data/bot.db.")
    args = parser.parse_args()

    result = seed(args.db_path)

    base_url = (config.DASHBOARD_BASE_URL or "http://localhost:8000").rstrip("/")
    login_url = f"{base_url}/auth/token?token={result['token']}"

    print(f"Design-Seed-DB geschrieben: {result['db_path']}")
    print(f"Demo-Nutzer: user_id={result['user_id']} (keine Broker-Keys, Paper/Demo-Modus)")
    print("Trades nach Status:")
    for status, n in sorted(result["status_counts"].items()):
        print(f"  {status:16s} {n}")
    print()
    print("Dashboard-Login (Token-Link, ersetzt Login-Formular):")
    print(f"  {login_url}")
    print()
    print("Hinweis: Server läuft nicht automatisch mit — der Server-Prozess (run_dashboard.py)")
    print("liest immer data/bot.db. Für eine Vorschau gegen diese Seed-Datei die DB-Datei vor")
    print("dem Start umbiegen (z. B. `import stockbot.core.db as db; db.DB_FILE = Path(...)`)")
    print("oder testweise `stockbot.core.db.DB_FILE` in einem eigenen Runner-Skript setzen —")
    print("data/bot.db selbst wird von diesem Skript nie angefasst.")


if __name__ == "__main__":
    main()
