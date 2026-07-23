"""Training-job state machine — pure logic, no IO (design-backend §9).

    queued ─→ running ─→ evaluating ─→ shadow ─→ passed
       │         │            │           │
       └─────────┴────────────┴───────────┴──→ cancelled   (POST /cancel, non-terminal only)
                                          └────→ failed     (any evaluation gate fails)

States (verbatim, design-backend §9): ``queued / running / evaluating / shadow /
passed / failed`` — aligned to design-frontend §8.4-3. ``cancelled`` is added for
``POST /training/jobs/{id}/cancel`` (spec has the endpoint but does not name the
resulting state — D7.7). ``passed / failed / cancelled`` are terminal.

The mock simulator advances a job one step along the happy path each worker tick
(D7.6); reaching ``passed`` is what spawns a ``model_promotion`` pending approval.
Like the command/alarm SMs this is IO-free and fully unit tested.
"""

from __future__ import annotations

# --- States ------------------------------------------------------------------
QUEUED = "queued"
RUNNING = "running"
EVALUATING = "evaluating"
SHADOW = "shadow"
PASSED = "passed"
FAILED = "failed"
CANCELLED = "cancelled"

JOB_STATES: frozenset[str] = frozenset(
    {QUEUED, RUNNING, EVALUATING, SHADOW, PASSED, FAILED, CANCELLED}
)
TERMINAL_STATES: frozenset[str] = frozenset({PASSED, FAILED, CANCELLED})

# The happy-path order the mock simulator walks (D7.6).
HAPPY_PATH: tuple[str, ...] = (QUEUED, RUNNING, EVALUATING, SHADOW, PASSED)

# Job types (design-backend §9 POST body).
FINETUNE = "finetune"
FULL_RETRAIN = "full_retrain"
JOB_TYPES: frozenset[str] = frozenset({FINETUNE, FULL_RETRAIN})

# Legal (from, to) transitions.
_LEGAL: frozenset[tuple[str, str]] = frozenset(
    {
        (QUEUED, RUNNING),
        (RUNNING, EVALUATING),
        (EVALUATING, SHADOW),
        (SHADOW, PASSED),
        # An evaluation gate can fail at evaluating or shadow.
        (EVALUATING, FAILED),
        (SHADOW, FAILED),
        # Cancel is allowed from any non-terminal state.
        (QUEUED, CANCELLED),
        (RUNNING, CANCELLED),
        (EVALUATING, CANCELLED),
        (SHADOW, CANCELLED),
    }
)

# Progress percentage per state (for the training:progress event payload, §9).
PROGRESS_PCT: dict[str, int] = {
    QUEUED: 0,
    RUNNING: 40,
    EVALUATING: 70,
    SHADOW: 90,
    PASSED: 100,
    FAILED: 100,
    CANCELLED: 100,
}


class InvalidJobTransition(Exception):
    """Attempted an illegal training-job state transition (→ HTTP 409)."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot transition training job {current!r} → {target!r}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: str, target: str) -> bool:
    return (current, target) in _LEGAL


def transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise InvalidJobTransition(current, target)
    return target


def next_happy(current: str) -> str | None:
    """The next state along the happy path, or None if already at/after ``passed``."""
    try:
        idx = HAPPY_PATH.index(current)
    except ValueError:
        return None
    if idx + 1 < len(HAPPY_PATH):
        return HAPPY_PATH[idx + 1]
    return None
