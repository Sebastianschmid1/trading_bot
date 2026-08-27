"""OBS-CORPACT: Sichtbarmachung unverarbeiteter Kursanpassungen (Splits), BEVOR sie
Kennzahlen/P&L verfälschen.

Reine Verdrahtung zweier bereits vorhandener, aber bislang von keinem Produktionscode
aufgerufener Bausteine: `market_data.MarketDataProvider.get_corporate_actions` (Datenabruf,
IO) und `data_quality.check_corporate_actions` (reine Entscheidung). Beobachtet nur — der
TSAFE-Risk-Pfad (`core/risk.py::pretrade_check`) bleibt unberührt, ob die Prüfung später ins
Risk-Gate wandert, entscheidet der Betreiber.

Analog zu `core/alerts.py`: dieses Modul ist IO-frei (`find_corporate_actions` wertet bereits
abgerufene Corporate Actions aus) + ein dünner Dedup-Zustand (`CorporateActionNotifier`). Der
Broker-Abruf und der Telegram-Versand laufen im Scheduler-Job
(`tgbot/bot.py::corporate_action_guard_job`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from stockbot.core.data_quality import check_corporate_actions
from stockbot.core.market_data import CorporateAction

# Deckt sich bewusst mit dem Default in `data_quality.check_corporate_actions` — hier
# zusätzlich als Filter gebraucht, um aus der abgerufenen Liste GENAU die blockierenden
# Einträge herauszugreifen (z. B. keine Dividenden), damit die Meldung Art + Datum je Fund
# konkret benennen kann statt nur die Sammel-Begründung der Entscheidung zu wiederholen.
SPLIT_ACTION_TYPES = (
    "split", "forward_split", "forward_splits", "reverse_split", "reverse_splits",
)


@dataclass(frozen=True)
class CorporateActionFinding:
    """Eine erkannte, unverarbeitete Kursanpassung für EIN Symbol."""
    ticker: str
    action_type: str
    ex_date: datetime

    @property
    def key(self) -> tuple[str, str, str]:
        """Identität eines Fundes für die Dedup-Prüfung (Kalendertag statt exakter Zeit, damit
        z. B. unterschiedliche Uhrzeiten im ex_date denselben Fund nicht doppelt melden)."""
        return (self.ticker, self.action_type, self.ex_date.date().isoformat())


def find_corporate_actions(
    actions_by_ticker: Mapping[str, list[CorporateAction]],
    *, since_by_ticker: Mapping[str, datetime],
) -> list[CorporateActionFinding]:
    """Reine Auswertung bereits abgerufener Corporate Actions je Symbol — kein IO.

    `actions_by_ticker`: Ergebnis von `MarketDataProvider.get_corporate_actions` je Ticker.
    `since_by_ticker`: dasselbe `since`, mit dem die Actions abgerufen wurden (Positions-
    Einstieg bzw. Watchlist-Fenster) — wird hier erneut gebraucht, um aus der Liste genau die
    Einträge zu filtern, die `check_corporate_actions` als blockierend eingestuft hat.
    """
    findings: list[CorporateActionFinding] = []
    for ticker, actions in actions_by_ticker.items():
        since = since_by_ticker[ticker]
        decision = check_corporate_actions(actions, since=since, blocking_types=SPLIT_ACTION_TYPES)
        if decision.ok:
            continue
        findings.extend(
            CorporateActionFinding(ticker, action.action_type, action.ex_date)
            for action in actions
            if action.ex_date >= since and action.action_type in SPLIT_ACTION_TYPES
        )
    return findings


class CorporateActionNotifier:
    """Meldet einen Fund nur einmal — analog `alerts.AlertNotifier`, aber ohne dessen
    optionalen Sync-Notifier-Callback (der Job hier ist ohnehin schon async und verschickt
    selbst über `context.bot`)."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str]] = set()

    def new_findings(self, findings: list[CorporateActionFinding]) -> list[CorporateActionFinding]:
        """Filtert auf noch nicht gemeldete Funde und merkt sich auch die alten weiter (ein
        Fund gilt dauerhaft als gemeldet, nicht nur bis er aus einem Lauf verschwindet)."""
        new = [f for f in findings if f.key not in self._seen]
        self._seen.update(f.key for f in findings)
        return new

    @staticmethod
    def format_message(findings: list[CorporateActionFinding]) -> str:
        lines = ["⚠️ Unverarbeitete Kursanpassung erkannt — Kennzahlen ggf. verzerrt:"]
        lines.extend(
            f"• {f.ticker}: {f.action_type} zum {f.ex_date.date()}" for f in findings
        )
        return "\n".join(lines)
