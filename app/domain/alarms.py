"""Alarm lifecycle state machine — pure logic, no IO (design-backend.md §4.1).

    active → acknowledged → resolved
    active ─────────────→ resolved   (system auto-resolve on residual recovery)

`ack` only claims/marks-read; it does NOT clear the device fault (frontend §8.3).
`resolve` is reached from active or acknowledged (via maintenance report or
detected residual recovery). Every other transition is illegal.
"""

from __future__ import annotations

ACTIVE = "active"
ACKNOWLEDGED = "acknowledged"
RESOLVED = "resolved"

ALARM_STATES: frozenset[str] = frozenset({ACTIVE, ACKNOWLEDGED, RESOLVED})

# Severities (design-backend §4.1).
SEVERITIES: frozenset[str] = frozenset({"critical", "warning", "info"})

# Actions.
ACK = "ack"
RESOLVE = "resolve"

# Legal (from_state, to_state) transitions.
_LEGAL: frozenset[tuple[str, str]] = frozenset(
    {
        (ACTIVE, ACKNOWLEDGED),
        (ACTIVE, RESOLVED),
        (ACKNOWLEDGED, RESOLVED),
    }
)

# Action → target state.
_ACTION_TARGET: dict[str, str] = {ACK: ACKNOWLEDGED, RESOLVE: RESOLVED}


class InvalidAlarmTransition(Exception):
    """Attempted an illegal alarm state transition (→ HTTP 409)."""

    def __init__(self, current: str, action: str) -> None:
        self.current = current
        self.action = action
        super().__init__(f"cannot {action} an alarm in state {current!r}")


def can_transition(current: str, target: str) -> bool:
    return (current, target) in _LEGAL


def next_state(current: str, action: str) -> str:
    """Return the resulting state for an action, or raise InvalidAlarmTransition."""
    target = _ACTION_TARGET.get(action)
    if target is None:
        raise InvalidAlarmTransition(current, action)
    if not can_transition(current, target):
        raise InvalidAlarmTransition(current, action)
    return target
