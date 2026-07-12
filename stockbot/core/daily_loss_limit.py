"""Tagesverlustlimit (Phase 3 / RISK-004, siehe docs/Plan.md, docs/PLAN_CHECKLIST.md Phase 3).

Reine, IO-freie Entscheidungsfunktion — „laufend fortschreiben" bedeutet hier NICHT eigenen
State/Persistenz in diesem Modul, sondern dass der Aufrufer bei jeder Prüfung den aktuellen
realisierten Tages-P&L übergibt (z. B. aus `db.get_all_trades_between(user_id, heute, heute)`);
die Funktion selbst ist zustandslos und immer mit den aktuellsten Zahlen korrekt.

Noch von KEINEM Live-Codepfad genutzt — die Verdrahtung in `stockbot/core/risk.py::pretrade_check`
folgt, sobald der Aufrufer (Telegram/Web) den heutigen realisierten P&L ermittelt und übergibt.
"""

from __future__ import annotations

from dataclasses import dataclass

from stockbot.core.domain import RiskProfile


@dataclass(frozen=True)
class DailyLossLimitStatus:
    """Ergebnis der Prüfung. `ok=False` ⇒ keine neue Position mehr für heute."""
    ok: bool
    realized_pnl: float
    limit_amount: float
    reason: str = ""
    code: str = ""


def check_daily_loss_limit(
    *, realized_pnl_today: float, account_value: float, risk_profile: RiskProfile,
) -> DailyLossLimitStatus:
    """Blockiert neue Positionen, sobald der heutige REALISIERTE Verlust das Limit erreicht/
    überschreitet (`risk_profile.daily_loss_limit_pct` % des Kontowerts).

    Nur realisierte P&L geschlossener Trades zählen — unrealisierte Verluste offener Positionen
    fließen bewusst NICHT ein, sonst würde ein reiner Kursrücksetzer (ohne Verkauf) allein schon
    neue Positionen sperren."""
    if account_value <= 0:
        return DailyLossLimitStatus(
            False, realized_pnl_today, 0.0, "Kontowert muss > 0 sein.", "invalid_account_value")

    limit_amount = account_value * (risk_profile.daily_loss_limit_pct / 100.0)
    if realized_pnl_today <= -limit_amount:
        return DailyLossLimitStatus(
            False, realized_pnl_today, limit_amount,
            f"Tagesverlustlimit erreicht: {realized_pnl_today:.2f} ≤ -{limit_amount:.2f} "
            f"({risk_profile.daily_loss_limit_pct:g}% von {account_value:.2f}).",
            "daily_loss_limit_reached")
    return DailyLossLimitStatus(True, realized_pnl_today, limit_amount)
