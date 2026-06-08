"""
Tests für SL/TP-Modi, Hebel/Liquidation, P&L-Berechnung, Auto-Close mit Hebel
und die zugehörigen DB-Einstellungen. Offline.

Lauf:  python test_trading.py   oder   pytest test_trading.py
"""

import sys
import tempfile
from pathlib import Path

import db
import bot
import analyzer
import evaluator

CHAT = 8484


def fresh_db():
    d = tempfile.mkdtemp(prefix="tradetest_")
    db.DB_FILE = Path(d) / "test.db"
    db.init_db()


# ── SL/TP-Modi ───────────────────────────────────────────────────────────────

def test_sl_tp_modes_ordering():
    p = analyzer.sl_tp_from_atr(100.0, 2.0, "passiv")
    n = analyzer.sl_tp_from_atr(100.0, 2.0, "normal")
    a = analyzer.sl_tp_from_atr(100.0, 2.0, "aggressiv")
    # passiv = engster Stop / kleinstes Ziel, aggressiv = weitester Stop / größtes Ziel
    assert p["stop_loss"] > n["stop_loss"] > a["stop_loss"]
    assert p["take_profit"] < n["take_profit"] < a["take_profit"]
    assert n["stop_loss"] == 97.0 and n["take_profit"] == 105.0


def test_sl_tp_without_atr_is_none():
    r = analyzer.sl_tp_from_atr(100.0, None, "normal")
    assert r["stop_loss"] is None and r["take_profit"] is None


def test_sl_tp_mode_aus_has_no_limits():
    # Modus "aus": auch MIT gültigem ATR keine SL/TP-Grenzen
    r = analyzer.sl_tp_from_atr(100.0, 2.0, "aus")
    assert r["stop_loss"] is None and r["take_profit"] is None and r["risk_reward"] is None


def test_auto_close_aus_holds_without_sl_tp():
    # Modus "aus" → ohne Hebel kein Schließen bei normalem Kurs, auch tief im Minus
    t = {"ticker": "NVDA", "direction": "long", "entry": 100.0,
         "signal": {"stop_loss": None, "take_profit": None, "leverage": 1.0}}
    assert bot.evaluate_active_trade(t, price=70.0, strength=70.0) is None
    # aber Signal-Verfall schließt weiterhin
    assert "verschlechtert" in bot.evaluate_active_trade(t, price=70.0, strength=20.0)


# ── Hebel & Liquidation ──────────────────────────────────────────────────────

def test_liquidation_price():
    assert evaluator.liquidation_price(100.0, 10, "long") == 90.0
    assert evaluator.liquidation_price(100.0, 2, "long") == 50.0
    assert evaluator.liquidation_price(100.0, 1, "long") is None     # kein Hebel → keine Liquidation


def test_realized_pnl_scales_with_leverage():
    pct, eur = evaluator.realized_pnl(100.0, 110.0, "long", 25.0, leverage=3)
    assert round(pct, 2) == 10.0
    assert round(eur, 2) == 7.5                                       # 25 € × 10 % × 3
    # Verlust ist auf die Margin begrenzt (mehr kann man nicht verlieren)
    _, eur2 = evaluator.realized_pnl(100.0, 50.0, "long", 25.0, leverage=5)
    assert eur2 == -25.0


# ── Auto-Close inkl. Liquidation (Hebel hat Vorrang vor SL) ──────────────────

def _trade(entry=100.0, sl=85.0, tp=120.0, leverage=10.0):
    return {"ticker": "NVDA", "direction": "long", "entry": entry,
            "signal": {"stop_loss": sl, "take_profit": tp, "leverage": leverage}}


def test_auto_close_liquidation_before_stop():
    # Hebel 10 → Liquidation bei 90; Stop liegt weiter unten (85) → Liquidation zuerst
    assert "Liquidation" in bot.evaluate_active_trade(_trade(), price=89.0, strength=70.0)


def test_auto_close_stop_loss_without_leverage():
    assert "Stop-Loss" in bot.evaluate_active_trade(_trade(leverage=1.0), price=84.0, strength=70.0)


def test_auto_close_take_profit():
    assert "Take-Profit" in bot.evaluate_active_trade(_trade(leverage=1.0), price=121.0, strength=70.0)


def test_auto_close_holds_when_fine():
    assert bot.evaluate_active_trade(_trade(leverage=1.0), price=101.0, strength=70.0) is None


# ── DB-Einstellungen ─────────────────────────────────────────────────────────

def test_settings_defaults_and_setters():
    fresh_db()
    u = db.get_or_create_user(CHAT, "t")
    assert u["sl_tp_mode"] == "normal" and u["leverage"] == 1.0 and u["auto_accept"] is False

    db.set_sl_tp_mode(CHAT, "aggressiv")
    db.set_leverage(CHAT, 5)
    db.set_auto_accept(CHAT, True)
    u = db.get_user(CHAT)
    assert u["sl_tp_mode"] == "aggressiv" and u["leverage"] == 5.0 and u["auto_accept"] is True

    db.set_leverage(CHAT, 999)            # Clamp auf max 20
    assert db.get_user(CHAT)["leverage"] == 20.0


def test_set_trade_leverage_updates_pending():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 100.0, "leverage": 1.0}, 1)
    db.set_trade_leverage(CHAT, "AAPL", 3.0)
    assert db.get_trade(CHAT, "AAPL")["signal"]["leverage"] == 3.0


def test_get_pending_trades():
    fresh_db()
    db.get_or_create_user(CHAT)
    db.add_pending(CHAT, {"ticker": "AAPL", "direction": "long", "price": 1.0}, 1)
    db.add_pending(CHAT, {"ticker": "MSFT", "direction": "long", "price": 1.0}, 2)
    assert len(db.get_pending_trades(CHAT)) == 2


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
