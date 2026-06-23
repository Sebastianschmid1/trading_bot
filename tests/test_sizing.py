"""
Tests für das Order-Sizing (stockbot/broker/sizing.py).

Kern (Feature 4): Trade-Wert ist das Budget — Hebel hebelt NICHT den Einsatz; möglichst ganze
Aktien, Bruchteile nur, wenn eine einzelne Aktie teurer als das Budget ist.
Feature 3: bei Hebel > 1 wählt der `option_selector` eine Option fürs Budget.

Lauf:  pytest tests/test_sizing.py
"""

from stockbot.broker import sizing


def test_whole_shares_for_budget():
    # 1000$ / 200$ = 5 ganze Aktien, keine Bruchteile
    plan = sizing.plan_share_order(price=200.0, budget=1000.0)
    assert plan == {"kind": "shares", "qty": 5, "notional": None, "fractional": False, "rounded_up": False}


def test_floor_to_whole_shares():
    # 1000$ / 300$ = 3.33 → 3 ganze Aktien
    plan = sizing.plan_share_order(price=300.0, budget=1000.0)
    assert plan["kind"] == "shares" and plan["qty"] == 3 and plan["fractional"] is False


def test_fractional_only_when_share_exceeds_budget():
    # Eine Aktie (1500$) ist teurer als das Budget (1000$) → einziger Bruchteil-Fall
    plan = sizing.plan_share_order(price=1500.0, budget=1000.0)
    assert plan == {"kind": "shares", "qty": None, "notional": 1000.0, "fractional": True, "rounded_up": False}


def test_invalid_inputs():
    assert sizing.plan_share_order(price=0.0, budget=1000.0)["kind"] == "none"
    assert sizing.plan_share_order(price=100.0, budget=0.0)["kind"] == "none"


def test_plan_order_budget_is_trade_size_not_times_leverage():
    # Hebel 5, 1000$ Budget, KEINE Optionsauswahl → ganze Aktien für GENAU 1000$ (nicht 5000$)
    plan = sizing.plan_order(price=100.0, trade_size=1000.0, leverage=5.0,
                             option_selector=None, extended=False)
    assert plan["kind"] == "shares" and plan["qty"] == 10   # 1000/100, nicht 5000/100


def test_plan_order_uses_option_when_leverage_and_selector():
    called = {}

    def selector(budget, target_leverage):
        called["budget"] = budget
        called["target"] = target_leverage
        return {"option_symbol": "AAPL260116C00200000", "qty": 2, "premium": 4.0,
                "delta": 0.55, "omega": 5.1, "strike": 200.0, "expiry": "2026-01-16"}

    plan = sizing.plan_order(price=200.0, trade_size=1000.0, leverage=5.0,
                             option_selector=selector, extended=False)
    assert plan["kind"] == "option" and plan["option_symbol"] == "AAPL260116C00200000"
    assert called["budget"] == 1000.0 and called["target"] == 5.0   # Budget = Trade-Wert


def test_plan_order_falls_back_to_shares_when_no_option():
    plan = sizing.plan_order(price=100.0, trade_size=1000.0, leverage=5.0,
                             option_selector=lambda b, t: None, extended=False)
    assert plan["kind"] == "shares" and plan["qty"] == 10


def test_plan_order_extended_hours_never_options():
    # In erweiterten Handelszeiten keine Optionen (Selector wird nicht aufgerufen)
    plan = sizing.plan_order(price=100.0, trade_size=1000.0, leverage=5.0,
                             option_selector=lambda b, t: (_ for _ in ()).throw(AssertionError()),
                             extended=True)
    assert plan["kind"] == "shares"
