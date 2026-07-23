# DECISIONS

Design rulings made while implementing the backend. Conflict-resolution rulings
from `PROMPT_backend_new_repo.md` §3 are authoritative; this file records the
per-batch decisions that §3 does not already fix.

---

## Batch 1 — 骨架 + CI

### D1.1 Repo root == backend root

The tree in PROMPT §4 is rooted at `backend/` and contains `docs/`. The new repo
already has `docs/` at its root, so the repo root **is** the backend root:
`app/`, `tests/`, `Dockerfile`, `docker-compose.yml` live at the repo root — no
nested `backend/backend/`. A sibling `frontend/` (existing Flask) will be wired
into compose in the deployment batch (§6), not now.

### D1.2 Error codes

Unified error envelope per design-backend §1.2. The enum there is illustrative;
we use the canonical set `VALIDATION_ERROR (400/422)`, `FORBIDDEN (403)`,
`NOT_FOUND (404)`, `CONFLICT (409)`, `UPSTREAM_TIMEOUT (504)`, plus an internal
`INTERNAL_ERROR (500)` for uncaught exceptions (message scrubbed, no internals
leaked — design-frontend §7.1). There is no dedicated `UNAUTHORIZED` code: a
bad/missing **service token** maps to `FORBIDDEN` (403), since the enum has no
401 member and the request is from an untrusted source, not an unauthenticated
user (users authenticate at Flask, not here).

### D1.3 Where the trust boundary is enforced

Service-token + `X-User-*` validation is ASGI middleware (`TrustBoundaryMiddleware`),
so it runs **before routing**. Consequences:

- Identity-header validation on a mutation fires before a 404/405 for an
  unmatched route (this is what the security tests rely on).
- `X-Correlation-ID` is generated if absent (so every response, including
  errors, carries one) and echoed back as a response header.

Exempt paths (no service token): `/api/v1/health` (container healthcheck has no
token), `/docs`, `/redoc`, `/openapi.json`, and `/`.

### D1.4 Header requirements: mutations vs reads

Per PROMPT §7, the three identity headers are **required on mutations**
(POST/PUT/PATCH/DELETE) → missing/invalid role = `400 VALIDATION_ERROR`. On
reads they are optional but parsed when present (needed later for per-user audit
filtering, design-backend §5.2). The service token itself is required on **all**
non-exempt requests, reads included.

### D1.5 Role → permission mapping

Single source of truth in `app/core/permissions.py`, exposed via
`GET /api/v1/authz/permissions` for Flask to sync (design-backend §1.1).
Derived from the design-frontend §6.3 matrix with these rules:

- `R` (view) on a **controllable** action does **not** grant that action's
  execute code; only `E`/`A` grant it.
- Admin is **not** a super-operator (design-frontend §6.1): admin gets read +
  governance/approval/settings codes, but **no** device-control codes
  (`cycle.*`, `mode.switch`, `safety.stop_request`) and **no** `alarm.ack`
  (admin is `R` on alarm handling).

Resulting grants:

| code | operator | engineer | admin |
|---|:--:|:--:|:--:|
| dashboard.read | ✓ | ✓ | ✓ |
| trend.read | ✓ | ✓ | ✓ |
| model.read | ✓ | ✓ | ✓ |
| alarm.read | ✓ | ✓ | ✓ |
| audit.read | ✓¹ | ✓ | ✓ |
| safety.stop_request | ✓ | ✓ | ✓ |
| cycle.start / cycle.stop | ✓ | — | — |
| mode.switch | ✓ | ✓ | — |
| alarm.ack | ✓ | ✓ | — |
| maintenance.report | ✓ | ✓ | — |
| model.retrain | — | ✓ | — |
| model.promote.propose | — | ✓ | — |
| scenario.activate.propose | — | ✓ | — |
| param.tune.propose | — | ✓ | — |
| approval.read | — | — | ✓ |
| model.promote.approve | — | — | ✓ |
| scenario.activate.approve | — | — | ✓ |
| param.tune.approve | — | — | ✓ |
| audit.export | — | — | ✓ |
| system.settings | — | — | ✓ |

¹ operator `audit.read` is scoped to their own entries by the backend
(design-backend §5.2); the code is granted, the filtering is enforced at the
query layer (batch 2).

### D1.5a Approval codes split propose/approve (revised after batch-1 review)

The original mapping had a single `model.promote` granted only to admin. That
conflates *proposing* an upgrade with *approving* it and would break batch 7:
engineers (design-frontend §6.3: Promotion/Scenario/調參 = engineer **E**,
admin **A**) could not even open an approval request, and admin would end up
proposing and approving the same item — violating 同人禁核 (design-backend §6.2).

Fix: each of the three approval types (model_promotion, scenario_activation,
param_tuning — design-backend §6.1) is split into two codes:

| type | propose (engineer) | approve (admin) |
|---|---|---|
| model promotion | `model.promote.propose` | `model.promote.approve` |
| scenario activation | `scenario.activate.propose` | `scenario.activate.approve` |
| param tuning | `param.tune.propose` | `param.tune.approve` |

No role holds both the propose and approve code for a type, so same-person
approval is impossible at the permission layer (defence in depth on top of the
`decided_by != proposed_by` check in §6.2). This resolves a spec debt in
design-backend §6.2, which specified only "對應 code" without the split.

### D1.5b safety.stop_request granted to all roles (conscious override)

"Admin is not a super-operator" (D1.5) correctly denies admin `cycle.*` and
`mode.switch`. But `safety.stop_request` is a **safety** action, not an
operational one: an E-Stop *request* mirrors the shop-floor convention that
anyone may hit the emergency stop. Blocking a role that can see a hazard from
requesting a stop would not survive a safety review. Therefore
`safety.stop_request` is granted to operator, engineer **and** admin — a
deliberate exception to least-privilege, not a matrix derivation. (The request
is only a request; real functional-safety stop remains the physical button /
safety PLC per design-frontend §8.6.)

### D1.6 CI scope

`ruff check` + `ruff format --check` + `pytest` + `docker build`. Heavy stack
members (schemathesis contract tests, fakeredis, SQLAlchemy/Alembic) are added
in the batches that first need them, to keep batch-1 CI fast and deterministic.

### D1.7 Deferred to later batches (explicitly not in batch 1)

DB pool / Redis client in lifespan, the mock simulator, `docker-compose.prod.yml`,
Caddy/Flask services in compose, and OpenAPI-driven schemathesis — all deferred.
The lifespan and compose file are structured as the seams where they attach.

**Open item for the deployment batch (§6):** when the existing Flask frontend is
added to compose, decide whether it is pulled as a pre-built image from another
repo/registry or built from a relative path (git submodule / sibling checkout).
Record the choice here at that time.
