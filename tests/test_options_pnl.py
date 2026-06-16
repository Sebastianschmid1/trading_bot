"""
Tests für die optionsbewusste P&L (evaluator.trade_pnl / option_pnl) und den Positions-Abgleich
(reconcile.diff_positions).

Lauf:  pytest tests/test_options_pnl.py
"""

from stockbot.core import evaluator
from stockbot.broker import reconcile


# ── P&L: Aktien (linear) vs. Optionen (Prämie / Omega-Näherung) ──────────────

def test_trade_pnl_shares_linear_with_leverage():
    trade = {"entry": 100.0, "direction": "long", "signal": {"leverage": 3.0}}
    pct, eur = evaluator.trade_pnl(trade, 110.0, 1000.0)   # +10% × Hebel 3
    assert round(pct, 2) == 10.0
    assert round(eur, 2) == 300.0


def test_trade_pnl_option_live_premium_nonlinear():
    # 2 Kontrakte, Einstiegsprämie 4.00 → jetzt 6.50 : (6.50-4.00)*100*2 = 500
    sig = {"option_symbol": "X", "entry_premium": 4.0, "contracts": 2, "omega": 5.0, "leverage": 5.0}
    trade = {"entry": 200.0, "direction": "long", "signal": sig}
    pct, eur = evaluator.trade_pnl(trade, 210.0, 1000.0, current_premium=6.5)
    assert round(eur, 2) == 500.0


def test_trade_pnl_option_loss_capped_at_premium():
    sig = {"option_symbol": "X", "entry_premium": 4.0, "contracts": 2, "omega": 5.0}
    trade = {"entry": 200.0, "direction": "long", "signal": sig}
    # Prämie auf 0 → maximaler Verlust = gezahlte Prämie = 4.00*100*2 = 800
    _, eur = evaluator.trade_pnl(trade, 150.0, 1000.0, current_premium=0.0)
    assert round(eur, 2) == -800.0


def test_trade_pnl_option_omega_approximation_without_premium():
    sig = {"option_symbol": "X", "entry_premium": 4.0, "contracts": 2, "omega": 5.0}
    trade = {"entry": 200.0, "direction": "long", "signal": sig}
    # Kein Live-Premium → Omega-Näherung: 1000 * (+5%/100) * 5 = 250
    pct, eur = evaluator.trade_pnl(trade, 210.0, 1000.0)
    assert round(pct, 2) == 5.0
    assert round(eur, 2) == 250.0


def test_trade_pnl_option_omega_loss_capped_at_budget():
    sig = {"option_symbol": "X", "entry_premium": 4.0, "contracts": 2, "omega": 5.0}
    trade = {"entry": 200.0, "direction": "long", "signal": sig}
    # -20% × Omega 5 = -100% → auf Budget begrenzt (-1000)
    _, eur = evaluator.trade_pnl(trade, 160.0, 1000.0)
    assert round(eur, 2) == -1000.0


# ── Reconciliation ───────────────────────────────────────────────────────────

def test_diff_positions_match():
    d = reconcile.diff_positions({"AAPL", "MSFT"}, {"MSFT", "AAPL"})
    assert d["ok"] and not d["only_bot"] and not d["only_broker"]


def test_diff_positions_mismatch():
    d = reconcile.diff_positions({"AAPL", "TSLA"}, {"AAPL", "NVDA"})
    assert not d["ok"]
    assert d["only_bot"] == ["TSLA"]
    assert d["only_broker"] == ["NVDA"]


def test_bot_symbol_prefers_option_contract():
    share = {"ticker": "AAPL", "signal": {"leverage": 1.0}}
    opt = {"ticker": "AAPL", "signal": {"option_symbol": "AAPL260116C00200000"}}
    assert reconcile.bot_symbol(share) == "AAPL"
    assert reconcile.bot_symbol(opt) == "AAPL260116C00200000"
