"""
Tests für den Demo-Reset (db.reset_user_trades), die Options-Anreicherung eines Trades
(services.trades.attach_option_for_trade) und die Kontraktwahl nach Ziel-Hebel
(broker.select_option_for_leverage, mit gefälschten Alpaca-Daten).

Lauf:  pytest tests/test_reset_and_options.py   (offline)
"""

import tempfile
from pathlib import Path

from stockbot.core import db
from stockbot.services import trades as trade_svc
from stockbot.broker import client as broker

CHAT = 4242


def _fresh_db():
    d = tempfile.mkdtemp(prefix="resettest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()
    db.get_or_create_user(CHAT, "tester")
    db.save_profile(CHAT, trade_size_eur=1000.0)


# ── Reset ─────────────────────────────────────────────────────────────────────

def test_reset_user_trades_clears_trades_and_keeps_user():
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "NVDA", "direction": "long", "price": 100.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    assert db.get_trade(CHAT, "NVDA") is not None

    n = db.reset_user_trades(CHAT)
    assert n >= 1
    assert db.get_trade(CHAT, "NVDA") is None          # Trades weg
    assert db.get_user(CHAT) is not None               # Nutzer bleibt
    assert db.get_user(CHAT)["trade_size_eur"] == 1000.0


# ── Options-Anreicherung eines aktiven Trades ────────────────────────────────

def test_attach_option_for_trade_writes_contract_into_signal():
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 200.0,
                          "leverage": 5.0, "strength": 70.0}, 1)
    # Aktiv setzen (entry direkt, ohne yfinance)
    with db._connect() as conn:
        conn.execute("UPDATE trades SET status='active', entry=200.0 WHERE user_id=? AND ticker=?",
                     (CHAT, "AAPL"))
    user = db.get_user(CHAT)
    trade = db.get_trade(CHAT, "AAPL")

    def selector(budget, target_leverage):
        assert budget == 1000.0 and target_leverage == 5.0
        return {"option_symbol": "AAPL260116C00200000", "strike": 200.0, "expiry": "2026-01-16",
                "premium": 4.0, "delta": 0.55, "omega": 5.1, "qty": 2}

    extra = trade_svc.attach_option_for_trade(user, trade, selector)
    assert extra["option_symbol"] == "AAPL260116C00200000" and extra["contracts"] == 2

    stored = db.get_trade(CHAT, "AAPL")["signal"]
    assert stored["option_symbol"] == "AAPL260116C00200000"
    assert stored["entry_premium"] == 4.0 and stored["omega"] == 5.1


def test_attach_option_skips_when_leverage_one():
    _fresh_db()
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 200.0,
                          "leverage": 1.0, "strength": 70.0}, 1)
    with db._connect() as conn:
        conn.execute("UPDATE trades SET status='active', entry=200.0 WHERE user_id=? AND ticker=?",
                     (CHAT, "AAPL"))
    user, trade = db.get_user(CHAT), db.get_trade(CHAT, "AAPL")
    called = {"n": 0}

    def selector(budget, target):
        called["n"] += 1
        return None

    assert trade_svc.attach_option_for_trade(user, trade, selector) is None
    assert called["n"] == 0   # bei Hebel 1 wird gar nicht erst gewählt


# ── Kontraktwahl nach Ziel-Hebel (Omega am nächsten) ─────────────────────────

def test_select_option_for_leverage_picks_closest_omega(monkeypatch):
    # Drei (bezahlbare) Kontrakte; Omega = |delta|*price/premium bei Kurs 200, Budget 1000.
    # Ziel-Hebel 5 → der mit Omega≈5 gewinnt.
    contracts = [
        {"symbol": "C190", "strike": 190.0, "expiry": "2026-01-16"},   # ITM
        {"symbol": "C200", "strike": 200.0, "expiry": "2026-01-16"},   # ATM ← Omega 5
        {"symbol": "C220", "strike": 220.0, "expiry": "2026-01-16"},   # OTM
    ]
    snaps = {
        "C190": {"ok": True, "premium": 8.0, "delta": 0.80},   # omega = 0.80*200/8  = 20
        "C200": {"ok": True, "premium": 10.0, "delta": 0.25},  # omega = 0.25*200/10 = 5  ← Ziel
        "C220": {"ok": True, "premium": 2.0,  "delta": 0.10},  # omega = 0.10*200/2  = 10
    }
    monkeypatch.setattr(broker, "list_option_contracts", lambda *a, **k: contracts)
    monkeypatch.setattr(broker, "get_option_snapshot", lambda sym, **k: snaps[sym])

    best = broker.select_option_for_leverage("AAPL", 200.0, target_leverage=5.0, budget=1000.0)
    assert best["option_symbol"] == "C200"
    assert best["omega"] == 5.0
    assert best["qty"] == 1   # 1000 // (10*100) = 1


def test_select_option_skips_unaffordable_contracts(monkeypatch):
    contracts = [{"symbol": "CHEAP", "strike": 200.0, "expiry": "2026-01-16"}]
    # premium*100 = 400 ≤ 1000 → bezahlbar, qty = 2
    monkeypatch.setattr(broker, "list_option_contracts", lambda *a, **k: contracts)
    monkeypatch.setattr(broker, "get_option_snapshot",
                        lambda sym, **k: {"ok": True, "premium": 4.0, "delta": 0.5})
    best = broker.select_option_for_leverage("AAPL", 200.0, target_leverage=5.0, budget=1000.0)
    assert best is not None and best["qty"] == 2


def test_select_option_none_when_no_chain(monkeypatch):
    monkeypatch.setattr(broker, "list_option_contracts", lambda *a, **k: [])
    assert broker.select_option_for_leverage("XYZ", 50.0, 5.0, 1000.0) is None
