"""Exposure-Limits (Phase 3 / RISK-005, siehe docs/Plan.md, docs/PLAN_CHECKLIST.md Phase 3).

Reine, IO-freie Prüf-Funktionen für Einzel-/Sektor-/Korrelations-/Tages-Exposure gegen die
Deckel aus `domain.RiskProfile`. Sektor-/Korrelationsgruppen sind ein Eingabe-String je Position
(`sector`/`correlation_group`) — dieses Modul berechnet KEINE Sektor-Klassifikation oder
Kurskorrelation selbst; die Datenquelle dafür (z. B. `yfinance` `.info['sector']` oder eine
statische Zuordnung) ist ein separater Wiring-Schritt. Fehlt die Sektor-/Korrelationsangabe für
eine Position, wird der jeweilige Check übersprungen statt fälschlich zu blockieren.

Noch von KEINEM Live-Codepfad genutzt. „Post-Trade: offene Position ohne Schutzorder erkennen"
(zweiter RISK-005-Punkt) ist ein eigenes, separates Thema und NICHT Teil dieses Moduls.
"""

from __future__ import annotations

from dataclasses import dataclass

from stockbot.core.domain import RiskProfile


@dataclass(frozen=True)
class ExposurePosition:
    """Eine bestehende offene Position, reduziert auf die für Exposure-Limits nötigen Felder
    (kein voller Positions-Datensatz)."""
    ticker: str
    notional: float
    sector: str | None = None
    correlation_group: str | None = None
    opened_today: bool = False


@dataclass(frozen=True)
class ExposureDecision:
    """Ergebnis eines Exposure-Checks. `ok=False` ⇒ die Order darf nicht gesendet werden."""
    ok: bool
    reason: str = ""
    code: str = ""


_OK = ExposureDecision(ok=True)


def check_single_position_exposure(
    *, candidate_notional: float, account_value: float, risk_profile: RiskProfile,
) -> ExposureDecision:
    """Plan.md §11.1 „Einzel"-Exposure: eine einzelne Position darf `max_position_pct` % des
    Kontowerts nicht überschreiten."""
    if account_value <= 0:
        return ExposureDecision(False, "Kontowert muss > 0 sein.", "invalid_account_value")
    limit = account_value * (risk_profile.max_position_pct / 100.0)
    if candidate_notional > limit:
        return ExposureDecision(
            False,
            f"Positionsgröße {candidate_notional:.2f} über Einzel-Limit {limit:.2f} "
            f"({risk_profile.max_position_pct:g}% von {account_value:.2f}).",
            "single_position_exposure")
    return _OK


def check_sector_exposure(
    *, candidate_notional: float, candidate_sector: str | None,
    positions: tuple[ExposurePosition, ...], account_value: float, risk_profile: RiskProfile,
) -> ExposureDecision:
    """„Sektor"-Exposure: Summe aller offenen Positionen desselben Sektors + Kandidat darf
    `max_sector_exposure_pct` % des Kontowerts nicht überschreiten. Ohne Sektor-Angabe für den
    Kandidaten wird nicht geprüft (nicht anwendbar, kein Ablehnungsgrund)."""
    if candidate_sector is None:
        return _OK
    if account_value <= 0:
        return ExposureDecision(False, "Kontowert muss > 0 sein.", "invalid_account_value")
    existing = sum(p.notional for p in positions if p.sector == candidate_sector)
    total = existing + candidate_notional
    limit = account_value * (risk_profile.max_sector_exposure_pct / 100.0)
    if total > limit:
        return ExposureDecision(
            False,
            f"Sektor-Exposure {total:.2f} ({candidate_sector}) über Limit {limit:.2f} "
            f"({risk_profile.max_sector_exposure_pct:g}% von {account_value:.2f}).",
            "sector_exposure")
    return _OK


def check_correlated_exposure(
    *, candidate_notional: float, candidate_correlation_group: str | None,
    positions: tuple[ExposurePosition, ...], account_value: float, risk_profile: RiskProfile,
) -> ExposureDecision:
    """„Korreliert"-Exposure: analog zu Sektor-Exposure, aber gruppiert nach
    `correlation_group` statt `sector` (z. B. eine manuell gepflegte Gruppe stark
    korrelierter Ticker)."""
    if candidate_correlation_group is None:
        return _OK
    if account_value <= 0:
        return ExposureDecision(False, "Kontowert muss > 0 sein.", "invalid_account_value")
    existing = sum(p.notional for p in positions if p.correlation_group == candidate_correlation_group)
    total = existing + candidate_notional
    limit = account_value * (risk_profile.max_correlated_exposure_pct / 100.0)
    if total > limit:
        return ExposureDecision(
            False,
            f"Korrelierte Exposure {total:.2f} ({candidate_correlation_group}) über Limit "
            f"{limit:.2f} ({risk_profile.max_correlated_exposure_pct:g}% von {account_value:.2f}).",
            "correlated_exposure")
    return _OK


def check_daily_new_exposure(
    *, candidate_notional: float, positions: tuple[ExposurePosition, ...],
    account_value: float, risk_profile: RiskProfile,
) -> ExposureDecision:
    """„Täglich neu"-Exposure: Summe des HEUTE neu eröffneten Notionals + Kandidat darf
    `max_daily_new_exposure_pct` % des Kontowerts nicht überschreiten (bremst zu schnelles
    Aufbauen neuer Positionen an einem einzigen Handelstag)."""
    if account_value <= 0:
        return ExposureDecision(False, "Kontowert muss > 0 sein.", "invalid_account_value")
    opened_today = sum(p.notional for p in positions if p.opened_today)
    total = opened_today + candidate_notional
    limit = account_value * (risk_profile.max_daily_new_exposure_pct / 100.0)
    if total > limit:
        return ExposureDecision(
            False,
            f"Heute neu eröffnete Exposure {total:.2f} über Limit {limit:.2f} "
            f"({risk_profile.max_daily_new_exposure_pct:g}% von {account_value:.2f}).",
            "daily_new_exposure")
    return _OK


def evaluate_exposure(
    *, candidate_notional: float, account_value: float, risk_profile: RiskProfile,
    candidate_sector: str | None = None, candidate_correlation_group: str | None = None,
    positions: tuple[ExposurePosition, ...] = (),
) -> ExposureDecision:
    """Bündelt alle vier Exposure-Checks in fester Reihenfolge (Einzel → Sektor → Korreliert →
    Täglich neu) — Seam fürs Wiring in `risk.py::pretrade_check`, analog zu `data_quality.
    evaluate_quality`."""
    decision = check_single_position_exposure(
        candidate_notional=candidate_notional, account_value=account_value, risk_profile=risk_profile)
    if not decision.ok:
        return decision
    decision = check_sector_exposure(
        candidate_notional=candidate_notional, candidate_sector=candidate_sector,
        positions=positions, account_value=account_value, risk_profile=risk_profile)
    if not decision.ok:
        return decision
    decision = check_correlated_exposure(
        candidate_notional=candidate_notional, candidate_correlation_group=candidate_correlation_group,
        positions=positions, account_value=account_value, risk_profile=risk_profile)
    if not decision.ok:
        return decision
    return check_daily_new_exposure(
        candidate_notional=candidate_notional, positions=positions,
        account_value=account_value, risk_profile=risk_profile)
