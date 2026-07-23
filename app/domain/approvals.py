"""Approval lifecycle state machine — pure logic, no IO (design-backend.md §6.2).

    pending ─→ approved
        │
        ├──→ rejected
        │
        └──→ withdrawn   (proposer retracts before a decision)

`approved` / `rejected` / `withdrawn` are all **terminal** — a decided approval
can never transition again, so a double-approve (or approve-after-reject) raises
``InvalidApprovalTransition`` → HTTP 409 (same standard as the command SM, D6.1).

This is deliberately IO-free: it knows nothing about who proposed/decided, the
summary payload, or the side effects of an approval. 同人禁核 (`decided_by !=
proposed_by`, §6.2) and the model-promotion side effect live in the service
layer — this module only governs the legal shape of the lifecycle.
"""

from __future__ import annotations

# --- States ------------------------------------------------------------------
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"

APPROVAL_STATES: frozenset[str] = frozenset({PENDING, APPROVED, REJECTED, WITHDRAWN})
TERMINAL_STATES: frozenset[str] = frozenset({APPROVED, REJECTED, WITHDRAWN})

# --- Approval types (design-backend §6.1) ------------------------------------
# The permission codes are split propose/approve per type (D1.5a); the type value
# stored on the record is one of these.
MODEL_PROMOTION = "model_promotion"
SCENARIO_ACTIVATION = "scenario_activation"
PARAM_TUNING = "param_tuning"

APPROVAL_TYPES: frozenset[str] = frozenset({MODEL_PROMOTION, SCENARIO_ACTIVATION, PARAM_TUNING})

# --- Decisions (proposer/approver actions) -----------------------------------
APPROVE = "approve"
REJECT = "reject"
WITHDRAW = "withdraw"

# Action → resulting terminal state.
_ACTION_TARGET: dict[str, str] = {
    APPROVE: APPROVED,
    REJECT: REJECTED,
    WITHDRAW: WITHDRAWN,
}

# Legal (from_state, to_state) transitions — everything leaves `pending` only.
_LEGAL: frozenset[tuple[str, str]] = frozenset(
    {
        (PENDING, APPROVED),
        (PENDING, REJECTED),
        (PENDING, WITHDRAWN),
    }
)


class InvalidApprovalTransition(Exception):
    """Attempted an illegal approval state transition (→ HTTP 409)."""

    def __init__(self, current: str, action: str) -> None:
        self.current = current
        self.action = action
        super().__init__(f"cannot {action} an approval in state {current!r}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: str, target: str) -> bool:
    return (current, target) in _LEGAL


def next_state(current: str, action: str) -> str:
    """Return the resulting state for a decision, or raise InvalidApprovalTransition.

    A decision on an already-terminal approval (double-approve, approve-after-reject,
    decide-after-withdraw) is illegal and raises → 409.
    """
    target = _ACTION_TARGET.get(action)
    if target is None or not can_transition(current, target):
        raise InvalidApprovalTransition(current, action)
    return target
