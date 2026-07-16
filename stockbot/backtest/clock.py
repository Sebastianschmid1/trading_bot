"""IO-freie Zeitabstraktion für deterministische Backtests."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class Clock(ABC):
    """Vom Backtest injizierbare Quelle der aktuellen simulierten Zeit."""

    @abstractmethod
    def now(self) -> datetime:
        raise NotImplementedError

    @abstractmethod
    def advance_to(self, bar_time) -> None:
        """Setzt die aktuelle Zeit auf den gerade ausgewerteten Bar."""
        raise NotImplementedError


class BarClock(Clock):
    """Clock, deren Zeit ausschließlich durch historische Bars fortgeschrieben wird."""

    def __init__(self):
        self._now: datetime | None = None

    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("BarClock wurde noch nicht auf einen Bar-Zeitpunkt gesetzt.")
        return self._now

    def advance_to(self, bar_time) -> None:
        self._now = pd.Timestamp(bar_time).to_pydatetime()
