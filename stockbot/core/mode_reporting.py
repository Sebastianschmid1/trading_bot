"""Adapter: Legacy-/Shadow-Daten → mode-isolierte ``ModeReport``s (RES-002 / W3.4 → Gate P6).

`stockbot.core.mode_report.build_mode_report` ist bewusst rein und akzeptiert nur Domänenobjekte
eines einzigen Modus. Dieser Adapter überführt die real vorhandenen Daten in genau diese Objekte:

- **Paper**: die Legacy-`trades` (abgeschlossene Demo-/Paper-Trades mit `pnl_eur`) → je Trade eine
  `PerformanceSnapshot(mode=PAPER, net_pnl=pnl_eur, strategy_version_id aus signal_json)`.
- **Shadow**: `PerformanceSnapshot(mode=SHADOW, …)` aus dem Shadow-Lauf (siehe
  `stockbot/research/shadow.py::shadow_performance_snapshot`). Bis der Shadow-Scheduler (W3.5) Daten
  liefert, ist der Shadow-Report leer — er wird nie mit Paper-Zahlen vermischt (Mode-Isolation).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from stockbot.core.domain import Mode, PerformanceSnapshot
from stockbot.core.mode_report import ModeReport, build_mode_report


def _paper_snapshots(closed_trades: Iterable[dict]) -> list[PerformanceSnapshot]:
    snapshots: list[PerformanceSnapshot] = []
    for trade in closed_trades:
        if trade.get("pnl_eur") is None:
            continue
        signal = trade.get("signal") or {}
        snapshots.append(PerformanceSnapshot(
            id=trade.get("id"),
            strategy_version_id=int(signal.get("strategy_version_id") or 0),
            mode=Mode.PAPER,
            captured_at=str(trade.get("trade_date") or ""),
            net_pnl=float(trade["pnl_eur"]),
            open_risk=None,
        ))
    return snapshots


def paper_report(closed_trades: Iterable[dict]) -> ModeReport:
    """Mode-isolierter Paper-Report aus den Legacy-Trade-Rows."""
    return build_mode_report(Mode.PAPER, performance_snapshots=_paper_snapshots(closed_trades))


def shadow_report(shadow_snapshots: Iterable[PerformanceSnapshot] = ()) -> ModeReport:
    """Mode-isolierter Shadow-Report aus Shadow-`PerformanceSnapshot`s (leer bis W3.5 liefert)."""
    return build_mode_report(Mode.SHADOW, performance_snapshots=tuple(shadow_snapshots))


def report_to_dict(report: ModeReport) -> dict:
    """Template-/JSON-freundliche Sicht (Mode als String)."""
    data = asdict(report)
    data["mode"] = report.mode.value
    return data


def build_mode_reports(closed_trades: Iterable[dict], *,
                       shadow_snapshots: Iterable[PerformanceSnapshot] = ()) -> dict[str, dict]:
    """Getrennte Paper-/Shadow-Reports als serialisierbare Dicts — für die Dashboard-Ansichten."""
    return {
        "paper": report_to_dict(paper_report(closed_trades)),
        "shadow": report_to_dict(shadow_report(shadow_snapshots)),
    }
