"""IO-Adapter fuer optionale Eingaben der zentralen Pre-Trade-Pruefung.

Ein eigenes Risikoprofil wird noch nicht persistiert. Bis eine Profiltabelle existiert,
verwendet der Loader deshalb die konservativen Defaults von :class:`RiskProfile`.
Nicht sauber ableitbare Werte werden weggelassen, damit ``pretrade_check`` den jeweiligen
optionalen Check ueberspringt. Insbesondere liefert Alpacas Konto-Snapshot derzeit keinen
realisierten Tages-P&L; ``realized_pnl_today`` wird daher bewusst nicht gesetzt.
"""

from __future__ import annotations

import logging
from typing import Any

from stockbot.broker import client as broker
from stockbot.core import db
from stockbot.core.domain import RiskProfile, Signal, TradeIntent
from stockbot.market.data_providers import AlpacaPaperMarketDataProvider

log = logging.getLogger(__name__)


def quote_context(
    ticker: str, *, provider: Any = None, risk_profile: RiskProfile | None = None,
) -> dict[str, Any]:
    """Lade optionale Quote-Risk-Eingaben; Providerfehler bleiben lokal und fail-open."""
    profile = risk_profile or RiskProfile(user_id=0)
    try:
        quote_provider = provider or AlpacaPaperMarketDataProvider()
        quote = quote_provider.get_quote(ticker)
    except Exception as exc:
        log.warning("Risk-Kontext: Quote fuer ticker=%s nicht lesbar: %s",
                    ticker, type(exc).__name__)
        return {}
    if quote is None:
        log.warning("Risk-Kontext: Quote fuer ticker=%s nicht verfuegbar", ticker)
        return {}
    return {
        "quote": quote,
        "max_quote_age_seconds": profile.max_quote_age_seconds,
        "max_spread_bps": profile.max_spread_bps,
    }


def signal_context(intent: TradeIntent, signal: Signal) -> dict[str, Any]:
    """Lade die aus Intent, Signal und Persistenz ableitbaren Risk-Eingaben."""
    context: dict[str, Any] = {"risk_profile": RiskProfile(user_id=int(intent.user_id))}

    try:
        active_trades = db.get_active_trades(intent.user_id)
    except Exception as exc:
        log.warning("Risk-Kontext: offene Positionen fuer user_id=%s nicht lesbar: %s",
                    intent.user_id, type(exc).__name__)
    else:
        context["open_position_count"] = len(active_trades)
        context["has_existing_ticker_position"] = any(
            str(trade.get("ticker", "")).upper() == signal.ticker.upper()
            for trade in active_trades
        )

    # `market_open` wird bewusst NICHT gesetzt: der Loader (intent, signal) kennt den
    # `extended`-Modus nicht, und der Bot unterstuetzt Extended-Hours-Orders (vorgemerkte
    # Limit-Orders bei geschlossenem regulaeren Markt). Ein OMS-seitiger market_open-Block
    # wuerde diese faelschlich ablehnen; die Session-/Entry-Cutoff-Steuerung liegt bereits
    # upstream (services/trades.accept_trade, ENTRY_CUTOFF_BEFORE_CLOSE_MIN).

    # Das aktuelle Domain-Signal enthaelt noch kein SL-Feld. Die bestehende Bridge referenziert
    # aber den Trade per signal.id; dessen Signal-JSON ist deshalb die kanonische SL-Quelle.
    try:
        trade = db.get_trade_by_id(int(signal.id)) if signal.id is not None else None
        stop_price = (trade.get("signal") or {}).get("stop_loss") if trade else None
        if stop_price is not None:
            context["stop_price"] = float(stop_price)
    except (TypeError, ValueError):
        log.warning("Risk-Kontext: ungueltiger Stop-Loss fuer signal_id=%s", signal.id)
    except Exception as exc:
        log.warning("Risk-Kontext: Stop-Loss fuer signal_id=%s nicht lesbar: %s",
                    signal.id, type(exc).__name__)

    return context


def account_context(client: Any, user_id: int) -> dict[str, Any]:
    """Lade optionale Konto-Risk-Eingaben; Brokerfehler bleiben lokal und fail-open."""
    try:
        summary = broker.account_summary(client)
    except Exception as exc:
        log.warning("Risk-Kontext: Brokerkonto fuer user_id=%s nicht lesbar: %s",
                    user_id, type(exc).__name__)
        return {}
    if not summary.get("ok"):
        log.warning("Risk-Kontext: Brokerkonto fuer user_id=%s nicht verfuegbar: %s",
                    user_id, summary.get("detail", "unbekannter Fehler"))
        return {}

    context: dict[str, Any] = {}
    # account_summary kann je nach Adapter zusaetzlich equity/account_value liefern.
    account_value = summary.get("account_value", summary.get("equity"))
    if account_value is not None:
        context["account_value"] = float(account_value)
    if summary.get("buying_power") is not None:
        context["buying_power"] = float(summary["buying_power"])
    if summary.get("status"):
        context["broker_status"] = str(summary["status"]).split(".")[-1].upper()
    return context
