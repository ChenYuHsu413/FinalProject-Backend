"""Command lifecycle state machine — pure logic, no IO (design-backend.md §3.1).

    submitted ─→ accepted ─→ completed
        │            └────────→ failed
        │            └────────→ timeout   (no device confirmation in time)
        └─→ rejected

`timeout` is decided ONLY by the worker's timeout scan, never by the API request
path, and is **terminal** — never presumed success or failure (PROMPT §7,
design-frontend §9.4). `completed` / `failed` / `timeout` / `rejected` are all
terminal.
"""

from __future__ import annotations

# States
SUBMITTED = "submitted"
ACCEPTED = "accepted"
COMPLETED = "completed"
FAILED = "failed"
TIMEOUT = "timeout"
REJECTED = "rejected"

COMMAND_STATES: frozenset[str] = frozenset(
    {SUBMITTED, ACCEPTED, COMPLETED, FAILED, TIMEOUT, REJECTED}
)
TERMINAL_STATES: frozenset[str] = frozenset({COMPLETED, FAILED, TIMEOUT, REJECTED})

# Command types (permission codes double as command_type — design-backend §3.2).
CYCLE_START = "cycle.start"
CYCLE_STOP = "cycle.stop"
MODE_SWITCH = "mode.switch"
SAFETY_STOP_REQUEST = "safety.stop_request"

COMMAND_TYPES: frozenset[str] = frozenset(
    {CYCLE_START, CYCLE_STOP, MODE_SWITCH, SAFETY_STOP_REQUEST}
)

# Legal (from, to) transitions.
_LEGAL: frozenset[tuple[str, str]] = frozenset(
    {
        (SUBMITTED, ACCEPTED),
        (SUBMITTED, REJECTED),
        (ACCEPTED, COMPLETED),
        (ACCEPTED, FAILED),
        (ACCEPTED, TIMEOUT),
        # A command can also time out while still only submitted (never accepted).
        (SUBMITTED, TIMEOUT),
    }
)


class InvalidCommandTransition(Exception):
    """Attempted an illegal command state transition (→ HTTP 409)."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot transition command {current!r} → {target!r}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: str, target: str) -> bool:
    return (current, target) in _LEGAL


def transition(current: str, target: str) -> str:
    """Validate a transition, returning the target state or raising."""
    if not can_transition(current, target):
        raise InvalidCommandTransition(current, target)
    return target
