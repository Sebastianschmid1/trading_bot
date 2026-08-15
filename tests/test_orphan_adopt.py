"""
Tests für die Selbstheilung „im Broker offen, aber nicht im Bot":

- `reconcile.parse_occ_symbol`     — OCC-Optionssymbol zerlegen (Aktie → None).
- `db.adopt_active_trade`          — verwaiste Position als aktiven Trade übernehmen/reaktivieren.
- `reconcile.adopt_orphan_positions` — verwaiste Broker-Positionen finden und übernehmen.
- `dashboard.broker_account_snapshot` — Buying Power nur bei echter Broker-Ausführung.
- `webapp._ensure_buying_power`    — Order ablehnen, wenn Buying Power nicht reicht.

Lauf:  pytest tests/test_orphan_adopt.py   (offline; Alpaca wird gefälscht)
"""

import tempfile
from pathlib import Path

from stockbot.core import db
from stockbot.broker import reconcile

CHAT = 7777


def _fresh_db():
    d = tempfile.mkdtemp(prefix="orphan_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=1000.0)


# ── OCC-Symbol-Parsing ───────────────────────────────────────────────────────

def test_parse_occ_symbol_option():
    occ = reconcile.parse_occ_symbol("AAPL260116C00200000")
    assert occ == {"underlying": "AAPL", "expiry": "2026-01-16", "type": "call", "strike": 200.0}


def test_parse_occ_symbol_put_and_decimal_strike():
    occ = reconcile.parse_occ_symbol("SPY251219P00450500")
    assert occ["underlying"] == "SPY" and occ["type"] == "put" and occ["strike"] == 450.5


def test_parse_occ_symbol_stock_is_none():
    assert reconcile.parse_occ_symbol("AAPL") is None
    assert reconcile.parse_occ_symbol("") is None


# ── db.adopt_active_trade ────────────────────────────────────────────────────

def test_adopt_inserts_new_active_trade():
    _fresh_db()
    sig = {"strategy": "adopted", "sl_tp_mode": "aus", "leverage": 1.0, "direction": "long"}
    assert db.adopt_active_trade(CHAT, "NVDA", entry=120.0, signal=sig, filled_qty=8) is True

    t = db.get_trade(CHAT, "NVDA")
    assert t is not None and t["status"] == "active" and t["entry"] == 120.0
    assert t["broker_status"] == "adopted_orphan"
    assert any(x["ticker"] == "NVDA" for x in db.get_active_trades(CHAT))


def test_adopt_reactivates_broker_failed_row():
    # Genau der Fehlerfall: Order ging beim Broker durch, der Bot verbuchte sie als failed.
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 200.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "TSLA", status="broker_pending")
    db.mark_broker_failed(CHAT, "TSLA", broker_status="submit_failed")
    assert db.get_trade(CHAT, "TSLA")["status"] == "broker_failed"
    assert not db.get_active_trades(CHAT)

    sig = {"strategy": "adopted", "sl_tp_mode": "aus", "leverage": 1.0, "direction": "long"}
    assert db.adopt_active_trade(CHAT, "TSLA", entry=205.0, signal=sig) is True
    t = db.get_trade(CHAT, "TSLA")
    assert t["status"] == "active" and t["entry"] == 205.0
    assert t["exit"] is None and t["pnl_eur"] is None       # sauber reaktiviert


def test_adopt_skips_already_active():
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "AMD", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    # Aktiv setzen ohne yfinance-Abruf (Einstieg fix).
    with db._connect() as conn:
        conn.execute("UPDATE trades SET status='active', entry=100.0 WHERE user_id=? AND ticker=?",
                     (CHAT, "AMD"))
    sig = {"strategy": "adopted", "leverage": 1.0, "direction": "long"}
    # Schon aktiv → nicht anfassen, kein Doppel-Trade.
    assert db.adopt_active_trade(CHAT, "AMD", entry=99.0, signal=sig) is False
    assert db.get_trade(CHAT, "AMD")["entry"] == 100.0      # unverändert


# ── reconcile.adopt_orphan_positions ─────────────────────────────────────────

