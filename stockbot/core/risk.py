"""
Zentrale Pre-Trade-Risk-/Order-Vorprüfung — Seam für Phase 3 (RISK-003).

Phase 0 (TSAFE-007) führt diese Stelle als **einzigen** künftigen Durchgang für jede neue
Position ein. Sie bündelt die harten Sicherheits-Invarianten, die schon anderswo erzwungen
werden (globaler Live-Kill-Switch, Hebel-Deckel, Optionsverbot), an einer reproduzierbaren
Stelle. In Phase 3 wird sie um das volle Risikomodell erweitert (Sizing, Tagesverlustlimit,
Exposure-/Sektor-Limits, Quote-Frische, Marktsession über den Exchange-Kalender aus Phase 2).

Bewusst broker-/IO-frei und rein — nur Config + übergebene Werte, damit sie gut testbar ist und
identisch für Telegram- und Web-Pfad gilt. Sie ENTSCHEIDET, führt aber selbst keine Order aus.
"""

from dataclasses import dataclass

from stockbot import config


@dataclass(frozen=True)
class RiskDecision:
    """Ergebnis der Vorprüfung. `ok=False` ⇒ die Order darf nicht gesendet werden."""
    ok: bool
    reason: str = ""
    code: str = ""          # maschinenlesbarer Ablehnungsgrund (z. B. "leverage_blocked")


_ALLOW = RiskDecision(ok=True)


def pretrade_check(*, leverage: float = 1.0, is_option: bool = False,
                   is_live_account: bool = False) -> RiskDecision:
    """Prüft die harten Phase-0-Invarianten für eine NEUE Position.

    Reihenfolge (schlimmster/absolutester Grund zuerst):
      1. globaler Live-Kill-Switch — Live-Konto ohne Freigabe,
      2. Hebel über `MAX_LEVERAGE`,
      3. Optionen in V1 deaktiviert.

    Schutz-Exits (Verkäufe/Positionsschließungen) laufen NICHT über diese Funktion — sie bleiben
    erlaubt (vgl. Konzept §17.4). Erweiterung um Sizing/Limits/Session folgt in Phase 3.
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
    return _ALLOW
