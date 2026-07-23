"""Unit tests for the training-job state machine (design-backend §9)."""

from __future__ import annotations

import itertools

import pytest
from app.domain.training import (
    CANCELLED,
    EVALUATING,
    FAILED,
    HAPPY_PATH,
    JOB_STATES,
    PASSED,
    QUEUED,
    RUNNING,
    SHADOW,
    TERMINAL_STATES,
    InvalidJobTransition,
    can_transition,
    is_terminal,
    next_happy,
    transition,
)

_LEGAL = {
    (QUEUED, RUNNING),
    (RUNNING, EVALUATING),
    (EVALUATING, SHADOW),
    (SHADOW, PASSED),
    (EVALUATING, FAILED),
    (SHADOW, FAILED),
    (QUEUED, CANCELLED),
    (RUNNING, CANCELLED),
    (EVALUATING, CANCELLED),
    (SHADOW, CANCELLED),
}


def test_full_matrix_matches_legal_set():
    for a, b in itertools.product(JOB_STATES, repeat=2):
        assert can_transition(a, b) == ((a, b) in _LEGAL)


def test_legal_transitions_pass():
    for a, b in _LEGAL:
        assert transition(a, b) == b


def test_illegal_transitions_raise():
    for a, b in itertools.product(JOB_STATES, repeat=2):
        if (a, b) not in _LEGAL:
            with pytest.raises(InvalidJobTransition):
                transition(a, b)


def test_terminal_states_have_no_exits():
    assert TERMINAL_STATES == {PASSED, FAILED, CANCELLED}
    for t in TERMINAL_STATES:
        assert is_terminal(t)
        for b in JOB_STATES:
            assert not can_transition(t, b)


def test_happy_path_walk():
    state = QUEUED
    walked = [state]
    while (nxt := next_happy(state)) is not None:
        state = transition(state, nxt)
        walked.append(state)
    assert walked == list(HAPPY_PATH)
    assert state == PASSED
    assert next_happy(PASSED) is None


def test_cancel_from_any_nonterminal():
    for s in (QUEUED, RUNNING, EVALUATING, SHADOW):
        assert can_transition(s, CANCELLED)
    for t in TERMINAL_STATES:
        assert not can_transition(t, CANCELLED)
