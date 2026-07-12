"""
Zentrale Pre-Trade-Risk-/Order-Vorprüfung — Seam für Phase 3 (RISK-003).

Phase 0 (TSAFE-007) führt diese Stelle als **einzigen** künftigen Durchgang für jede neue
Position ein. Sie bündelt die harten Sicherheits-Invarianten, die schon anderswo erzwungen
werden (globaler Live-Kill-Switch, Hebel-Deckel, Optionsverbot), an einer reproduzierbaren
Stelle. Phase 3 (RISK-003) erweitert sie schrittweise um das volle Risikomodell — bislang
Signal gültig/nicht abgelaufen, Strategie erlaubt, Markt-offen (DATA-002), Quote-Frische/Spread
(DATA-004), Liquidität, Tagesverlustlimit (RISK-004), max Positionen, bestehende
Ticker-Position, Exposure/Sektor/Korreliert/Täglich-neu (RISK-005), risikobasiertes Sizing
(RISK-002), Buying Power, Brokerstatus. Wie alle Live-Kontodaten-Checks bewusst nur als
optionaler Parameter angebunden — der Aufrufer fragt den Broker ab (z. B.
`broker.account_summary`) und übergibt das Ergebnis, statt dass dieses Modul selbst IO macht.

Bewusst broker-/IO-frei und rein — nur Config + übergebene Werte, damit sie gut testbar ist und
identisch für Telegram- und Web-Pfad gilt. Sie ENTSCHEIDET, führt aber selbst keine Order aus.
Jeder optionale Check läuft nur, wenn die dafür nötige Eingabe übergeben wurde (Aufrufer, die
z. B. noch keine Quote haben, lassen `quote=None` — das blockiert nicht allein deshalb).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from stockbot import config
from stockbot.core import data_quality
from stockbot.core.daily_loss_limit import check_daily_loss_limit
from stockbot.core.domain import RiskProfile, SignalStatus
from stockbot.core.exposure import ExposurePosition, evaluate_exposure
from stockbot.core.market_data import Quote
from stockbot.core.risk_sizing import SizingResult, size_position


@dataclass(frozen=True)
class RiskDecision:
    """Ergebnis der Vorprüfung. `ok=False` ⇒ die Order darf nicht gesendet werden."""
    ok: bool
    reason: str = ""
    code: str = ""          # maschinenlesbarer Ablehnungsgrund (z. B. "leverage_blocked")
    sizing: SizingResult | None = None   # gesetzt, wenn entry_price/stop_price übergeben wurden


_ALLOW = RiskDecision(ok=True)


def pretrade_check(
    *, leverage: float = 1.0, is_option: bool = False, is_live_account: bool = False,
    signal_status: SignalStatus | None = None, signal_expires_at: datetime | None = None,
    strategy_key: str | None = None, allowed_strategies: tuple[str, ...] | None = None,
    market_open: bool | None = None, quote: Quote | None = None,
    max_quote_age_seconds: float | None = None, max_spread_bps: float | None = None,
    average_dollar_volume: float | None = None, min_average_dollar_volume: float | None = None,
    realized_pnl_today: float | None = None, account_value: float | None = None,
    risk_profile: RiskProfile | None = None,
    open_position_count: int | None = None, has_existing_ticker_position: bool | None = None,
    candidate_notional: float | None = None, candidate_sector: str | None = None,
    candidate_correlation_group: str | None = None,
    open_positions: tuple[ExposurePosition, ...] = (),
    entry_price: float | None = None, stop_price: float | None = None,
    buying_power: float | None = None, broker_status: str | None = None,
    now: datetime | None = None,
) -> RiskDecision:
    """Prüft die Pre-Trade-Invarianten für eine NEUE Position, in fester Reihenfolge
    (Plan.md §11.3, RISK-003 — schlimmster/absolutester Grund zuerst):

      1. globaler Live-Kill-Switch — Live-Konto ohne Freigabe,
      2. Hebel über `MAX_LEVERAGE`,
      3. Optionen in V1 deaktiviert,
      4. Signal gültig und nicht abgelaufen (nur geprüft, wenn `signal_status`/`signal_expires_at`
         übergeben wurden),
      5. Strategie erlaubt (nur geprüft, wenn `strategy_key`+`allowed_strategies` übergeben
         wurden und `allowed_strategies` nicht leer ist — eine leere Liste blockiert nichts),
      6. Markt offen (nur geprüft, wenn `market_open` übergeben wurde),
      7. Quote frisch (nur geprüft, wenn `quote`+`max_quote_age_seconds` übergeben wurden),
      8. Spread nicht zu groß (nur geprüft, wenn `quote`+`max_spread_bps` übergeben wurden),
      9. Liquidität ausreichend (nur geprüft, wenn `average_dollar_volume`+
         `min_average_dollar_volume` übergeben wurden),
     10. Tagesverlustlimit (nur geprüft, wenn `realized_pnl_today`+`account_value`+
         `risk_profile` übergeben wurden — der heutige REALISIERTE Tages-P&L ist eine
         Live-Kontoabfrage, die der Aufrufer selbst ermitteln und übergeben muss),
     11. maximale Positionen (nur geprüft, wenn `open_position_count`+`risk_profile`
         übergeben wurden),
     12. bestehende Ticker-Position (nur geprüft, wenn `has_existing_ticker_position`
         übergeben wurde — verhindert eine zweite offene Position im selben Ticker),
     13. Exposure (Einzel/Sektor/Korreliert/Täglich neu, delegiert an
         `exposure.evaluate_exposure` — nur geprüft, wenn `candidate_notional`+`account_value`+
         `risk_profile` übergeben wurden; `open_positions` defaultet auf leer),
     14. risikobasiertes Sizing (delegiert an `risk_sizing.size_position` — nur geprüft, wenn
         `entry_price`+`stop_price`+`account_value`+`risk_profile` übergeben wurden; das
         Ergebnis [Stückzahl/Risikobetrag/Notional] hängt bei Erfolg als `sizing` an der
         zurückgegebenen `RiskDecision`, damit der Aufrufer nicht doppelt rechnen muss),
     15. Buying Power (nur geprüft, wenn `buying_power` übergeben wurde; verglichen wird das
         Notional aus Schritt 14 [`sizing.notional`], ersatzweise `candidate_notional`, falls
         kein Sizing berechnet wurde),
     16. Brokerstatus (nur geprüft, wenn `broker_status` übergeben wurde — alles außer
         `"ACTIVE"` blockiert).

    Schutz-Exits (Verkäufe/Positionsschließungen) laufen NICHT über diese Funktion — sie bleiben
    erlaubt (vgl. Konzept §17.4).
    """
    if is_live_account and not config.LIVE_TRADING_ENABLED:
        return RiskDecision(False, "Live-Trading ist deaktiviert (globaler Kill-Switch).", "live_blocked")
    if float(leverage or 1.0) > config.MAX_LEVERAGE + 1e-9:
        return RiskDecision(
            False,
            f"Hebel {float(leverage):g}× über erlaubtem Maximum {config.MAX_LEVERAGE:g}×.",
            "leverage_blocked")
    if is_option and not config.ALLOW_OPTIONS:
        return RiskDecision(False, "Optionshandel ist in Version 1 deaktiviert.", "options_blocked")
    if signal_status is not None and signal_status != SignalStatus.ACCEPTED:
        return RiskDecision(
            False, f"Signal ist nicht gültig (Status: {signal_status.value}).", "signal_invalid")
    if signal_expires_at is not None and (now or datetime.now(timezone.utc)) > signal_expires_at:
        return RiskDecision(False, "Signal ist abgelaufen.", "signal_expired")
    if strategy_key is not None and allowed_strategies:
        if strategy_key not in allowed_strategies:
            return RiskDecision(
                False, f"Strategie „{strategy_key}“ ist für dieses Risikoprofil nicht erlaubt.",
                "strategy_not_allowed")
    if market_open is False:
        return RiskDecision(False, "Markt ist geschlossen — keine neue Position.", "market_closed")
    if quote is not None and max_quote_age_seconds is not None:
        d = data_quality.check_quote_age(quote, max_age_seconds=max_quote_age_seconds, now=now)
        if not d.ok:
            return RiskDecision(False, d.reason, d.code)
    if quote is not None and max_spread_bps is not None:
        d = data_quality.check_spread(quote, max_spread_bps=max_spread_bps)
        if not d.ok:
            return RiskDecision(False, d.reason, d.code)
    if average_dollar_volume is not None and min_average_dollar_volume is not None:
        if average_dollar_volume < min_average_dollar_volume:
            return RiskDecision(
                False,
                f"Durchschnittlicher Dollar-Umsatz {average_dollar_volume:,.0f} unter Minimum "
                f"{min_average_dollar_volume:,.0f}.",
                "liquidity_low")
    if realized_pnl_today is not None and account_value is not None and risk_profile is not None:
        d = check_daily_loss_limit(
            realized_pnl_today=realized_pnl_today, account_value=account_value,
            risk_profile=risk_profile)
        if not d.ok:
            return RiskDecision(False, d.reason, d.code)
    if open_position_count is not None and risk_profile is not None:
        if open_position_count >= risk_profile.max_open_positions:
            return RiskDecision(
                False,
                f"Maximale Anzahl offener Positionen erreicht "
                f"({open_position_count}/{risk_profile.max_open_positions}).",
                "max_positions_reached")
    if has_existing_ticker_position:
        return RiskDecision(
            False, "Für diesen Ticker ist bereits eine Position offen.", "ticker_position_exists")
    if candidate_notional is not None and account_value is not None and risk_profile is not None:
        d = evaluate_exposure(
            candidate_notional=candidate_notional, account_value=account_value,
            risk_profile=risk_profile, candidate_sector=candidate_sector,
            candidate_correlation_group=candidate_correlation_group, positions=open_positions)
        if not d.ok:
            return RiskDecision(False, d.reason, d.code)
    sizing_result: SizingResult | None = None
    if (entry_price is not None and stop_price is not None and account_value is not None
            and risk_profile is not None):
        sizing_result = size_position(
            account_value=account_value, entry_price=entry_price, stop_price=stop_price,
            risk_profile=risk_profile)
        if not sizing_result.ok:
            return RiskDecision(False, sizing_result.reason, sizing_result.code)
    if buying_power is not None:
        effective_notional = sizing_result.notional if sizing_result is not None else candidate_notional
        if effective_notional is not None and effective_notional > buying_power:
            return RiskDecision(
                False,
                f"Notional {effective_notional:.2f} über verfügbarer Buying Power "
                f"{buying_power:.2f}.",
                "insufficient_buying_power")
    if broker_status is not None and broker_status != "ACTIVE":
        return RiskDecision(
            False, f"Brokerstatus „{broker_status}“ erlaubt keine neue Position.",
            "broker_status_blocked")
    if sizing_result is not None:
        return RiskDecision(True, sizing_result.reason, sizing_result.code, sizing=sizing_result)
    return _ALLOW
