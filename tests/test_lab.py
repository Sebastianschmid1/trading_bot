"""Tests für das Strategie-Labor (W6, Gate P8)."""

import pytest

from stockbot.research import lab
from stockbot.research.lab import (
    Candidate,
    CandidateEvidence,
    ChampionCandidateRegistry,
    HoldoutProtection,
    HoldoutViolation,
    InvalidTransition,
    LabError,
    PromotionBlocked,
    PromotionCriteria,
    SearchSpace,
    evaluate_promotion_gates,
    validate_llm_hypothesis,
)


def _good_evidence(**over):
    base = dict(n_trades=200, oos_windows_positive_ratio=0.8, net_performance_pct=12.0,
                drawdown_pct=15.0, champion_drawdown_pct=14.0, sensitivity_stable=True,
                shadow_confirmed=True)
    base.update(over)
    return CandidateEvidence(**base)


# ── Kandidaten-Zustandsmaschine ──────────────────────────────────────────────

def test_candidate_valid_lifecycle():
    c = Candidate("c1", "standard")
    for state in ("validated", "backtested", "shadow", "pending_review", "approved", "archived"):
        c = c.transition(state)
    assert c.state == "archived"
    assert c.history[0] == "generated" and c.history[-1] == "archived"


def test_candidate_rejects_invalid_transition_and_no_live_state():
    assert "live" not in lab.CANDIDATE_STATES        # „live" ist kein Kandidatenzustand
    with pytest.raises(InvalidTransition):
        Candidate("c", "generated").transition("approved")   # muss den Workflow durchlaufen
    with pytest.raises(InvalidTransition):
        Candidate("c", "archived").transition("approved")    # terminal


# ── Champion/Candidate-Registry ──────────────────────────────────────────────

def test_exactly_one_champion_per_strategy():
    reg = ChampionCandidateRegistry()
    reg.set_initial_champion(Candidate("champ0", "standard", state="approved"))
    with pytest.raises(PromotionBlocked):
        reg.set_initial_champion(Candidate("champ1", "standard", state="approved"))
    assert reg.champion("standard").candidate_id == "champ0"


def test_promotion_requires_gate_and_human_approval():
    reg = ChampionCandidateRegistry()
    reg.set_initial_champion(Candidate("champ0", "standard", state="approved"))
    cand = Candidate("cand1", "standard", state="approved")
    passed = evaluate_promotion_gates(_good_evidence())
    # ohne Freigabe: blockiert (Tor T3)
    with pytest.raises(PromotionBlocked):
        reg.promote(cand, gate=passed, human_approved_by=None)
    # mit Freigabe: neuer Champion
    reg.promote(cand, gate=passed, human_approved_by="sebastian")
    assert reg.champion("standard").candidate_id == "cand1"


def test_promotion_blocked_when_gate_failed():
    reg = ChampionCandidateRegistry()
    reg.set_initial_champion(Candidate("champ0", "standard", state="approved"))
    failed = evaluate_promotion_gates(_good_evidence(n_trades=10))
    with pytest.raises(PromotionBlocked):
        reg.promote(Candidate("c", "standard", state="approved"),
                    gate=failed, human_approved_by="sebastian")


# ── Promotion-Gates ──────────────────────────────────────────────────────────

def test_gate_passes_on_good_evidence():
    assert evaluate_promotion_gates(_good_evidence()).passed


@pytest.mark.parametrize("override,fragment", [
    (dict(n_trades=10), "zu wenige Trades"),
    (dict(oos_windows_positive_ratio=0.3), "OOS-Mehrheit"),
    (dict(drawdown_pct=30.0), "Drawdown"),
    (dict(net_performance_pct=-1.0), "Netto-Performance"),
    (dict(sensitivity_stable=False), "Sensitivität"),
    (dict(shadow_confirmed=False), "Shadow"),
])
def test_gate_flags_each_failure(override, fragment):
    decision = evaluate_promotion_gates(_good_evidence(**override))
    assert not decision.passed
    assert any(fragment in r for r in decision.reasons)


# ── Holdout-Schutz (RES-005) ─────────────────────────────────────────────────

def test_holdout_blocks_repeated_candidate_test():
    holdout = HoldoutProtection("2026H2")
    assert holdout.evaluate("cand1", lambda: 0.9) == 0.9
    with pytest.raises(HoldoutViolation):
        holdout.evaluate("cand1", lambda: 0.1)        # zweiter Zugriff desselben Kandidaten
    holdout.evaluate("cand2", lambda: 0.5)            # anderer Kandidat ok
    assert len(holdout.access_log) == 2


# ── Suchraum-Begrenzung ──────────────────────────────────────────────────────

def test_search_space_enforces_bounds_and_budget():
    space = SearchSpace(bounds={"rsi": (2, 30), "atr_mult": (1.0, 5.0)}, max_candidates_per_cycle=3)
    space.validate_params({"rsi": 14, "atr_mult": 2.5})       # ok
    with pytest.raises(LabError):
        space.validate_params({"rsi": 99})                    # außerhalb
    with pytest.raises(LabError):
        space.validate_params({"unknown": 1})                 # nicht im Suchraum
    with pytest.raises(LabError):
        space.check_cycle_budget(4)                           # zu viele Kandidaten


# ── LLM nur beschreibend ─────────────────────────────────────────────────────

def test_llm_hypothesis_accepts_descriptive_rejects_code():
    assert validate_llm_hypothesis(
        "Momentum wirkt stärker nach ruhigen Phasen mit steigendem Volumen"
    ).startswith("Momentum")
    for bad in ("rsi = 14", "import os", "def f(): return 1", "params[0]"):
        with pytest.raises(LabError):
            validate_llm_hypothesis(bad)
