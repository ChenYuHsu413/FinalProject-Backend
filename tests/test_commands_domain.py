"""Unit tests for the command state machine — full transition table."""

from __future__ import annotations

import itertools

import pytest
from app.domain.commands import (
    ACCEPTED,
    COMMAND_STATES,
    COMPLETED,
    FAILED,
    REJECTED,
    SUBMITTED,
    TERMINAL_STATES,
    TIMEOUT,
    InvalidCommandTransition,
    can_transition,
    is_terminal,
    transition,
)

_LEGAL = {
    (SUBMITTED, ACCEPTED),
    (SUBMITTED, REJECTED),
    (SUBMITTED, TIMEOUT),
    (ACCEPTED, COMPLETED),
    (ACCEPTED, FAILED),
    (ACCEPTED, TIMEOUT),
}


def test_legal_transitions_pass():
    for a, b in _LEGAL:
        assert transition(a, b) == b


def test_full_matrix_illegal_transitions_raise():
    for a, b in itertools.product(COMMAND_STATES, repeat=2):
        if (a, b) in _LEGAL:
            assert can_transition(a, b)
        else:
            assert not can_transition(a, b)
            with pytest.raises(InvalidCommandTransition):
                transition(a, b)


def test_terminal_states():
    assert TERMINAL_STATES == {COMPLETED, FAILED, TIMEOUT, REJECTED}
    for t in TERMINAL_STATES:
        assert is_terminal(t)
        # nothing leaves a terminal state
        for b in COMMAND_STATES:
            assert not can_transition(t, b)
    assert not is_terminal(SUBMITTED)
    assert not is_terminal(ACCEPTED)


def test_timeout_reachable_from_submitted_and_accepted():
    assert can_transition(SUBMITTED, TIMEOUT)
    assert can_transition(ACCEPTED, TIMEOUT)
