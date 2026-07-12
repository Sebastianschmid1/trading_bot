"""
Zentrale Pre-Trade-Risk-/Order-Vorprüfung — Seam für Phase 3 (RISK-003).

Phase 0 (TSAFE-007) führt diese Stelle als **einzigen** künftigen Durchgang für jede neue
Position ein. Sie bündelt die harten Sicherheits-Invarianten, die schon anderswo erzwungen
werden (globaler Live-Kill-Switch, Hebel-Deckel, Optionsverbot), an einer reproduzierbaren
Stelle. Phase 3 (RISK-003) erweitert sie schrittweise um das volle Risikomodell — bislang
Signal gültig/nicht abgelaufen, Strategie erlaubt, Markt-offen (DATA-002), Quote-Frische/Spread
(DATA-004), Liquidität; Tagesverlustlimit/max Positionen/Exposure-Sektor/Sizing/Buying-Power/
Brokerstatus (RISK-004/005/RISK-002-Integration) folgen als eigene Schritte, sobald die dafür
nötige Live-Kontoabfrage angebunden ist.

Bewusst broker-/IO-frei und rein — nur Config + übergebene Werte, damit sie gut testbar ist und
identisch für Telegram- und Web-Pfad gilt. Sie ENTSCHEIDET, führt aber selbst keine Order aus.
Jeder optionale Check läuft nur, wenn die dafür nötige Eingabe übergeben wurde (Aufrufer, die
z. B. noch keine Quote haben, lassen `quote=None` — das blockiert nicht allein deshalb).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from stockbot import config
from stockbot.core import data_quality
from stockbot.core.domain import SignalStatus
from stockbot.core.market_data import Quote


@dataclass(frozen=True)
class RiskDecision:
    """Ergebnis der Vorprüfung. `ok=False` ⇒ die Order darf nicht gesendet werden."""
    ok: bool
    reason: str = ""
    code: str = ""          # maschinenlesbarer Ablehnungsgrund (z. B. "leverage_blocked")


_ALLOW = RiskDecision(ok=True)


def pretrade_check(
    *, leverage: float = 1.0, is_option: bool = False, is_live_account: bool = False,
    signal_status: SignalStatus | None = None, signal_expires_at: datetime | None = None,
    strategy_key: str | None = None, allowed_strategies: tuple[str, ...] | None = None,
    market_open: bool | None = None, quote: Quote | None = None,
    max_quote_age_seconds: float | None = None, max_spread_bps: float | None = None,
    average_dollar_volume: float | None = None, min_average_dollar_volume: float | None = None,
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
         `min_average_dollar_volume` übergeben wurden).

    Tagesverlustlimit/max Positionen/bestehende Ticker-Position/Exposure-Sektor/Sizing/
    Buying-Power/Brokerstatus (RISK-004/005/RISK-002-Integration) sind noch NICHT Teil dieser
    Funktion — sie brauchen eine Live-Kontoabfrage (offene Positionen, Tages-P&L, Buying Power),
    die den bislang reinen IO-freien Charakter dieses Seams sprengen würde; sie folgen als
    eigene, separate Schritte.

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
    return _ALLOW
