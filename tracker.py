"""
Trade-Tracker: Verwaltet Demo-Trades (pending → aktiv → geschlossen)
Speichert alles in einer JSON-Datei für Persistenz
"""

import json
import logging
from datetime import date
from pathlib import Path

import yfinance as yf

log = logging.getLogger(__name__)
DATA_FILE = Path("data/trades.json")


class TradeTracker:
    def __init__(self):
        DATA_FILE.parent.mkdir(exist_ok=True)
        self._load()

    def _load(self):
        if DATA_FILE.exists():
            with open(DATA_FILE) as f:
                self.data = json.load(f)
        else:
            self.data = {}

    def _save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def _today(self) -> str:
        return str(date.today())

    def _ensure_today(self):
        today = self._today()
        if today not in self.data:
            self.data[today] = {}

    def add_pending(self, signal: dict, message_id: int):
        """Fügt einen vorgemerkten Trade hinzu (noch nicht bestätigt)."""
        self._ensure_today()
        ticker = signal["ticker"]
        self.data[self._today()][ticker] = {
            "ticker":     ticker,
            "direction":  signal["direction"],
            "signal":     signal,
            "message_id": message_id,
            "status":     "pending",   # pending → active → closed
            "entry":      None,
            "exit":       None,
            "pnl_eur":    None,
            "pnl_pct":    None,
        }
        self._save()
        log.info(f"Trade vorgemerkt: {ticker}")

    def activate_trade(self, ticker: str) -> bool:
        """Aktiviert einen Trade nach JA-Klick. Holt aktuellen Kurs."""
        today = self._today()
        trade = self.data.get(today, {}).get(ticker)
        if not trade or trade["status"] != "pending":
            return False

        # Aktuellen Kurs holen
        try:
            info = yf.Ticker(ticker).fast_info
            entry_price = float(info.last_price)
        except Exception:
            entry_price = float(trade["signal"]["price"])

        trade["status"] = "active"
        trade["entry"]  = entry_price
        self._save()
        log.info(f"Trade aktiviert: {ticker} @ ${entry_price:.2f}")
        return True

    def reject_trade(self, ticker: str):
        """Markiert Trade als abgelehnt."""
        today = self._today()
        if ticker in self.data.get(today, {}):
            self.data[today][ticker]["status"] = "rejected"
            self._save()

    def get_active_trades(self) -> list[dict]:
        """Gibt alle heute aktiven Trades zurück."""
        today_trades = self.data.get(self._today(), {})
        return [t for t in today_trades.values() if t["status"] == "active"]

    def get_trade(self, ticker: str) -> dict | None:
        return self.data.get(self._today(), {}).get(ticker)

    def close_all(self, results: list[dict]):
        """Schließt alle aktiven Trades mit den ausgewerteten Ergebnissen."""
        today = self._today()
        for r in results:
            ticker = r["ticker"]
            if ticker in self.data.get(today, {}):
                self.data[today][ticker].update({
                    "status":  "closed",
                    "exit":    r["exit"],
                    "pnl_eur": r["pnl_eur"],
                    "pnl_pct": r["pnl_pct"],
                })
        self._save()
        log.info(f"{len(results)} Trades geschlossen.")

    def get_history(self, days: int = 30) -> list[dict]:
        """Gibt alle abgeschlossenen Trades der letzten N Tage zurück."""
        all_trades = []
        for day_trades in self.data.values():
            for t in day_trades.values():
                if t["status"] == "closed":
                    all_trades.append(t)
        return all_trades[-days * 5:]  # ca. 5 Trades/Tag
