"""IO-Adapter fuer optionale Eingaben der zentralen Pre-Trade-Pruefung.

Ein eigenes Risikoprofil kann pro Nutzer persistiert werden (`db.get_risk_profile`). Solange
ein Nutzer KEIN Profil gespeichert hat, liefert der Loader das permissive Default-
:class:`RiskProfile` und verhaelt sich exakt wie zuvor (inert-by-default). Nicht sauber
ableitbare Werte werden weggelassen, damit ``pretrade_check`` den jeweiligen optionalen Check
ueberspringt.

``realized_pnl_today`` (fuer das Tagesverlustlimit RISK-004) wird bewusst NUR fuer Nutzer mit
einem gespeicherten Profil gesetzt: erst mit dem Opt-in ins Risiko-Framework darf das
Tagesverlustlimit greifen. Ohne gespeichertes Profil bleibt ``realized_pnl_today`` weg → der
Check wird uebersprungen (unveraendertes heutiges Verhalten). Die Ermittlung ist fail-open:
ist der Wert nicht lesbar, wird er weggelassen statt haerter zu blocken.

``average_dollar_volume`` (fuer den Liquiditaetscheck RISK-003 Schritt 9) wird aus dem im Trade
persistierten Signal abgeleitet (`avg_volume` × `price`, kein zusaetzlicher Marktdatenabruf) und
ist damit nur fuer Strategien verfuegbar, die `avg_volume` mitliefern (aktuell: "standard").
Bleibt der Wert weg, ueberspringt ``pretrade_check`` den Check wie bisher.
"""

from __future__ import annotations

import logging
from typing import Any

from stockbot import config
from stockbot.broker import client as broker
from stockbot.core import db
from stockbot.core.domain import RiskProfile, Signal, TradeIntent
from stockbot.core.exposure import ExposurePosition
from stockbot.market import asset_classes
from stockbot.market.data_providers import AlpacaPaperMarketDataProvider

log = logging.getLogger(__name__)


def _open_position_notional(trade: dict) -> float | None:
    """Notional einer aktiven Position aus dem persistierten Fill (Stückzahl × Einstiegspreis).

    Fehlt eine der beiden Größen (z. B. ein reiner Demo-Trade ohne Broker-Ausführung, dessen
    ``broker_filled_qty`` nie gesetzt wurde), bleibt die Position für den Korrelations-/
    Sektor-Exposure-Vergleich außen vor, statt eine Größe zu raten (fail-neutral, analog zur
    fehlenden Korrelationsgruppe in :func:`correlation_group_for_ticker`)."""
    qty, entry = trade.get("broker_filled_qty"), trade.get("entry")
    if qty is None or entry is None:
        return None
    try:
        qty_f, entry_f = float(qty), float(entry)
    except (TypeError, ValueError):
        return None
    if qty_f <= 0 or entry_f <= 0:
        return None
    return qty_f * entry_f


def quote_context(
    ticker: str, *, provider: Any = None, risk_profile: RiskProfile | None = None,
) -> dict[str, Any]:
    """Lade optionale Quote-Risk-Eingaben.

    ``quote_context`` kennt keine ``user_id`` (Aufruf nur mit ``ticker``); ein persistiertes
    Profil laedt daher ``signal_context``. Ruft ein Aufrufer mit einem bereits geladenen
    ``risk_profile`` auf, wird dieses genutzt, sonst das permissive Default.

    Default (``RISK_FAIL_CLOSED_ON_QUOTE`` AUS): Providerfehler bleiben lokal und fail-open —
    ohne belastbare Quote kommt ``{}`` zurueck und ``pretrade_check`` ueberspringt die
    Frische-/Spread-Checks (heutiges Verhalten, unveraendert).

    Fail-closed (Flag AN): ist keine belastbare Quote abrufbar (Exception oder ``None``),
    kommt stattdessen das additive Sentinel ``{"quote_required": True}`` zurueck. Das signalisiert
    ``pretrade_check``, die Order explizit mit Grund ``quote_unavailable`` abzulehnen, statt sie
    still durchzulassen. Der Erfolgsfall ist in beiden Modi identisch.
    """
    profile = risk_profile or RiskProfile(user_id=0)
    # Ein leeres dict laesst das Gate fail-open (heute); das Sentinel macht es fail-closed.
    unavailable: dict[str, Any] = {"quote_required": True} if config.RISK_FAIL_CLOSED_ON_QUOTE else {}
    try:
        quote_provider = provider or AlpacaPaperMarketDataProvider()
        quote = quote_provider.get_quote(ticker)
    except Exception as exc:
        log.warning("Risk-Kontext: Quote fuer ticker=%s nicht lesbar: %s",
                    ticker, type(exc).__name__)
        return unavailable
    if quote is None:
        log.warning("Risk-Kontext: Quote fuer ticker=%s nicht verfuegbar", ticker)
        return unavailable
    return {
        "quote": quote,
        "max_quote_age_seconds": profile.max_quote_age_seconds,
        "max_spread_bps": profile.max_spread_bps,
    }