def test_adopt_orphan_positions_adopts_only_untracked(monkeypatch):
    _fresh_db()
    # Bot führt MSFT bereits aktiv; NVDA ist eine verwaiste Broker-Position.
    db.add_pending(CHAT, {"ticker": "MSFT", "direction": "long", "price": 300.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    with db._connect() as conn:
        conn.execute("UPDATE trades SET status='active', entry=300.0 WHERE user_id=? AND ticker=?",
                     (CHAT, "MSFT"))

    positions = [
        {"symbol": "MSFT", "qty": 3.0, "avg_entry": 300.0, "side": "long"},   # getrackt → übersprungen
        {"symbol": "NVDA", "qty": 8.0, "avg_entry": 120.0, "side": "long"},   # verwaist → adopt
    ]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: positions)

    res = reconcile.adopt_orphan_positions(db.get_user(CHAT), client=object())
    adopted = {a["ticker"] for a in res["adopted"]}
    assert adopted == {"NVDA"}                      # nur die verwaiste Position übernommen
    assert all(t["ticker"] != "MSFT" or t["status"] == "active" for t in db.get_active_trades(CHAT))
    assert len([t for t in db.get_active_trades(CHAT) if t["ticker"] == "MSFT"]) == 1   # kein Doppel

    nvda = db.get_trade(CHAT, "NVDA")
    assert nvda["status"] == "active" and nvda["entry"] == 120.0
    assert nvda["broker_filled_qty"] == 8.0


def test_adopt_orphan_option_position(monkeypatch):
    _fresh_db()
    positions = [{"symbol": "AAPL260116C00200000", "qty": 2.0, "avg_entry": 4.5, "side": "long"}]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: positions)
    monkeypatch.setattr(reconcile, "get_current_price", lambda tkr, fb=0.0: 200.0)  # Underlying

    res = reconcile.adopt_orphan_positions(db.get_user(CHAT), client=object())
    assert [a["ticker"] for a in res["adopted"]] == ["AAPL"]
    t = db.get_trade(CHAT, "AAPL")
    sig = t["signal"]
    assert sig["option_symbol"] == "AAPL260116C00200000"
    assert sig["entry_premium"] == 4.5 and sig["contracts"] == 2 and sig["strike"] == 200.0
    # entry ist der UNDERLYING-Kurs (200), NICHT die Prämie (4.5) — sonst absurde P&L-Anzeige.
    assert t["entry"] == 200.0


def test_heal_adopted_option_entry_fixes_premium_as_entry(monkeypatch):
    _fresh_db()
    # Fehlerhafter Altdatensatz: entry == Prämie (0.35) statt Underlying-Kurs.
    sig = {"option_symbol": "BAX260116C00021000", "entry_premium": 0.35, "contracts": 1,
           "omega": 1.0, "strategy": "adopted", "direction": "long",
           "sl_tp_mode": "aus", "leverage": 1.0}
    db.adopt_active_trade(CHAT, "BAX", entry=0.35, signal=sig, filled_qty=1.0)
    assert db.get_trade(CHAT, "BAX")["entry"] == 0.35

    monkeypatch.setattr(reconcile, "get_current_price", lambda tkr, fb=0.0: 21.0)
    fixed = reconcile.heal_adopted_option_entries(db.get_user(CHAT))
    assert fixed == ["BAX"]
    assert db.get_trade(CHAT, "BAX")["entry"] == 21.0          # auf Underlying korrigiert
    assert db.get_trade(CHAT, "BAX")["signal"]["entry_premium"] == 0.35   # Prämie bleibt

    # Idempotent: zweiter Lauf ändert nichts mehr (entry ist keine Prämie mehr).
    assert reconcile.heal_adopted_option_entries(db.get_user(CHAT)) == []


def test_heal_ignores_plain_stock_trades(monkeypatch):
    _fresh_db()
    db.adopt_active_trade(CHAT, "MSFT", entry=300.0,
                          signal={"strategy": "adopted", "direction": "long"}, filled_qty=3.0)
    monkeypatch.setattr(reconcile, "get_current_price", lambda tkr, fb=0.0: 999.0)
    assert reconcile.heal_adopted_option_entries(db.get_user(CHAT)) == []
    assert db.get_trade(CHAT, "MSFT")["entry"] == 300.0       # unverändert


