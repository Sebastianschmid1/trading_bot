"""
Tests für die Signal-Zustandsmaschine (Phase 1, stockbot/core/state_machine.py, Plan.md §9.3).
"""

import pytest

from stockbot.core.domain import SignalStatus
from stockbot.core.state_machine import assert_signal_transition, signal_transition_allowed

VALID_SIGNAL_TRANSITIONS = [
    (SignalStatus.GENERATED, SignalStatus.FILTERED),
    (SignalStatus.FILTERED, SignalStatus.PUBLISHED),
    (SignalStatus.FILTERED, SignalStatus.REJECTED),
    (SignalStatus.FILTERED, SignalStatus.EXPIRED),
    (SignalStatus.PUBLISHED, SignalStatus.ACCEPTED),
    (SignalStatus.PUBLISHED, SignalStatus.REJECTED),
    (SignalStatus.PUBLISHED, SignalStatus.EXPIRED),
    (SignalStatus.PUBLISHED, SignalStatus.BLOCKED_BY_RISK),
    (SignalStatus.ACCEPTED, SignalStatus.ORDER_CREATED),
]


@pytest.mark.parametrize("from_status,to_status", VALID_SIGNAL_TRANSITIONS)
def test_valid_signal_transitions_allowed(from_status, to_status):
    assert signal_transition_allowed(from_status, to_status) is True
    assert_signal_transition(from_status, to_status)      # wirft nicht


def test_terminal_states_allow_no_further_transition():
    for terminal in (
        SignalStatus.REJECTED, SignalStatus.EXPIRED,
        SignalStatus.BLOCKED_BY_RISK, SignalStatus.ORDER_CREATED,
    ):
        for target in SignalStatus:
            assert signal_transition_allowed(terminal, target) is False


def test_cannot_skip_generated_to_published():
    assert signal_transition_allowed(SignalStatus.GENERATED, SignalStatus.PUBLISHED) is False


def test_cannot_reopen_from_rejected():
    assert signal_transition_allowed(SignalStatus.REJECTED, SignalStatus.PUBLISHED) is False


def test_assert_invalid_transition_raises_value_error():
    with pytest.raises(ValueError, match="Ungültiger Signal-Übergang"):
        assert_signal_transition(SignalStatus.GENERATED, SignalStatus.ORDER_CREATED)


def test_every_status_has_an_explicit_transition_entry():
    from stockbot.core.state_machine import SIGNAL_TRANSITIONS
    assert set(SIGNAL_TRANSITIONS.keys()) == set(SignalStatus)
