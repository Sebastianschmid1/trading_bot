"""Pure, mode-isolated performance reporting (Plan.md §14.4 / RES-002).

The calculation layer deliberately accepts domain objects only and rejects every
object whose mode differs from the requested report. This makes it impossible to
silently include Backtest objects in Live metrics.

``Fill`` currently exposes fees, but neither realized P&L nor slippage. Therefore
costs are summed from fills, while P&L metrics use the explicit ``net_pnl`` values
from performance snapshots. Slippage remains ``None``. Open risk uses only explicit
snapshot values because today's positions have no stop price or risk budget.
When several snapshots contain open risk, the last explicit value is the current
snapshot rather than a value that can meaningfully be summed over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from stockbot.core.domain import Fill, Mode, PerformanceSnapshot, Position


@dataclass(frozen=True)
class ModeReport:
    """Mode-specific metrics.

    ``None`` means the inputs cannot support a value; for ``profit_factor`` it also
    represents the mathematically unbounded no-loss case, matching legacy metrics.
    """

    mode: Mode
    net_pnl: float | None
    costs: float | None
    slippage: float | None
    drawdown: float | None
    trade_count: int | None
    profit_factor: float | None
    expectancy: float | None
    open_risk: float | None
    strategy_versions: list[int]


def _checked(items: Iterable, mode: Mode, entity_name: str) -> tuple:
    materialized = tuple(items)
    for item in materialized:
        if item.mode != mode:
            raise ValueError(
                f"{entity_name} mode {item.mode.value!r} does not match report mode {mode.value!r}"
            )
    return materialized


def _max_drawdown(pnls: list[float]) -> float:
    """Absolute drawdown over cumulative net P&L, including the zero starting point."""
    cumulative = 0.0
    peak = 0.0
    maximum = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def build_mode_report(
    mode: Mode,
    *,
    fills: Iterable[Fill] = (),
    positions: Iterable[Position] = (),
    performance_snapshots: Iterable[PerformanceSnapshot] = (),
) -> ModeReport:
    """Build one report from inputs that must all belong to ``mode``.

    Snapshots must be supplied in reporting order. Each snapshot with a non-``None``
    ``net_pnl`` is treated as one completed-trade observation. Snapshots without P&L
    can still contribute explicit open risk and a strategy version, but never an
    invented performance value.
    """
    mode = Mode(mode)
    checked_fills = _checked(fills, mode, "Fill")
    checked_positions = _checked(positions, mode, "Position")
    checked_snapshots = _checked(performance_snapshots, mode, "PerformanceSnapshot")

    pnls = [float(snapshot.net_pnl) for snapshot in checked_snapshots
            if snapshot.net_pnl is not None]
    gains = sum(pnl for pnl in pnls if pnl > 0)
    losses = sum(pnl for pnl in pnls if pnl < 0)
    explicit_risks = [float(snapshot.open_risk) for snapshot in checked_snapshots
                      if snapshot.open_risk is not None]
    strategy_versions = sorted(
        {position.strategy_version_id for position in checked_positions}
        | {snapshot.strategy_version_id for snapshot in checked_snapshots}
    )

    return ModeReport(
        mode=mode,
        net_pnl=sum(pnls) if pnls else None,
        costs=sum(float(fill.fee) for fill in checked_fills) if checked_fills else None,
        slippage=None,
        drawdown=_max_drawdown(pnls) if pnls else None,
        trade_count=len(pnls) if pnls else None,
        profit_factor=(gains / abs(losses)) if losses else None,
        expectancy=(sum(pnls) / len(pnls)) if pnls else None,
        open_risk=explicit_risks[-1] if explicit_risks else None,
        strategy_versions=strategy_versions,
    )