def test_regression_broker_has_position_bot_does_not(monkeypatch):
    """End-to-end: Order beim Broker durch, Bot verbuchte sie als failed → Diskrepanz.
    Nach einem Monitor-Lauf (adopt) ist der Trade wieder im Bot und der Abgleich sauber."""
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "TSLA", "direction": "long", "price": 200.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "TSLA", status="broker_pending")
    db.mark_broker_failed(CHAT, "TSLA", broker_status="submit_failed")

    positions = [{"symbol": "TSLA", "qty": 5.0, "avg_entry": 201.0, "side": "long"}]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: positions)
    user = db.get_user(CHAT)

    # Vor der Übernahme: Bot kennt keinen aktiven Trade, Broker schon → Abweichung.
    before = reconcile.reconcile_user(user, client=object())
    assert before["only_broker"] == ["TSLA"] and not before["ok"]

    reconcile.adopt_orphan_positions(user, client=object())

    after = reconcile.reconcile_user(user, client=object())
    assert after["ok"] and not after["only_broker"] and not after["only_bot"]
    assert db.get_trade(CHAT, "TSLA")["status"] == "active"


# ── Alpaca-Symbolmapping Klassen-Suffixe (BRK-B ↔ BRK.B) ─────────────────────
#
# Alpaca meldet Positionen/Orders für Klassen-Suffix-Aktien mit Punkt ("BRK.B"), der Bot führt
# den Trade intern mit Bindestrich ("BRK-B", Yahoo-Konvention, siehe market/universes.py). Diese
# Tests rufen bewusst NICHT `reconcile.broker.list_positions` direkt gemockt auf, sondern gehen
# über einen Fake-*Alpaca*-Client (wie tests/test_broker.py), damit die echte Rückrichtung in
# `stockbot/broker/client.py::list_positions` mit ausgeführt wird — sonst würde ein zu früh
# gemocktes `list_positions` die eigentlich zu testende Umschreibung umgehen.

class _FakeAlpacaPosition:
    def __init__(self, symbol, qty="1", avg_entry_price="300.0", unrealized_pl="0.0"):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.unrealized_pl = unrealized_pl


class _FakeAlpacaClient:
    """Steht für den Alpaca TradingClient — nur `get_all_positions` wird für diese Tests gebraucht."""
    def __init__(self, positions):
        self._positions = positions

    def get_all_positions(self):
        return self._positions


def test_reconcile_user_matches_alpaca_dot_symbol_to_bot_hyphen_trade():
    """AC3: Der Bot führt BRK-B aktiv, Alpaca meldet die Position als BRK.B — nach der
    Rückrichtung matcht der Abgleich sauber (kein only_bot/only_broker-Diff)."""
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "BRK-B", "direction": "long", "price": 300.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    with db._connect() as conn:
        conn.execute("UPDATE trades SET status='active', entry=300.0 WHERE user_id=? AND ticker=?",
                     (CHAT, "BRK-B"))

    client = _FakeAlpacaClient([_FakeAlpacaPosition("BRK.B")])
    diff = reconcile.reconcile_user(db.get_user(CHAT), client=client)
    assert diff["ok"] is True
    assert diff["only_bot"] == [] and diff["only_broker"] == []


def test_sweep_missing_positions_does_not_close_bot_hyphen_trade_reported_as_alpaca_dot():
    """AC3: sweep_missing_positions darf den aktiven BRK-B-Trade NICHT als 'verkauft' schließen,
    nur weil Alpaca die (weiterhin offene) Position als BRK.B meldet — keine falsche
    Adoption/Fehlschließung durch einen Symbolformat-Mismatch."""
    _fresh_db()
    _seed_active(["BRK-B"])
    client = _FakeAlpacaClient([_FakeAlpacaPosition("BRK.B")])
    res = reconcile.sweep_missing_positions({"user_id": CHAT, "trade_size_eur": 1000.0},
                                            client=client, grace_sec=0)
    assert res["closed"] == []
    assert {t["ticker"] for t in db.get_active_trades(CHAT)} == {"BRK-B"}


