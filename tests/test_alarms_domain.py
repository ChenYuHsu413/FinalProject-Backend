"""Unit tests for the alarm state machine — every legal + illegal path."""

from __future__ import annotations

import pytest
from app.domain.alarms import (
    ACK,
    ACKNOWLEDGED,
    ACTIVE,
    RESOLVE,
    RESOLVED,
    InvalidAlarmTransition,
    can_transition,
    next_state,
)


def test_legal_transitions():
    assert next_state(ACTIVE, ACK) == ACKNOWLEDGED
    assert next_state(ACTIVE, RESOLVE) == RESOLVED
    assert next_state(ACKNOWLEDGED, RESOLVE) == RESOLVED


def test_can_transition_matrix():
    assert can_transition(ACTIVE, ACKNOWLEDGED)
    assert can_transition(ACTIVE, RESOLVED)
    assert can_transition(ACKNOWLEDGED, RESOLVED)
    # illegal
    assert not can_transition(ACKNOWLEDGED, ACTIVE)
    assert not can_transition(RESOLVED, ACKNOWLEDGED)
    assert not can_transition(RESOLVED, ACTIVE)
    assert not can_transition(ACTIVE, ACTIVE)


def test_ack_on_acknowledged_is_illegal():
    with pytest.raises(InvalidAlarmTransition):
        next_state(ACKNOWLEDGED, ACK)


def test_ack_on_resolved_is_illegal():
    with pytest.raises(InvalidAlarmTransition):
        next_state(RESOLVED, ACK)


def test_resolve_on_resolved_is_illegal():
    with pytest.raises(InvalidAlarmTransition):
        next_state(RESOLVED, RESOLVE)


def test_unknown_action_is_illegal():
    with pytest.raises(InvalidAlarmTransition):
        next_state(ACTIVE, "explode")
