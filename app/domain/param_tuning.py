"""param_tuning five-check chain — pure logic, no IO (design-frontend §11.3).

design-frontend §11.3: *"參數值必須由後端再次檢查白名單、型別、上下限、變化率與
設備狀態。"* — the backend re-validates every proposed param change through five
checks, **in order**: whitelist → type → bounds → rate-of-change → device-state.
design-backend §6.2 mirrors it: any failing check ⇒ the application is `failed`
and audited (the approval itself is still `approved` — the decision and the
application are separate, D7.3).

The whitelist and the max change-rate are **mock-stage initial values** (a spec
open item — design-backend §13 item 6: which Drive/PLC params are openable is
undecided). Kept tiny and explicit here so widening the whitelist later is a
one-line change with a test, not a scattered edit (D7.5).

This module is intentionally IO-free: it takes already-extracted scalars (from
the approval `summary`, §6.1) plus a `device_state` string the service supplies,
and returns which check failed. No DB, no files — so the whole chain is unit
tested without a running system.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Mock-stage policy (design-backend §13 item 6 — initial whitelist) --------
PARAM_WHITELIST: frozenset[str] = frozenset({"Kp", "Ki"})
# Max allowed magnitude of change in one tuning step (變化率 check). The §6.1
# example is delta_pct 2.8 within this bound; a jump like 25% is rejected.
MAX_DELTA_PCT: float = 10.0
# Device states in which a live param change is safe to apply (設備狀態 check).
SAFE_DEVICE_STATES: frozenset[str] = frozenset({"idle", "normal"})

# Check identifiers (returned as the failing-check name for the audit trail).
WHITELIST = "whitelist"
TYPE = "type"
BOUNDS = "bounds"
RATE = "rate_of_change"
DEVICE_STATE = "device_state"

# The chain, in the exact §11.3 order.
CHECK_ORDER: tuple[str, ...] = (WHITELIST, TYPE, BOUNDS, RATE, DEVICE_STATE)


@dataclass(frozen=True)
class ParamCheckResult:
    ok: bool
    failed_check: str | None = None
    reason: str | None = None


def _is_number(value: object) -> bool:
    # bool is an int subclass but is never a valid param value.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_param_tuning(
    *,
    param: object,
    new_value: object,
    allowed_range: object,
    delta_pct: object,
    device_state: str | None,
) -> ParamCheckResult:
    """Run the five checks in order, returning at the first failure.

    Inputs come from the param_tuning `summary` (`param`, `new`, `allowed_range`,
    `delta_pct`, §6.1) plus the resolved `device_state`. Returns ``ok=True`` only
    if all five pass.
    """
    # 1. whitelist — the param must be one the platform allows tuning.
    if param not in PARAM_WHITELIST:
        return ParamCheckResult(False, WHITELIST, f"param {param!r} is not in the tuning whitelist")

    # 2. type — the new value (and the range bounds) must be numeric.
    if not _is_number(new_value):
        return ParamCheckResult(False, TYPE, "new value is not numeric")
    if (
        not isinstance(allowed_range, (list, tuple))
        or len(allowed_range) != 2
        or not all(_is_number(b) for b in allowed_range)
    ):
        return ParamCheckResult(False, TYPE, "allowed_range must be a [min, max] number pair")

    # 3. bounds — new value within the per-approval allowed range.
    low, high = allowed_range[0], allowed_range[1]
    if not (low <= new_value <= high):
        return ParamCheckResult(
            False, BOUNDS, f"new value {new_value} outside allowed range [{low}, {high}]"
        )

    # 4. rate-of-change — the step magnitude must not exceed the policy cap.
    if not _is_number(delta_pct):
        return ParamCheckResult(False, RATE, "delta_pct is not numeric")
    if abs(delta_pct) > MAX_DELTA_PCT:
        return ParamCheckResult(
            False, RATE, f"change rate {delta_pct}% exceeds max {MAX_DELTA_PCT}%"
        )

    # 5. device-state — the device must be in a state where a live change is safe.
    if device_state not in SAFE_DEVICE_STATES:
        return ParamCheckResult(
            False, DEVICE_STATE, f"device state {device_state!r} does not allow a live param change"
        )

    return ParamCheckResult(True)