def test_adopt_orphan_position_reported_as_alpaca_dot_symbol_adopts_as_bot_hyphen_ticker():
    """Ergänzend: eine wirklich verwaiste BRK.B-Position wird als Bot-Ticker BRK-B übernommen
    (nicht als 'BRK.B' — das würde nicht zum intern verwendeten Yahoo-Ticker passen)."""
    _fresh_db()
    client = _FakeAlpacaClient([_FakeAlpacaPosition("BRK.B", qty="2", avg_entry_price="300.0")])
    res = reconcile.adopt_orphan_positions(db.get_user(CHAT), client=client)
    assert [a["ticker"] for a in res["adopted"]] == ["BRK-B"]
    t = db.get_trade(CHAT, "BRK-B")
    assert t is not None and t["status"] == "active" and t["entry"] == 300.0


# ── Sweep-Guard gegen Massen-Fehlschließung (transiente Broker-Fehler) ────────

def _seed_active(tickers):
    sig = {"strategy": "standard", "sl_tp_mode": "aus", "leverage": 1.0, "direction": "long", "price": 100.0}
    for tk in tickers:
        db.adopt_active_trade(CHAT, tk, entry=100.0, signal=sig, filled_qty=1)


def test_sweep_skips_when_broker_list_empty(monkeypatch):
    """Liefert der Broker (transient) KEINE Positionen, obwohl der Bot aktive Trades hält, darf
    der Sweep NICHTS schließen — sonst Massen-Fehlschließung + Adopt-Ping-Pong (der reale Bug)."""
    _fresh_db()
    _seed_active(["AAA", "BBB", "CCC", "DDD"])
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: [])   # API-Glitch → []
    res = reconcile.sweep_missing_positions({"user_id": CHAT, "trade_size_eur": 1000.0},
                                            client=object(), grace_sec=0)
    assert res["closed"] == []
    assert len(db.get_active_trades(CHAT)) == 4                    # alle bleiben aktiv
    assert all(s["reason"] == "broker_list_unreliable" for s in res["skipped"])


def test_sweep_still_closes_single_genuine_missing(monkeypatch):
    """Einzeln fehlende Position (Broker liefert die übrigen) wird weiterhin normal geschlossen —
    sofern die Einzelabfrage sie als eindeutig weg (False) bestätigt."""
    _fresh_db()
    _seed_active(["AAA", "BBB", "CCC", "DDD"])
    present = [{"symbol": s, "qty": 1.0, "avg_entry": 100.0, "side": "long"} for s in ("AAA", "BBB", "CCC")]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: present)  # DDD fehlt echt
    monkeypatch.setattr(reconcile.broker, "position_exists", lambda sym, c=None: False)  # bestätigt weg
    monkeypatch.setattr(reconcile, "get_current_price", lambda tk, fb=0.0: 100.0)
    res = reconcile.sweep_missing_positions({"user_id": CHAT, "trade_size_eur": 1000.0},
                                            client=object(), grace_sec=0)
    assert [c["ticker"] for c in res["closed"]] == ["DDD"]
    assert {t["ticker"] for t in db.get_active_trades(CHAT)} == {"AAA", "BBB", "CCC"}


def test_sweep_skips_when_single_symbol_still_confirmed(monkeypatch):
    """`list_positions` lässt ein Symbol aus (Lücke), die Einzelabfrage bestätigt es aber als
    vorhanden (True) → NICHT schließen (verhindert den Close↔Adopt-Ping-Pong bei 1–2 Flackerern)."""
    _fresh_db()
    _seed_active(["AAA", "BBB", "CCC", "DDD"])
    present = [{"symbol": s, "qty": 1.0, "avg_entry": 100.0, "side": "long"} for s in ("AAA", "BBB", "CCC")]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: present)   # DDD scheinbar weg
    monkeypatch.setattr(reconcile.broker, "position_exists", lambda sym, c=None: True)  # existiert doch
    res = reconcile.sweep_missing_positions({"user_id": CHAT, "trade_size_eur": 1000.0},
                                            client=object(), grace_sec=0)
    assert res["closed"] == []
    assert len(db.get_active_trades(CHAT)) == 4
    assert any(s.get("reason") == "broker_still_reports_or_unknown" for s in res["skipped"])


