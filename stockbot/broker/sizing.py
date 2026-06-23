"""
Order-Sizing: aus Trade-Wert (Budget) + Hebel den konkreten Order-Plan ableiten.

Reine, broker-/IO-freie Logik (gut testbar). Zwei Bausteine:

- `plan_share_order` — Aktien fürs Budget: möglichst **ganze** Aktien, **Bruchteile nur**, wenn
  eine einzelne Aktie teurer als das Budget ist.
- `plan_order` — der Gesamt-Plan: bei Hebel > 1 (und regulärer Handelszeit) wird über einen
  `option_selector`-Callback eine **Option** fürs Budget gewählt; sonst/als Fallback Aktien.

Wichtig (Feature 3/4): Das **Budget ist der Trade-Wert selbst** — der Hebel multipliziert NICHT mehr
den Einsatz (früher `trade_size × leverage`). Der Hebel entsteht beim Optionskauf aus dem Kontrakt.

Rückgabe ist immer ein Dict mit `kind`:
- `{"kind": "shares", "qty": int|None, "notional": float|None, "fractional": bool}`
- `{"kind": "option", "option_symbol": str, "qty": int, "premium": float, "delta": float,
    "omega": float, "strike": float, "expiry": str}`
- `{"kind": "none", "reason": str}` — nichts kaufbar (Budget reicht nicht).
"""

import math


def plan_share_order(price: float, budget: float, *, roundup_factor: float = 1.0) -> dict:
    """Plant einen Aktienkauf fürs Budget.

    - Budget reicht für ≥ 1 ganze Aktie → ganze Aktien (keine Bruchteile).
    - Aktie etwas teurer als das Budget (Kurs ≤ Budget × `roundup_factor`, Faktor > 1) → 1 GANZE
      Aktie; der Einsatz überschreitet dann bewusst die Trade-Größe, dafür ist die Order auch
      außerhalb der regulären Handelszeit (als Limit) handelbar. `rounded_up=True` markiert das.
    - Aktie deutlich teurer als das Budget → genau ein Bruchteil über `notional = budget`.
    - Ungültiger Kurs/Budget → `{"kind": "none"}`.
    """
    if not price or price <= 0 or not budget or budget <= 0:
        return {"kind": "none", "reason": "kein Kurs/Budget"}
    whole = math.floor(budget / price)
    if whole >= 1:
        return {"kind": "shares", "qty": whole, "notional": None, "fractional": False, "rounded_up": False}
    # Eine einzelne Aktie ist teurer als das Budget — knapp darüber → auf 1 ganze Aktie aufrunden.
    if roundup_factor and roundup_factor > 1.0 and price <= budget * roundup_factor:
        return {"kind": "shares", "qty": 1, "notional": None, "fractional": False, "rounded_up": True}
    # Deutlich teurer → Bruchteil zulassen (einzig erlaubter Bruchteil-Fall).
    return {"kind": "shares", "qty": None, "notional": round(budget, 2), "fractional": True, "rounded_up": False}


def plan_order(price: float, trade_size: float, leverage: float, *,
               option_selector=None, extended: bool = False, roundup_factor: float = 1.0) -> dict:
    """Gesamter Order-Plan für genau `trade_size` Budget (Hebel hebelt NICHT den Einsatz).

    - `leverage > 1` und reguläre Handelszeit und `option_selector` vorhanden → Option fürs Budget
      (Ziel-Hebel ≈ leverage). Liefert der Selector nichts → Aktien-Fallback.
    - sonst (Hebel ≤ 1, erweiterte Handelszeit oder kein Selector) → Aktien fürs Budget.

    `roundup_factor` wird an `plan_share_order` durchgereicht (Aufrunden auf 1 ganze Aktie).
    `option_selector(budget, target_leverage)` ist ein Callback, das `{option_symbol, qty,
    premium, delta, omega, strike, expiry}` oder None zurückgibt (kapselt Alpaca-Zugriffe).
    """
    budget = float(trade_size)
    lev = float(leverage or 1.0)

    if lev > 1.0 and not extended and option_selector is not None:
        opt = option_selector(budget, lev)
        if opt:
            return {"kind": "option", **opt}
        # kein passender Kontrakt/keine Daten → Aktien-Fallback (ohne künstlichen Hebel)

    return plan_share_order(price, budget, roundup_factor=roundup_factor)
