"""Deterministischer Portfolio-Allocator (Phase 6 / STRAT-006).

Der Allocator ist eine reine, IO-freie Schicht *über* ``risk.pretrade_check``. Er wiederholt
keine Einzelsignal-Prüfungen: eine bereits vorhandene ``RiskDecision`` wird übernommen. Für
ein zulässiges Signal reserviert er das von ``risk_sizing.size_position`` berechnete Risiko
und prüft Exposure-Limits nach jeder Auswahl erneut gegen den dann aktuellen Bestand.

Dieses Modul ist bewusst noch an keinen Signal-, Accept- oder Order-Pfad angebunden. Die
bestehende STRAT-004-Ausspielung bleibt bis zu einem separaten Wiring-Schritt unverändert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stockbot.core.domain import (
    Order,
    OrderStatus,
    Position,
    PositionStatus,
    RiskProfile,
    SignalCandidate,
)
from stockbot.core.exposure import ExposurePosition, evaluate_exposure
from stockbot.core.risk_sizing import SizingResult, size_position

if TYPE_CHECKING:
    from stockbot.core.risk import RiskDecision


@dataclass(frozen=True)
class AllocationCandidate:
    """Ein Kandidat plus die bereits außerhalb des Allocators ermittelten Eingaben.

    Die Reihenfolge der ``AllocationCandidate``-Objekte innerhalb einer Strategie ist deren
    interner Rang. ``expected_value`` und ``expected_cost`` sind Beträge in derselben Währung;
    ohne erwarteten Wert ist der optionale Kostenfilter nicht anwendbar.
    """

    candidate: SignalCandidate
    strategy_key: str
    entry_price: float
    stop_price: float
    sector: str | None = None
    correlation_group: str | None = None
    expected_value: float | None = None
    expected_cost: float = 0.0
    risk_decision: RiskDecision | None = None


@dataclass(frozen=True)
class SelectedSignal:
    candidate: SignalCandidate
    sizing: SizingResult
    reserved_risk_budget: float


@dataclass(frozen=True)
class RejectedSignal:
    candidate: SignalCandidate
    reason: str
    code: str
    # Bei zusammengefassten Allocator-Codes bleibt der konkrete Untergrund maschinenlesbar.
    detail_code: str = ""


@dataclass(frozen=True)
class AllocationInput:
    candidates: tuple[AllocationCandidate, ...]
    positions: tuple[Position, ...]
    open_orders: tuple[Order, ...]
    risk_profile: RiskProfile
    account_value: float
    exposure_positions: tuple[ExposurePosition, ...] = ()
    strategy_priorities: tuple[str, ...] = ()
    realized_pnl_today: float = 0.0
    total_risk_budget_pct: float | None = None
    minimum_net_expected_value: float | None = None


@dataclass(frozen=True)
class AllocationResult:
    selected: tuple[SelectedSignal, ...]
    rejected: tuple[RejectedSignal, ...]
    reserved_risk_total: float
    # Vollständige Prüf-Reihenfolge, einschließlich später verworfener Kandidaten.
    allocation_order: tuple[SignalCandidate, ...]

    @property
    def decision_order(self) -> tuple[SignalCandidate, ...]:
        """Sprechender Alias für Aufrufer, die die Reihenfolge als Audit-Trail verwenden."""
        return self.allocation_order


_TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
}


def _ordered_candidates(
    candidates: tuple[AllocationCandidate, ...], priorities: tuple[str, ...]
) -> tuple[AllocationCandidate, ...]:
    """Rundlauf über Strategieranglisten; explizite Priorität zuerst, Rest alphabetisch."""
    by_strategy: dict[str, list[AllocationCandidate]] = {}
    for item in candidates:
        by_strategy.setdefault(item.strategy_key, []).append(item)

    explicit = tuple(dict.fromkeys(key for key in priorities if key in by_strategy))
    remaining = tuple(sorted(set(by_strategy) - set(explicit)))
    strategy_order = explicit + remaining
    max_rank = max((len(by_strategy[key]) for key in strategy_order), default=0)

    ordered: list[AllocationCandidate] = []
    for rank in range(max_rank):
        for key in strategy_order:
            ranking = by_strategy[key]
            if rank < len(ranking):
                ordered.append(ranking[rank])
    return tuple(ordered)


def _risk_budget_limit(inputs: AllocationInput) -> float:
    daily_limit = inputs.account_value * (inputs.risk_profile.daily_loss_limit_pct / 100.0)
    daily_remaining = max(0.0, daily_limit - max(0.0, -inputs.realized_pnl_today))
    if inputs.total_risk_budget_pct is None:
        return daily_remaining
    explicit_limit = max(0.0, inputs.account_value * inputs.total_risk_budget_pct / 100.0)
    return min(daily_remaining, explicit_limit)


def allocate(inputs: AllocationInput) -> AllocationResult:
    """Teilt Kandidaten in deterministischer Strategie-Rundlauf-Reihenfolge zu.

    Ein identisches ``AllocationInput`` erzeugt ein identisches Ergebnis. Rohscores werden
    dabei nie zwischen Strategien verglichen. Bestehende Positionen, aktive offene Orders und
    bereits ausgewählte Kandidaten belegen jeweils einen Platz im Positionslimit.
    """
    ordered = _ordered_candidates(inputs.candidates, inputs.strategy_priorities)
    active_positions = tuple(p for p in inputs.positions if p.status != PositionStatus.CLOSED)
    active_orders = tuple(o for o in inputs.open_orders if o.status not in _TERMINAL_ORDER_STATUSES)
    occupied_tickers = {p.ticker for p in active_positions} | {o.ticker for o in active_orders}
    occupied_slots = len(active_positions) + len(active_orders)
    exposure_positions = list(inputs.exposure_positions)
    budget_limit = _risk_budget_limit(inputs)

    selected: list[SelectedSignal] = []
    rejected: list[RejectedSignal] = []
    reserved_total = 0.0

    for item in ordered:
        candidate = item.candidate
        risk_decision = item.risk_decision
        if risk_decision is not None and not risk_decision.ok:
            rejected.append(RejectedSignal(
                candidate, risk_decision.reason,
                risk_decision.code or "risk_rejected",
            ))
            continue

        if candidate.ticker in occupied_tickers:
            rejected.append(RejectedSignal(
                candidate,
                f"Für {candidate.ticker} besteht bereits eine Position, Order oder Zuteilung.",
                "duplicate_ticker",
            ))
            continue

        if occupied_slots + len(selected) >= inputs.risk_profile.max_open_positions:
            rejected.append(RejectedSignal(
                candidate,
                f"Maximale Anzahl offener Positionen erreicht "
                f"({occupied_slots + len(selected)}/{inputs.risk_profile.max_open_positions}).",
                "max_positions_reached",
            ))
            continue

        sizing = risk_decision.sizing if risk_decision is not None else None
        if sizing is None:
            sizing = size_position(
                account_value=inputs.account_value,
                entry_price=item.entry_price,
                stop_price=item.stop_price,
                risk_profile=inputs.risk_profile,
            )
        if not sizing.ok:
            rejected.append(RejectedSignal(
                candidate, sizing.reason, sizing.code or "risk_sizing_failed"))
            continue

        if (inputs.minimum_net_expected_value is not None
                and item.expected_value is not None
                and item.expected_value - item.expected_cost < inputs.minimum_net_expected_value):
            net_value = item.expected_value - item.expected_cost
            rejected.append(RejectedSignal(
                candidate,
                f"Erwarteter Nettowert {net_value:.2f} unter Minimum "
                f"{inputs.minimum_net_expected_value:.2f} (Kosten {item.expected_cost:.2f}).",
                "expected_value_below_minimum",
            ))
            continue

        exposure = evaluate_exposure(
            candidate_notional=sizing.notional,
            account_value=inputs.account_value,
            risk_profile=inputs.risk_profile,
            candidate_sector=item.sector,
            candidate_correlation_group=item.correlation_group,
            positions=tuple(exposure_positions),
        )
        if not exposure.ok:
            rejected.append(RejectedSignal(
                candidate, exposure.reason, "exposure_cap", detail_code=exposure.code))
            continue

        if reserved_total + sizing.risk_amount > budget_limit + 1e-12:
            rejected.append(RejectedSignal(
                candidate,
                f"Risikobudget erschöpft: {reserved_total:.2f} reserviert, "
                f"{sizing.risk_amount:.2f} benötigt, {budget_limit:.2f} verfügbar.",
                "budget_exhausted",
            ))
            continue

        selected.append(SelectedSignal(candidate, sizing, sizing.risk_amount))
        reserved_total += sizing.risk_amount
        occupied_tickers.add(candidate.ticker)
        exposure_positions.append(ExposurePosition(
            ticker=candidate.ticker,
            notional=sizing.notional,
            sector=item.sector,
            correlation_group=item.correlation_group,
            opened_today=True,
        ))

    return AllocationResult(
        selected=tuple(selected),
        rejected=tuple(rejected),
        reserved_risk_total=reserved_total,
        allocation_order=tuple(item.candidate for item in ordered),
    )