def test_sweep_skips_when_single_symbol_check_unknown(monkeypatch):
    """Ist die Einzelabfrage unklar (None = transienter Fehler), NICHT schließen (kein Verkauf
    ohne Bestätigung)."""
    _fresh_db()
    _seed_active(["AAA", "BBB", "CCC", "DDD"])
    present = [{"symbol": s, "qty": 1.0, "avg_entry": 100.0, "side": "long"} for s in ("AAA", "BBB", "CCC")]
    monkeypatch.setattr(reconcile.broker, "list_positions", lambda *a, **k: present)
    monkeypatch.setattr(reconcile.broker, "position_exists", lambda sym, c=None: None)  # unklar
    res = reconcile.sweep_missing_positions({"user_id": CHAT, "trade_size_eur": 1000.0},
                                            client=object(), grace_sec=0)
    assert res["closed"] == []
    assert len(db.get_active_trades(CHAT)) == 4


# ── Stale-Pending-Bereinigung (Pending-Stau-Prävention) ──────────────────────

def test_expire_stale_pending_only_past_days(monkeypatch):
    """`expire_stale_pending` läuft NUR Pending vergangener Handelstage ab; heutige bleiben."""
    _fresh_db()
    monkeypatch.setattr(db, "_today", lambda: "2000-01-01")             # „gestriges" Pending
    db.add_pending(CHAT, {"ticker": "OLD", "direction": "long", "price": 10.0}, 0)
    monkeypatch.undo()                                                  # zurück auf echtes Heute
    db.add_pending(CHAT, {"ticker": "NEW", "direction": "long", "price": 10.0}, 0)

    n = db.expire_stale_pending()
    assert n == 1                                                       # nur OLD abgelaufen
    assert {t["ticker"] for t in db.get_pending_trades(CHAT)} == {"NEW"}  # heutiges bleibt pending
    assert db.expire_stale_pending() == 0                              # idempotent


# ── Dashboard Buying Power ───────────────────────────────────────────────────

def test_broker_account_snapshot_none_without_broker_exec():
    from stockbot.web import dashboard
    assert dashboard.broker_account_snapshot({"user_id": CHAT, "broker_exec": False}) is None


def test_broker_account_snapshot_returns_account(monkeypatch):
    from stockbot.web import dashboard
    monkeypatch.setattr(dashboard.config, "ALPACA_ENABLED", True)
    monkeypatch.setattr(dashboard.broker, "_get_client", lambda *a, **k: object())
    monkeypatch.setattr(dashboard.broker, "account_summary",
                        lambda c: {"ok": True, "buying_power": 5000.0, "cash": 3000.0, "paper": True})
    snap = dashboard.broker_account_snapshot({"user_id": CHAT, "broker_exec": True,
                                              "broker_platform": None})
    assert snap["buying_power"] == 5000.0 and snap["cash"] == 3000.0


# ── Buying-Power-Schutz (Order ablehnen) ─────────────────────────────────────

def test_ensure_buying_power_rejects_when_insufficient(monkeypatch):
    _fresh_db()
    from stockbot.web import webapp
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 1000.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    db.activate_trade(CHAT, "NVDA", status="broker_pending")
    monkeypatch.setattr(webapp.broker, "account_summary",
                        lambda c: {"ok": True, "buying_power": 100.0})

    plan = {"kind": "shares", "qty": 5, "notional": None, "fractional": False}
    res = webapp._ensure_buying_power(db.get_user(CHAT), "NVDA", client=object(),
                                      plan=plan, entry=1000.0)
    assert res is not None and not res["ok"]
    assert db.get_trade(CHAT, "NVDA")["status"] == "broker_failed"


def test_ensure_buying_power_allows_when_enough(monkeypatch):
    _fresh_db()
    from stockbot.web import webapp
    monkeypatch.setattr(webapp.broker, "account_summary",
                        lambda c: {"ok": True, "buying_power": 100000.0})
    plan = {"kind": "shares", "qty": 5, "notional": None, "fractional": False}
    assert webapp._ensure_buying_power(db.get_user(CHAT), "NVDA", client=object(),
                                       plan=plan, entry=1000.0) is None
