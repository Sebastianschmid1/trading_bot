"""W3.6: strategie-spezifische Exit-Policies in evaluate_active_trade — default AUS (Tor T2).

Prüft: (1) Default AUS → Verhalten unverändert; (2) eingeschaltet → strategie-Exit greift, aber
Liquidation/Stop-Loss/Take-Profit bleiben maßgeblich (Vorrang).
"""

import pytest

from stockbot.tgbot import bot
from stockbot.market.exit_policies import ExitDecision


def _trade(entry=100.0, sl=None, tp=None, ticker="AAPL"):
    return {"ticker": ticker, "direction": "long", "entry": entry, "created_at": None,
            "signal": {"strategy": "standard", "stop_loss": sl, "take_profit": tp}}


@pytest.fixture(autouse=True)
def _no_bars(monkeypatch):
    # Bars-Abruf neutralisieren (kein Netz/Alpaca in Tests).
    class _P:
        def get_bars_batch(self, *a, **k):
            return {}
    monkeypatch.setattr(bot.provider_factory, "get_signal_provider", lambda: _P())


def test_strategy_exit_is_off_by_default(monkeypatch):
    # Default: Flag AUS → auch wenn die Policy schließen würde, bleibt der Trade offen.
    monkeypatch.setattr(bot, "STRATEGY_EXITS_ENABLED", False)
    called = {"n": 0}

    def _would_close(*a, **k):
        called["n"] += 1
        return ExitDecision(close=True, reason="X", code="x")

    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit", _would_close)
    assert bot.evaluate_active_trade(_trade(), price=100.0, strength=50.0) is None
    assert called["n"] == 0   # gar nicht erst aufgerufen


def test_strategy_exit_fires_when_enabled(monkeypatch):
    monkeypatch.setattr(bot, "STRATEGY_EXITS_ENABLED", True)
    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit",
                        lambda *a, **k: ExitDecision(close=True, reason="Momentumbruch", code="momentum_break"))
    assert bot.evaluate_active_trade(_trade(), price=100.0, strength=50.0) == "Momentumbruch"


def test_strategy_exit_holds_when_policy_holds(monkeypatch):
    monkeypatch.setattr(bot, "STRATEGY_EXITS_ENABLED", True)
    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit",
                        lambda *a, **k: ExitDecision(close=False))
    assert bot.evaluate_active_trade(_trade(), price=100.0, strength=50.0) is None


def test_stop_loss_takes_precedence_over_strategy_exit(monkeypatch):
    monkeypatch.setattr(bot, "STRATEGY_EXITS_ENABLED", True)
    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit",
                        lambda *a, **k: ExitDecision(close=True, reason="Momentumbruch", code="x"))
    # Kurs unter Stop-Loss → Stop-Loss gewinnt, Strategie-Exit wird nicht erreicht.
    assert bot.evaluate_active_trade(_trade(sl=95.0), price=94.0, strength=50.0) == "Stop-Loss 🛑"


def test_sl_tp_off_mode_skips_strategy_exit(monkeypatch):
    monkeypatch.setattr(bot, "STRATEGY_EXITS_ENABLED", True)
    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit",
                        lambda *a, **k: ExitDecision(close=True, reason="X", code="x"))
    trade = _trade()
    trade["signal"]["sl_tp_mode"] = "aus"
    assert bot.evaluate_active_trade(trade, price=100.0, strength=50.0) is None