def signal_context(intent: TradeIntent, signal: Signal) -> dict[str, Any]:
    """Lade die aus Intent, Signal und Persistenz ableitbaren Risk-Eingaben."""
    user_id = int(intent.user_id)
    has_stored_profile = False
    try:
        stored_profile = db.get_risk_profile(user_id)
    except Exception as exc:
        log.warning("Risk-Kontext: Risikoprofil fuer user_id=%s nicht lesbar: %s",
                    user_id, type(exc).__name__)
        stored_profile = None
    else:
        has_stored_profile = stored_profile is not None
    context: dict[str, Any] = {
        "risk_profile": stored_profile if stored_profile is not None else RiskProfile(user_id=user_id)
    }

    # `realized_pnl_today` (Tagesverlustlimit RISK-004) wird NUR fuer Nutzer mit einem
    # gespeicherten Profil gesetzt. Ohne Opt-in bleibt der Wert weg → `pretrade_check`
    # ueberspringt den Check (unveraendertes heutiges Verhalten, inert-by-default). Die
    # Ermittlung ist fail-open: bei Lesefehler wird der Wert weggelassen, nicht haerter geblockt.
    if has_stored_profile:
        try:
            context["realized_pnl_today"] = db.get_realized_pnl_today(user_id)
        except Exception as exc:
            log.warning("Risk-Kontext: realisierter Tages-P&L fuer user_id=%s nicht lesbar: %s",
                        user_id, type(exc).__name__)

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
        # RISK-005-Wiring: Bestand fuer den Korrelations-/Sektor-Exposure-Check
        # (exposure.evaluate_exposure vergleicht sonst gegen einen leeren Bestand). Trades ohne
        # ermittelbares Notional (kein Broker-Fill) bleiben außen vor statt geraten zu werden.
        context["open_positions"] = tuple(
            ExposurePosition(
                ticker=str(trade.get("ticker", "")),
                notional=notional,
                correlation_group=asset_classes.correlation_group_for_ticker(trade.get("ticker", "")),
            )
            for trade in active_trades
            if (notional := _open_position_notional(trade)) is not None
        )

    # Korrelationsgruppe des Kandidaten selbst (RISK-005-Wiring). Kein Sektor-Pendant: dem Repo
    # fehlt eine Sektor-Datenquelle (market/asset_classes.py klassifiziert Anlageklassen, keine
    # Sektoren) — eine erfundene Zuordnung waere schlechter als der uebersprungene Check.
    candidate_group = asset_classes.correlation_group_for_ticker(signal.ticker)
    if candidate_group is not None:
        context["candidate_correlation_group"] = candidate_group

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

    # RISK-LIQUIDITY-Wiring: `average_dollar_volume` fuer den Liquiditaetscheck (RISK-003
    # Schritt 9). `min_average_dollar_volume` bleibt am Default 0.0 (RiskProfile) -> der Check
    # bleibt inert, bis ein Betreiber die Schwelle bewusst setzt; hier wird nur die bislang
    # fehlende Eingabe nachgereicht. Kein zusaetzlicher Marktdatenabruf: die Standard-Strategie
    # (analyzer.analyze_ticker) legt das durchschnittliche Tagesvolumen in Stueck bereits additiv
    # im Signal-JSON ab (`avg_volume`, market/analyzer.py) -- Dollar-Umsatz = `avg_volume` ×
    # `price`, beide schon im selben Trade wie der Stop-Loss oben. Fail-open: fehlt eine der
    # beiden Groessen, ist sie nicht plausibel (<= 0) oder die Strategie liefert kein
    # `avg_volume` (z. B. andere Strategien als "standard"), bleibt der Wert weg -> der Check
    # wird uebersprungen statt geraten oder haerter zu blocken.
    try:
        liquidity_trade = db.get_trade_by_id(int(signal.id)) if signal.id is not None else None
        sig_json = (liquidity_trade.get("signal") or {}) if liquidity_trade else {}
        avg_volume, price = sig_json.get("avg_volume"), sig_json.get("price")
        if avg_volume is not None and price is not None:
            avg_volume_f, price_f = float(avg_volume), float(price)
            if avg_volume_f > 0 and price_f > 0:
                context["average_dollar_volume"] = avg_volume_f * price_f
            else:
                log.info(
                    "Risk-Kontext: Dollar-Umsatz fuer signal_id=%s nicht plausibel "
                    "(avg_volume=%s, price=%s) - Liquiditaetscheck bleibt uebersprungen.",
                    signal.id, avg_volume, price)
    except (TypeError, ValueError):
        log.warning("Risk-Kontext: ungueltiger Dollar-Umsatz fuer signal_id=%s", signal.id)
    except Exception as exc:
        log.warning("Risk-Kontext: Dollar-Umsatz fuer signal_id=%s nicht lesbar: %s",
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
