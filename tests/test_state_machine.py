"""
Tests für die Signal-Zustandsmaschine (Phase 1, stockbot/core/state_machine.py, Plan.md §9.3).
"""

import pytest

from stockbot.core.domain import OrderStatus, SignalStatus
from stockbot.core.state_machine import (
    assert_order_transition,
    assert_signal_transition,
    order_transition_allowed,
    signal_transition_allowed,
)

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


VALID_ORDER_TRANSITIONS = [
    (OrderStatus.CREATED, OrderStatus.VALIDATED),
    (OrderStatus.CREATED, OrderStatus.REJECTED),
    (OrderStatus.VALIDATED, OrderStatus.SUBMITTED),
    (OrderStatus.VALIDATED, OrderStatus.REJECTED),
    (OrderStatus.SUBMITTED, OrderStatus.ACCEPTED_BY_BROKER),
    (OrderStatus.SUBMITTED, OrderStatus.REJECTED),
    (OrderStatus.SUBMITTED, OrderStatus.EXPIRED),
    (OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.FILLED),
    (OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.CANCEL_REQUESTED),
    (OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.REJECTED),
    (OrderStatus.ACCEPTED_BY_BROKER, OrderStatus.EXPIRED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.REJECTED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED),
    (OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED),
    (OrderStatus.CANCEL_REQUESTED, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.CANCEL_REQUESTED, OrderStatus.FILLED),
]


@pytest.mark.parametrize("from_status,to_status", VALID_ORDER_TRANSITIONS)
def test_valid_order_transitions_allowed(from_status, to_status):
    assert order_transition_allowed(from_status, to_status) is True
    assert_order_transition(from_status, to_status)        # wirft nicht


def test_order_terminal_states_allow_no_further_transition():
    for terminal in (
        OrderStatus.FILLED, OrderStatus.CANCELLED,
        OrderStatus.REJECTED, OrderStatus.EXPIRED,
    ):
        for target in OrderStatus:
            assert order_transition_allowed(terminal, target) is False


def test_order_cannot_skip_created_to_submitted():
    assert order_transition_allowed(OrderStatus.CREATED, OrderStatus.SUBMITTED) is False


def test_order_cannot_reopen_from_cancelled():
    assert order_transition_allowed(OrderStatus.CANCELLED, OrderStatus.PARTIALLY_FILLED) is False


def test_assert_invalid_order_transition_raises_value_error():
    with pytest.raises(ValueError, match="Ungültiger Order-Übergang"):
        assert_order_transition(OrderStatus.CREATED, OrderStatus.FILLED)


def test_every_order_status_has_an_explicit_transition_entry():
    from stockbot.core.state_machine import ORDER_TRANSITIONS
    assert set(ORDER_TRANSITIONS.keys()) == set(OrderStatus)
