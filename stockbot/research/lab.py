"""Strategie-Labor begrenzen: Champion/Candidate, Promotion-Gates, Holdout-Schutz (Gate P8, W6).

Reiner, IO-freier Kern (wie `shadow.py`). Zentrale Garantien aus Plan.md §Phase 8:

- **Kein direkter Live-Schreibzugriff**: Kandidaten durchlaufen einen expliziten Pending-Workflow;
  „live" ist kein Kandidatenzustand — nur ein separater, menschlich bestätigter Promotion-Schritt.
- **Genau ein Champion je Strategie**, beliebig viele Candidates.
- **Promotion-Gates**: ausreichende Tradezahl, OOS-Mehrheit, keine starke Drawdown-Verschlechterung,
  positive Netto-Performance, Sensitivitätsstabilität, Shadow-Bestätigung — UND **menschliche
  Freigabe** als letzter, nicht automatisierbarer Schritt (Tor T3).
- **Holdout-Schutz** (RES-005): Zugriffe protokolliert, kein wiederholtes Testen desselben
  Kandidaten am Holdout.
- **Suchraum begrenzt**: explizite Parametergrenzen, max. Kandidaten je Zyklus.
- **LLM nur beschreibend**: Hypothesen werden hart validiert und setzen niemals Code/Parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

# ── Kandidaten-Lebenszyklus ──────────────────────────────────────────────────

CANDIDATE_STATES = (
    "generated", "validated", "backtested", "shadow",
    "pending_review", "approved", "rejected", "archived",
)

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "generated": {"validated", "rejected", "archived"},
    "validated": {"backtested", "rejected", "archived"},
    "backtested": {"shadow", "rejected", "archived"},
    "shadow": {"pending_review", "rejected", "archived"},
    "pending_review": {"approved", "rejected", "archived"},
    "approved": {"archived"},
    "rejected": {"archived"},
    "archived": set(),
}


class LabError(RuntimeError):
    """Basisfehler des Strategie-Labors."""


class InvalidTransition(LabError):
    """Unzulässiger Zustandsübergang eines Kandidaten."""


class PromotionBlocked(LabError):
    """Eine Promotion wurde durch ein Gate oder eine fehlende Freigabe verhindert."""


class HoldoutViolation(LabError):
    """Verletzung des Holdout-Schutzes (wiederholter Zugriff)."""


def assert_candidate_transition(from_state: str, to_state: str) -> None:
    if from_state not in _ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"unbekannter Zustand: {from_state!r}")
    if to_state not in _ALLOWED_TRANSITIONS[from_state]:
        raise InvalidTransition(f"unzulässiger Übergang {from_state} -> {to_state}")


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class Candidate:
    """Ein Strategie-Kandidat im Pending-Workflow (unveränderlich; Übergänge erzeugen Kopien)."""

    candidate_id: str
    strategy_key: str
    params: dict = field(default_factory=dict)
    state: str = "generated"
    history: tuple[str, ...] = ("generated",)

    def transition(self, to_state: str) -> "Candidate":
        assert_candidate_transition(self.state, to_state)
        return replace(self, state=to_state, history=self.history + (to_state,))


# ── Champion/Candidate-Registry ──────────────────────────────────────────────

class ChampionCandidateRegistry:
    """Hält je Strategie genau EINEN Champion und beliebig viele Candidates."""

    def __init__(self):
        self._champions: dict[str, Candidate] = {}
        self._candidates: dict[str, list[Candidate]] = {}

    def add_candidate(self, candidate: Candidate) -> None:
        self._candidates.setdefault(candidate.strategy_key, []).append(candidate)

    def candidates(self, strategy_key: str) -> tuple[Candidate, ...]:
        return tuple(self._candidates.get(strategy_key, ()))

    def champion(self, strategy_key: str) -> Candidate | None:
        return self._champions.get(strategy_key)

    def set_initial_champion(self, candidate: Candidate) -> None:
        """Setzt den ersten Champion einer Strategie (nur, wenn noch keiner existiert)."""
        if candidate.strategy_key in self._champions:
            raise PromotionBlocked(
                f"{candidate.strategy_key} hat bereits einen Champion — nutze promote()")
        self._champions[candidate.strategy_key] = candidate

    def promote(self, candidate: Candidate, *, gate: "PromotionDecision",
                human_approved_by: str | None) -> Candidate:
        """Befördert einen Kandidaten zum Champion — nur bei bestandenem Gate UND Freigabe.

        Genau ein Champion je Strategie: der bisherige wird ersetzt (der Aufrufer archiviert ihn
        separat). `human_approved_by` ist Pflicht (Tor T3) — ohne Freigabe kein Live-Champion.
        """
        if not gate.passed:
            raise PromotionBlocked(f"Gate nicht bestanden: {', '.join(gate.reasons)}")
        if not human_approved_by:
            raise PromotionBlocked("menschliche Freigabe fehlt (Tor T3)")
        if candidate.state != "approved":
            raise PromotionBlocked(f"Kandidat muss 'approved' sein, ist {candidate.state!r}")
        self._champions[candidate.strategy_key] = candidate
        return candidate


# ── Promotion-Gates ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromotionCriteria:
    """Schwellwerte für die Promotion (konservative Defaults)."""

    min_trades: int = 100
    min_oos_windows_positive_ratio: float = 0.6   # Mehrheit der OOS-Fenster positiv
    max_drawdown_worsening_pct: float = 5.0        # ggü. Champion erlaubte DD-Verschlechterung
    require_positive_net: bool = True
    require_sensitivity_stable: bool = True
    require_shadow_confirmed: bool = True


@dataclass(frozen=True)
class CandidateEvidence:
    """Gemessene Belege eines Kandidaten (aus Backtest/Validierung/Shadow)."""

    n_trades: int
    oos_windows_positive_ratio: float
    net_performance_pct: float
    drawdown_pct: float
    champion_drawdown_pct: float
    sensitivity_stable: bool
    shadow_confirmed: bool


@dataclass(frozen=True)
class PromotionDecision:
    passed: bool
    reasons: tuple[str, ...]


def evaluate_promotion_gates(evidence: CandidateEvidence,
                             criteria: PromotionCriteria = PromotionCriteria()
                             ) -> PromotionDecision:
    """Prüft alle quantitativen Promotion-Gates. Die MENSCHLICHE Freigabe ist NICHT Teil davon —
    sie ist ein separater, nicht automatisierbarer Schritt in `ChampionCandidateRegistry.promote`.
    """
    reasons: list[str] = []
    if evidence.n_trades < criteria.min_trades:
        reasons.append(f"zu wenige Trades ({evidence.n_trades} < {criteria.min_trades})")
    if evidence.oos_windows_positive_ratio < criteria.min_oos_windows_positive_ratio:
        reasons.append("keine OOS-Mehrheit")
    dd_worsening = evidence.drawdown_pct - evidence.champion_drawdown_pct
    if dd_worsening > criteria.max_drawdown_worsening_pct:
        reasons.append(f"Drawdown verschlechtert sich um {dd_worsening:.1f}%")
    if criteria.require_positive_net and evidence.net_performance_pct <= 0:
        reasons.append("Netto-Performance nicht positiv")
    if criteria.require_sensitivity_stable and not evidence.sensitivity_stable:
        reasons.append("Sensitivität instabil")
    if criteria.require_shadow_confirmed and not evidence.shadow_confirmed:
        reasons.append("Shadow nicht bestätigt")
    return PromotionDecision(passed=not reasons, reasons=tuple(reasons))


# ── Holdout-Schutz (RES-005) ─────────────────────────────────────────────────

class HoldoutProtection:
    """Protokolliert Holdout-Zugriffe und verhindert wiederholtes Testen desselben Kandidaten."""

    def __init__(self, holdout_id: str):
        self.holdout_id = holdout_id
        self.access_log: list[dict] = []

    def _tested(self, candidate_id: str) -> bool:
        return any(entry["candidate_id"] == candidate_id for entry in self.access_log)

    def evaluate(self, candidate_id: str, fn, *args, **kwargs):
        """Wertet `fn` am Holdout aus — aber nur EINMAL je Kandidat (RES-005)."""
        if self._tested(candidate_id):
            raise HoldoutViolation(
                f"Kandidat {candidate_id} wurde bereits am Holdout '{self.holdout_id}' getestet")
        self.access_log.append({"candidate_id": candidate_id, "at": _utc_now_str()})
        return fn(*args, **kwargs)


# ── Suchraum-Begrenzung ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class SearchSpace:
    """Explizite Parametergrenzen + Obergrenze für Kandidaten je Zyklus."""

    bounds: dict           # name -> (low, high)
    max_candidates_per_cycle: int = 20

    def validate_params(self, params: dict) -> None:
        """Lehnt Parameter außerhalb der Grenzen oder unbekannte Parameter ab."""
        for name, value in params.items():
            if name not in self.bounds:
                raise LabError(f"unbekannter Parameter (kein Suchraum): {name!r}")
            low, high = self.bounds[name]
            if not (low <= value <= high):
                raise LabError(f"{name}={value} außerhalb [{low}, {high}]")

    def check_cycle_budget(self, n_candidates: int) -> None:
        if n_candidates > self.max_candidates_per_cycle:
            raise LabError(
                f"zu viele Kandidaten je Zyklus ({n_candidates} > {self.max_candidates_per_cycle})")


# ── LLM nur beschreibend ─────────────────────────────────────────────────────

# Muster, die auf ausführbaren Code / Parameter-Zuweisungen statt beschreibender Hypothese deuten.
_CODE_LIKE = re.compile(
    r"(=|;|\bimport\b|\bdef\b|\blambda\b|\breturn\b|\bexec\b|\beval\b|[\{\}\[\]()]|`)"
)


def validate_llm_hypothesis(text: str) -> str:
    """Stellt sicher, dass eine LLM-Ausgabe eine BESCHREIBENDE Hypothese ist — kein Code/Parameter.

    Wirft `LabError`, wenn der Text code-/zuweisungsartige Bestandteile enthält. Gibt den
    unveränderten Text zurück, wenn er rein beschreibend ist. Damit kann eine LLM-Ausgabe
    niemals Produktionscode oder Live-Parameter setzen (Gate P8).
    """
    if not text or not text.strip():
        raise LabError("leere Hypothese")
    if _CODE_LIKE.search(text):
        raise LabError("LLM-Hypothese enthält code-/parameterartige Bestandteile — nur "
                       "beschreibende Hypothesen erlaubt")
    return text.strip()
