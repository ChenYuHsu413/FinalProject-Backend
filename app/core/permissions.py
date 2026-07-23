"""Role → permission-code table — the single source of truth (design-backend.md §1.1).

The backend performs a second-layer permission check on mutations (it does not
trust that Flask already checked). This table is the authoritative mapping;
``GET /api/v1/authz/permissions`` exposes it so the Flask BFF can sync and avoid
drift.

Permission codes are drawn from the endpoint ``Permission`` columns in
design-backend.md (§3–§10) and the role matrix in design-frontend.md §6.3.
Rationale for each role's grants is recorded in docs/DECISIONS.md (batch 1).
"""

from __future__ import annotations

# --- Roles -------------------------------------------------------------------
OPERATOR = "operator"
ENGINEER = "engineer"
ADMIN = "admin"

VALID_ROLES: frozenset[str] = frozenset({OPERATOR, ENGINEER, ADMIN})

# --- Permission codes --------------------------------------------------------
DASHBOARD_READ = "dashboard.read"
TREND_READ = "trend.read"
CYCLE_START = "cycle.start"
CYCLE_STOP = "cycle.stop"
MODE_SWITCH = "mode.switch"
SAFETY_STOP_REQUEST = "safety.stop_request"
ALARM_READ = "alarm.read"
ALARM_ACK = "alarm.ack"
AUDIT_READ = "audit.read"
AUDIT_EXPORT = "audit.export"
APPROVAL_READ = "approval.read"
MODEL_READ = "model.read"
MODEL_RETRAIN = "model.retrain"
MAINTENANCE_REPORT = "maintenance.report"
SYSTEM_SETTINGS = "system.settings"

# Governance approval codes are split propose/approve so the proposer and the
# approver are never the same code — this is what makes 同人禁核 (design-backend
# §6.2) enforceable at the permission layer, and lets engineers create approval
# requests (design-frontend §6.3: Promotion/Scenario/調參 = engineer E, admin A)
# without holding the approve authority. The three approval types
# (model_promotion / scenario_activation / param_tuning, design-backend §6.1)
# all follow the same pattern.
MODEL_PROMOTE_PROPOSE = "model.promote.propose"
MODEL_PROMOTE_APPROVE = "model.promote.approve"
SCENARIO_ACTIVATE_PROPOSE = "scenario.activate.propose"
SCENARIO_ACTIVATE_APPROVE = "scenario.activate.approve"
PARAM_TUNE_PROPOSE = "param.tune.propose"
PARAM_TUNE_APPROVE = "param.tune.approve"

ALL_PERMISSIONS: tuple[str, ...] = (
    DASHBOARD_READ,
    TREND_READ,
    CYCLE_START,
    CYCLE_STOP,
    MODE_SWITCH,
    SAFETY_STOP_REQUEST,
    ALARM_READ,
    ALARM_ACK,
    AUDIT_READ,
    AUDIT_EXPORT,
    APPROVAL_READ,
    MODEL_READ,
    MODEL_RETRAIN,
    MODEL_PROMOTE_PROPOSE,
    MODEL_PROMOTE_APPROVE,
    SCENARIO_ACTIVATE_PROPOSE,
    SCENARIO_ACTIVATE_APPROVE,
    PARAM_TUNE_PROPOSE,
    PARAM_TUNE_APPROVE,
    MAINTENANCE_REPORT,
    SYSTEM_SETTINGS,
)

# --- Role → permission mapping ----------------------------------------------
# Read design-frontend.md §6.3: R=view, E=execute, A=approve, —=none.
# "execute"/"approve" grant the action code; a bare "R" for a controllable
# action does NOT grant its execute code. Admin is deliberately NOT a
# super-operator: no device *control* codes (cycle.*, mode.switch), no alarm.ack
# (design-frontend.md §6.1).
#
# Exception — safety.stop_request is granted to ALL roles: an E-Stop *request*
# is a safety action, not an operational one, and blocking anyone who can see a
# hazard from requesting a stop fails a safety review. This overrides the
# least-privilege default by conscious design decision (see docs/DECISIONS.md
# D1.5b), not by matrix derivation.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    OPERATOR: frozenset(
        {
            DASHBOARD_READ,
            TREND_READ,
            CYCLE_START,
            CYCLE_STOP,
            MODE_SWITCH,
            SAFETY_STOP_REQUEST,
            ALARM_READ,
            ALARM_ACK,
            AUDIT_READ,  # backend filters operator to their own entries (§5.2)
            MODEL_READ,
            MAINTENANCE_REPORT,
        }
    ),
    ENGINEER: frozenset(
        {
            DASHBOARD_READ,
            TREND_READ,
            MODE_SWITCH,
            SAFETY_STOP_REQUEST,
            ALARM_READ,
            ALARM_ACK,
            AUDIT_READ,
            MODEL_READ,
            MODEL_RETRAIN,
            MAINTENANCE_REPORT,
            # Proposer side of the governance approvals (design-frontend §6.3: E).
            MODEL_PROMOTE_PROPOSE,
            SCENARIO_ACTIVATE_PROPOSE,
            PARAM_TUNE_PROPOSE,
        }
    ),
    ADMIN: frozenset(
        {
            DASHBOARD_READ,
            TREND_READ,
            SAFETY_STOP_REQUEST,
            ALARM_READ,
            AUDIT_READ,
            AUDIT_EXPORT,
            APPROVAL_READ,
            MODEL_READ,
            SYSTEM_SETTINGS,
            # Approver side of the governance approvals (design-frontend §6.3: A).
            MODEL_PROMOTE_APPROVE,
            SCENARIO_ACTIVATE_APPROVE,
            PARAM_TUNE_APPROVE,
        }
    ),
}


# --- Approval type → propose/approve code maps (design-backend §6.1 + D1.5a) --
# The per-type codes an approval's propose/decide path requires. Keyed by the
# approval `type` value (domain/approvals). Because no role holds both the
# propose and the approve code for a type, same-person approval is impossible at
# the permission layer — defence in depth on top of the `decided_by !=
# proposed_by` runtime check (§6.2). Admin holds no propose code, so an admin can
# never reach a propose path (403); engineer holds no approve code.
PROPOSE_CODE: dict[str, str] = {
    "model_promotion": MODEL_PROMOTE_PROPOSE,
    "scenario_activation": SCENARIO_ACTIVATE_PROPOSE,
    "param_tuning": PARAM_TUNE_PROPOSE,
}
APPROVE_CODE: dict[str, str] = {
    "model_promotion": MODEL_PROMOTE_APPROVE,
    "scenario_activation": SCENARIO_ACTIVATE_APPROVE,
    "param_tuning": PARAM_TUNE_APPROVE,
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, frozenset()))


def permissions_table() -> dict[str, list[str]]:
    """Serializable role → sorted permission codes, for the authz sync endpoint."""
    return {role: permissions_for(role) for role in sorted(ROLE_PERMISSIONS)}
