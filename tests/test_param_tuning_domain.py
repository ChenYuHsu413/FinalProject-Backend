"""Unit tests for the param_tuning five-check chain (design-frontend §11.3)."""

from __future__ import annotations

from app.domain.param_tuning import (
    BOUNDS,
    DEVICE_STATE,
    RATE,
    TYPE,
    WHITELIST,
    check_param_tuning,
)


def _ok_kwargs(**over):
    base = dict(
        param="Kp",
        new_value=12.75,
        allowed_range=[10, 14],
        delta_pct=2.8,
        device_state="idle",
    )
    base.update(over)
    return base


def test_all_checks_pass():
    r = check_param_tuning(**_ok_kwargs())
    assert r.ok
    assert r.failed_check is None


def test_whitelist_is_first_gate():
    r = check_param_tuning(**_ok_kwargs(param="EvilParam"))
    assert not r.ok
    assert r.failed_check == WHITELIST


def test_non_numeric_value_fails_type():
    r = check_param_tuning(**_ok_kwargs(new_value="12.75"))
    assert r.failed_check == TYPE


def test_bool_is_not_a_valid_number():
    # bool is an int subclass — must be rejected by the type check.
    r = check_param_tuning(**_ok_kwargs(new_value=True))
    assert r.failed_check == TYPE


def test_malformed_range_fails_type():
    r = check_param_tuning(**_ok_kwargs(allowed_range=[10]))
    assert r.failed_check == TYPE


def test_out_of_bounds_fails():
    r = check_param_tuning(**_ok_kwargs(new_value=20.0))
    assert r.failed_check == BOUNDS


def test_excessive_rate_fails():
    r = check_param_tuning(**_ok_kwargs(delta_pct=25.0))
    assert r.failed_check == RATE


def test_unsafe_device_state_fails():
    r = check_param_tuning(**_ok_kwargs(device_state="running_cycle"))
    assert r.failed_check == DEVICE_STATE


def test_none_device_state_fails():
    r = check_param_tuning(**_ok_kwargs(device_state=None))
    assert r.failed_check == DEVICE_STATE


def test_check_order_whitelist_before_bounds():
    # A non-whitelisted param that is ALSO out of bounds must report whitelist
    # (the first failing check), proving the chain short-circuits in order.
    r = check_param_tuning(**_ok_kwargs(param="Nope", new_value=999.0))
    assert r.failed_check == WHITELIST
