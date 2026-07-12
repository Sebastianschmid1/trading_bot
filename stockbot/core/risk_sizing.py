"""Risikobasiertes Position-Sizing (Phase 3 / RISK-002, siehe docs/Plan.md §11.2,
docs/PLAN_CHECKLIST.md Phase 3).

Reine, IO-freie Sizing-Formel — bewusst getrennt von `stockbot/broker/sizing.py` (das plant
eine Order für ein bereits FESTES Euro-Budget je Trade; hier wird die Positionsgröße erst aus
dem Kontorisiko berechnet, bevor überhaupt ein Budget feststeht). Nutzt `domain.RiskProfile`
(RISK-001) für die Caps.

Noch von KEINEM Live-Codepfad genutzt — die Verdrahtung in den Order-Pfad folgt mit RISK-003
(feste Pre-Trade-Check-Reihenfolge, hier nur der Sizing-Baustein selbst).
"""

from __future__ import annotations

from dataclasses import dataclass

from stockbot.core.domain import RiskProfile


@dataclass(frozen=True)
class SizingResult:
    """Ergebnis des Sizings. `ok=False` ⇒ keine Order (siehe `reason`/`code`)."""
    ok: bool
    qty: float = 0.0
    risk_amount: float = 0.0
    notional: float = 0.0
    reason: str = ""
    code: str = ""


def size_position(
    *, account_value: float, entry_price: float, stop_price: float, risk_profile: RiskProfile,
) -> SizingResult:
    """Plan.md §11.2: `Risikobetrag = Kontowert × Risiko_pro_Trade`,
    `Stückzahl = Risikobetrag / Stop-Abstand`, gedeckelt durch `risk_profile.max_position_pct`
    (maximaler Kapitalanteil je Position — verhindert, dass ein sehr enger Stop eine
    unverhältnismäßig große Position ergibt)."""
    if account_value <= 0:
        return SizingResult(False, reason="Kontowert muss > 0 sein.", code="invalid_account_value")
    if entry_price <= 0:
        return SizingResult(False, reason="Einstiegskurs muss > 0 sein.", code="invalid_entry_price")
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return SizingResult(False, reason="Stop-Abstand muss > 0 sein.", code="invalid_stop_distance")

    risk_amount = account_value * (risk_profile.account_risk_per_trade_pct / 100.0)
    qty = risk_amount / stop_distance

    max_notional = account_value * (risk_profile.max_position_pct / 100.0)
    notional = qty * entry_price
    capped = False
    if notional > max_notional:
        qty = max_notional / entry_price
        notional = qty * entry_price
        capped = True

    if qty <= 0:
        return SizingResult(False, reason="Berechnete Stückzahl ist 0.", code="zero_quantity")

    return SizingResult(
        True, qty=qty, risk_amount=risk_amount, notional=notional,
        reason="Positionsgröße durch max_position_pct gedeckelt." if capped else "",
        code="capped_by_max_position_pct" if capped else "")
