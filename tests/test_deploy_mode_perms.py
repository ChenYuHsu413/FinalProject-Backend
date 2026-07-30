"""DEPLOY_MODE (full|lite) permission-topology switch — unit level (D1.8).

No DB: exercises the permission matrix and settings validation directly. The
behavioural approve-flow coverage (engineer approving over HTTP/service, and the
self-approval ban holding in lite) lives in test_approvals_integration.py.
"""

from __future__ import annotations

import pytest
from app.core.permissions import (
    APPROVAL_READ,
    MODEL_PROMOTE_APPROVE,
    MODEL_PROMOTE_PROPOSE,
    PARAM_TUNE_APPROVE,
    PARAM_TUNE_PROPOSE,
    SCENARIO_ACTIVATE_APPROVE,
    has_permission,
    permissions_table,
)
from pydantic import ValidationError

# The read grant + the two approve codes an engineer ends up with in lite.
_LITE_GRANTS = (APPROVAL_READ, MODEL_PROMOTE_APPROVE, PARAM_TUNE_APPROVE)
# The two approve codes lite adds on top; approval.read is baseline in full (B4).
_LITE_APPROVE_GRANTS = (MODEL_PROMOTE_APPROVE, PARAM_TUNE_APPROVE)


# --- (a) full: engineer reads the approvals list but cannot approve -----------
def test_full_engineer_reads_but_cannot_approve(deploy_mode_full):
    # B4: engineer holds approval.read in full mode (track own proposals)...
    assert has_permission("engineer", APPROVAL_READ)
    # ...but the per-type approve codes stay admin-only in full → decision blocked.
    for code in _LITE_APPROVE_GRANTS:
        assert not has_permission("engineer", code), code
    # Proposer side is unchanged — engineer still proposes in full.
    assert has_permission("engineer", MODEL_PROMOTE_PROPOSE)
    assert has_permission("engineer", PARAM_TUNE_PROPOSE)


def test_full_admin_holds_the_approve_codes(deploy_mode_full):
    for code in _LITE_GRANTS:
        assert has_permission("admin", code), code


# --- (b) lite: engineer gains read + approve for the two types ----------------
def test_lite_engineer_granted_approve_and_read(deploy_mode_lite):
    for code in _LITE_GRANTS:
        assert has_permission("engineer", code), code
    # Propose codes are untouched (additive grant, not a swap).
    assert has_permission("engineer", MODEL_PROMOTE_PROPOSE)
    assert has_permission("engineer", PARAM_TUNE_PROPOSE)


def test_lite_leaves_admin_unchanged(deploy_mode_lite):
    # Admin's set must be identical between modes — lite only widens engineer.
    with_lite = set(permissions_table()["admin"])
    from app.core.permissions import ROLE_PERMISSIONS

    assert with_lite == set(ROLE_PERMISSIONS["admin"])


# --- (d) both modes: engineer never gains scenario_activation approve ---------
@pytest.mark.parametrize("mode", ["full", "lite"])
def test_engineer_never_approves_scenario_activation(mode, request):
    request.getfixturevalue(f"deploy_mode_{mode}")
    assert not has_permission("engineer", SCENARIO_ACTIVATE_APPROVE)


# --- authz sync endpoint reflects the active mode ----------------------------
def test_permissions_table_reflects_mode(deploy_mode_full):
    assert MODEL_PROMOTE_APPROVE not in set(permissions_table()["engineer"])


def test_permissions_table_reflects_lite(deploy_mode_lite):
    eng = set(permissions_table()["engineer"])
    assert {MODEL_PROMOTE_APPROVE, PARAM_TUNE_APPROVE, APPROVAL_READ} <= eng
    assert SCENARIO_ACTIVATE_APPROVE not in eng


# --- (e) invalid DEPLOY_MODE fails fast at startup with a clear message -------
def test_invalid_deploy_mode_raises_at_construction(monkeypatch):
    from app.core.settings import Settings

    monkeypatch.setenv("DEPLOY_MODE", "turbo")
    with pytest.raises(ValidationError) as ei:
        Settings()
    # Message names the offending var and the allowed values.
    msg = str(ei.value)
    assert "DEPLOY_MODE" in msg
    assert "full" in msg and "lite" in msg


def test_deploy_mode_is_case_insensitive(monkeypatch):
    from app.core.settings import Settings

    monkeypatch.setenv("DEPLOY_MODE", "LITE")
    assert Settings().deploy_mode == "lite"
