"""Der ATR-Trailing-Stop muss live ueberhaupt ausloesen koennen.

Ausgangslage (Befund 23.08.2026): `market/exit_policies._trailing_stop` faellt auf HOLD zurueck,
sobald `highest_price_since_entry` oder `atr` fehlen — und `tgbot/bot._strategy_exit_reason`
uebergab beides nicht. Der Trailing-Stop war damit strukturell tot, obwohl das Strategie-Labor
`trail_mult` optimiert und seine Backtest-Erwartung den Trailing-Stop einrechnet. Genau diese
Luecke erklaert einen Teil der Divergenz zwischen Backtest-Erwartung und Live-Ergebnis.

Diese Tests halten die geschlossene Luecke fest: der Hoechstkurs wird persistiert und monoton
fortgeschrieben, und er erreicht die Policy zusammen mit dem ATR.
"""

from pathlib import Path

import pytest

from stockbot.core import db
from stockbot.market import exit_policies
from stockbot.tgbot import bot


USER = 4242


def _fresh_db(tmp_path: Path):
    db.DB_FILE = tmp_path / "trailing.db"
    db.init_db()
    db.get_or_create_user(USER, "trailing-tester")


def _active_trade(ticker: str = "AAPL", entry: float = 100.0) -> dict:
    db.add_pending(USER, {"ticker": ticker, "direction": "long", "strategy": "ai_adaptive",
                          "price": entry, "atr": 2.5},
                   message_id=1)
    return db.activate_trade(USER, ticker)


# ── Persistenz des Hoechstkurses ────────────────────────────────────────────

def test_high_water_rises_and_never_falls_back(tmp_path):
    _fresh_db(tmp_path)
    _active_trade()

    assert db.update_high_water(USER, "AAPL", 101.0) == 101.0
    assert db.update_high_water(USER, "AAPL", 107.5) == 107.5
    # Ein Rueckgang darf den Hoechstkurs nicht zuruecksetzen — sonst wandert der
    # Trailing-Stop mit dem fallenden Kurs nach unten und loest nie aus.
    assert db.update_high_water(USER, "AAPL", 99.0) == 107.5


def test_high_water_ignores_missing_price(tmp_path):
    _fresh_db(tmp_path)
    _active_trade()
    db.update_high_water(USER, "AAPL", 105.0)
    assert db.update_high_water(USER, "AAPL", None) is None       # kein Schreibvorgang
    assert db.update_high_water(USER, "AAPL", 100.0) == 105.0     # Wert unveraendert


def test_high_water_reaches_the_trade_dict(tmp_path):
    _fresh_db(tmp_path)
    _active_trade()
    db.update_high_water(USER, "AAPL", 112.0)
    trades = db.get_active_trades(USER)
    assert [t["high_water"] for t in trades] == [112.0]


# ── Durchreichung bis in die Policy ─────────────────────────────────────────

def test_strategy_exit_passes_high_water_and_atr_to_the_policy(monkeypatch):
    """Der eigentliche Regressionsschutz: beide Eingaben muessen ankommen."""
    seen = {}

    def _capture(strategy_key, **inputs):
        seen.update(inputs)
        return exit_policies.ExitDecision(close=False, reason="", code="hold")

    monkeypatch.setattr(bot.exit_policies, "evaluate_strategy_exit", _capture)

    class _NoBars:
        def get_bars_batch(self, *a, **k):
            return {}

    monkeypatch.setattr(bot.provider_factory, "get_signal_provider", lambda: _NoBars())

    trade = {"ticker": "AAPL", "direction": "long", "entry": 100.0, "created_at": None,
             "high_water": 118.0,
             "signal": {"strategy": "ai_adaptive", "atr": 2.5}}
    bot._strategy_exit_reason(trade, price=110.0)

    assert seen["highest_price_since_entry"] == 118.0
    assert seen["atr"] == 2.5


def test_trailing_stop_actually_fires_with_the_wired_inputs(monkeypatch):
    """Ende zu Ende ohne Attrappe der Policy: 118 Hoch, ATR 2,5, trail 3,0 → Schwelle 110,5."""
    class _NoBars:
        def get_bars_batch(self, *a, **k):
            return {}

    monkeypatch.setattr(bot.provider_factory, "get_signal_provider", lambda: _NoBars())
    trade = {"ticker": "AAPL", "direction": "long", "entry": 100.0, "created_at": None,
             "high_water": 118.0,
             "signal": {"strategy": "ai_adaptive", "atr": 2.5}}

    assert bot._strategy_exit_reason(trade, price=110.0) is not None   # unter der Schwelle
    assert bot._strategy_exit_reason(trade, price=111.0) is None       # darueber: halten

    # Gegenprobe auf die alte Lage: ohne Hoechstkurs bleibt es immer bei HOLD.
    ohne = dict(trade, high_water=None)
    assert bot._strategy_exit_reason(ohne, price=1.0) is None


# ── Dispatch vertraegt den gemeinsamen Eingabesatz ──────────────────────────

def test_dispatch_drops_inputs_the_target_policy_does_not_accept():
    """`mean_reversion_exit` kennt weder `atr` noch `minutes_to_close`.

    Vor dem Filter warf der gemeinsame Aufruf dort einen TypeError, den der Aufrufer als
    "kein Exit" verschluckte — die Policy von `bb_revert` (Produktionsstrategie) haette sich
    beim Scharfschalten also lautlos nie gemeldet.
    """
    decision = exit_policies.evaluate_strategy_exit(
        "bb_revert",
        current_price=100.0,
        entry_price=95.0,
        highest_price_since_entry=120.0,
        atr=2.0,
        minutes_to_close=30.0,
        bars=None,
        opened_at=None,
        now=None,
    )
    assert decision.code != "no_policy"     # die mean_reversion-Policy wurde erreicht


@pytest.mark.parametrize("strategy_key,expected_family_reached", [
    ("ai_adaptive", True),      # swing_trend
    ("standard", True),         # intraday_momentum
])
def test_dispatch_still_reaches_the_trailing_families(strategy_key, expected_family_reached):
    decision = exit_policies.evaluate_strategy_exit(
        strategy_key,
        current_price=100.0,
        highest_price_since_entry=120.0,
        atr=2.0,
        entry_price=95.0,
        minutes_to_close=300.0,
        bars=None, opened_at=None, now=None,
    )
    # 120 − 3,0 × 2,0 = 114 > 100 → der Trailing-Stop greift und meldet seinen Code.
    assert (decision.code == "trailing_stop") is expected_family_reached
