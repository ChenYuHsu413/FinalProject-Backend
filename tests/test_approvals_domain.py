"""Unit tests for the approval state machine — full transition table (§6.2)."""

from __future__ import annotations

import itertools

import pytest
from app.domain.approvals import (
    APPROVAL_STATES,
    APPROVAL_TYPES,
    APPROVED,
    MODEL_PROMOTION,
    PARAM_TUNING,
    PENDING,
    REJECTED,
    SCENARIO_ACTIVATION,
    TERMINAL_STATES,
    WITHDRAWN,
    InvalidApprovalTransition,
    can_transition,
    is_terminal,
    next_state,
)

# action → target used to derive legal (from, to) pairs from `pending`.
_LEGAL = {
    (PENDING, APPROVED),
    (PENDING, REJECTED),
    (PENDING, WITHDRAWN),
}

_ACTIONS = {"approve": APPROVED, "reject": REJECTED, "withdraw": WITHDRAWN}


def test_legal_decisions_from_pending():
    assert next_state(PENDING, "approve") == APPROVED
    assert next_state(PENDING, "reject") == REJECTED
    assert next_state(PENDING, "withdraw") == WITHDRAWN


def test_full_matrix_illegal_transitions_raise():
    for a, b in itertools.product(APPROVAL_STATES, repeat=2):
        assert can_transition(a, b) == ((a, b) in _LEGAL)


def test_double_decision_on_terminal_is_illegal():
    # A decided approval can never transition again (double-approve → 409).
    for terminal in TERMINAL_STATES:
        for action in _ACTIONS:
            with pytest.raises(InvalidApprovalTransition):
                next_state(terminal, action)


def test_terminal_states_have_no_exits():
    assert TERMINAL_STATES == {APPROVED, REJECTED, WITHDRAWN}
    for t in TERMINAL_STATES:
        assert is_terminal(t)
        for b in APPROVAL_STATES:
            assert not can_transition(t, b)
    assert not is_terminal(PENDING)


def test_unknown_action_raises():
    with pytest.raises(InvalidApprovalTransition):
        next_state(PENDING, "frobnicate")


def test_approval_types_are_the_three_governance_kinds():
    assert APPROVAL_TYPES == {MODEL_PROMOTION, SCENARIO_ACTIVATION, PARAM_TUNING}
