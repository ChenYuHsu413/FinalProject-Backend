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

---

## Batch 2 — 稽核子系統 (audit)

### D2.1 Genesis block

The first entry's `prev_hash` is `GENESIS_HASH = "0" * 64` (64 hex zeros),
mirroring the fallback hash-chain convention in 後端資料規格書 §五
(`"prev_hash": "0000..."`). `verify_chain([])` on an empty table is vacuously
`verified: true`. Tests cover empty / single / multi / tampered / broken-link /
deleted-middle (`tests/test_audit_domain.py`).

### D2.2 Hash field scope (what enters `entry_hash`)

`entry_hash = SHA256(prev_hash + canonical_json(business_view(entry)))`, where
`business_view` is the fixed field list in `app/domain/audit.py::HASHED_FIELDS`.

- **Included** (all business fields): `event_id, ts, correlation_id, command_id,
  user_id, role, source_ip, action, target_device, scenario_id, old_value,
  new_value, reason, proposed_at, approved_at, executed_at, result,
  model_version, mode`.
- **Excluded**: `id` (DB autoincrement) and `created_at` (DB `server_default now()`)
  — DB-generated, so the hash can be computed *before* insert; and `entry_hash`
  itself (the output).
- `prev_hash` is mixed in by **concatenation** per the formula, not embedded in
  the JSON body.
- `ts` is the app-set business event time (UTC) and **is** hashed; `created_at`
  is separate DB bookkeeping and is **not**. Timestamps are rendered for hashing
  by one function (`_to_iso`, fixed-width `…%H:%M:%S.%fZ`) used both at write and
  at re-verify, so a value and its DB round-trip hash identically — no drift.

`canonical_json`: `sort_keys=True, separators=(",",":"), ensure_ascii=False`.
`ensure_ascii` is off so Chinese device/scenario names hash by their real bytes.
Single tested function — changing it breaks every stored hash.

Known edge (`old_value`/`new_value` are JSONB): the hash is computed from the
Python dict at write time; re-verify reads it back through JSONB. Key order is
irrelevant (we sort), and strings/ints/typical decimals round-trip identically,
so this is safe for the values we store. If a future field needs exotic numeric
precision inside these blobs, add a normalization step before hashing.

### D2.3 `/audit/chain/verify` returns the worker's last result

The endpoint does **not** live-recompute the whole table (a slow query at scale).
It returns the latest row from `audit_chain_verifications`, written by the worker
(`reverify_audit_chain`) hourly + once on worker startup. This matches the admin
UI "VERIFIED, last checked 14:18" (design-frontend §7.5). Before the first worker
run the endpoint returns `verified: null, reason: "pending first verification"`.

### D2.4 POST /audit/events is service-only, exempt from X-User-* headers

design-backend §5.2 marks `POST /audit/events` "(service token only)" — Flask
deposits its own events (login/logout/lockout) where the acting identity is in
the **body**, not the caller's session. So this single path is exempt from the
mutation X-User-* requirement (`_SERVICE_MUTATION_PATHS` in `security.py`); it
still requires the service token, and the correlation id still flows. This is a
deliberate, narrow exception to PROMPT §7's blanket "mutations need the three
headers" rule.

### D2.5 Append-only: 3 layers, and what each test can prove

1. **App layer** — `AuditRepository` exposes no update/delete method (by
   construction; not unit-testable).
2. **DB privileges** — `REVOKE UPDATE, DELETE ON audit_events FROM PUBLIC`
   (documents intent; the table *owner* bypasses REVOKE, so this is not the real
   backstop and is not what the test targets).
3. **Triggers** — `BEFORE UPDATE OR DELETE` (row) **and** `BEFORE TRUNCATE`
   (statement) triggers `RAISE EXCEPTION`. These block **even the owner**, so
   this is the real enforcement and the layer the integration test attacks
   directly (`test_trigger_blocks_update/delete/truncate`). TRUNCATE is covered
   because it bypasses row-level triggers.

The tamper-detection test bypasses the trigger with `ALTER TABLE … DISABLE
TRIGGER` (simulating a privileged DB attacker) to prove the hash chain still
*detects* the change even if a layer is defeated.

### D2.6 Concurrency: appends serialized by advisory lock

`append()` takes `pg_advisory_xact_lock(_AUDIT_LOCK_KEY)` before reading the head
hash and inserting, so concurrent writers cannot both read the same `prev_hash`
and fork the chain. The lock is transaction-scoped (released at COMMIT/ROLLBACK).

### D2.7 Migration re-entrancy & downgrade

The raw DDL (function/trigger) uses `CREATE OR REPLACE` / `DROP … IF EXISTS`, so
it is safe to re-apply on an already-migrated DB. `downgrade()` **raises**
rather than dropping `audit_events` — tearing down the tamper-evident record is
never an implicit operation (batch-2 acceptance).

### D2.8 Failed attempts are audited (recursion-safe)

The trust boundary records every rejection — bad service token, missing/invalid
identity headers, and permission denials — as an `authz.denied` audit row
(`record_denied_attempt`). This powers admin security monitoring
(design-frontend §7.5 "login failed ×3"). It opens its **own** session (the
middleware has no DI session) and **swallows all errors**, so an audit-write
failure can neither break the 4xx response nor trigger another audit write (no
recursion). Consequence to revisit: a flood of bad-token requests becomes a
flood of audit writes — rate-limiting is deferred to the deployment/hardening
batch.

### D2.9 Definition of "本機驗證" (local verification) — hardened after batch-2 CI failure

Batch 2 was first delivered green on unit + offline checks but **red in CI**: the
migration had never executed against a real PostgreSQL (Docker would not start on
the dev machine), and offline `alembic upgrade head --sql` structurally cannot
catch execution-time failures. From now on, "本機驗證" (or "verified") for any
batch touching the DB **must** include:

1. `alembic upgrade head` run against a **real PostgreSQL** (not `--sql`
   generation), with the actual output captured/attached to the delivery.
2. The full test suite (incl. PG integration + schemathesis) passing in a shell
   where **interfering env vars are already set** (e.g. `SERVICE_TOKEN`), so a
   test that only passes because of a clean local env is caught.

**The standard local tool** is `scripts/local_pg.ps1` (committed): it downloads
the official portable PostgreSQL 16 binaries (no Docker, no admin), initdb's a
trust-auth cluster under `.localpg/` (gitignored) on port 15432 (outside the
Hyper-V reserved exclusion ranges that block 5432/5433), and starts it. The
required verification is then:

```powershell
pwsh scripts/local_pg.ps1                      # start local PG :15432
$env:SERVICE_TOKEN="ci-test-token"             # interfering env (rule 2)
$env:DATABASE_URL="postgresql+asyncpg://postgres@localhost:15432/aiservo_test"
$env:TEST_DATABASE_URL=$env:DATABASE_URL
python -m alembic upgrade head                 # rule 1 — capture output
python -m pytest -q                            # full suite on real PG
```

This was run for the batch-2 fix: `alembic upgrade head` → `Running upgrade ->
0001_audit` (rc 0, idempotent on re-run); `pytest` → 57 passed; CI (real
`postgres:16` service) also green.

Root causes fixed in the post-review pass (each was masked until the previous
was fixed):

1. **Migration crashed** — `op.execute` strings bundled `DROP; CREATE;`; asyncpg
   accepts one command per prepared statement. Split into individual
   `op.execute()` calls. (This is why #D2.9 rule 1 exists — offline SQL hid it.)
2. **All tests 403** — `conftest` used `os.environ.setdefault("SERVICE_TOKEN")`,
   which no-ops when CI already sets `SERVICE_TOKEN=ci-test-token`; app and tests
   then disagreed on the token. Changed to forced assignment + cache clear.
   (This is why #D2.9 rule 2 exists.)
3. **schemathesis 500s** — three undocumented server errors: overlong strings vs
   `VARCHAR` widths (added `max_length` to `AuditEventIn` matching the columns);
   NUL bytes in text/JSONB (recursive `_reject_nul`, incl. dict keys); unbounded
   `page` overflowing the `OFFSET` int64 (`page` `le=1_000_000`).
4. **Ancillary** — `/audit/export` did not declare `text/csv` in its OpenAPI
   responses (content-type conformance); the validation error handler could 500
   while serializing a pydantic `ctx` exception object (added `_json_safe` in
   `errors.py`).

---

## Batch 3 — 引擎層唯讀端點 + mock simulator

### D3.1 scenario_id validation is the path-traversal guard

Scenario ids are long-form only (PROMPT §3 #5). `EngineFileRepository` validates
`is_wellformed_scenario` (strict `^\d{2}_[A-Za-z0-9_]+$`, no `/`/`.`/`..`) **before**
assembling any path, so an arbitrary string can never traverse the filesystem
(acceptance #3). Unknown / malformed scenario → `EngineDataNotFound` → documented
404. Active set is `01_Pick_and_Place` / `18_Ball_Screw` / `34_Rotor_Demag`.

### D3.2 Missing engine data is a 404, never a 500

A missing file under `ENGINE_DATA_DIR` (simulator hasn't produced it / scenario
untrained) is normal (acceptance #2): the repo raises `EngineDataNotFound`, a
single app-level handler maps it to the unified 404 envelope, and every engine
router declares 404 in its OpenAPI (`NOT_FOUND_RESPONSES`) so schemathesis'
status-code conformance accepts it. schemathesis hitting endpoints with no data /
random scenarios therefore gets documented 404s, not 500s.

### D3.3 Fallback events stored as JSONL in the mock

`/fallback/events` and `/fallback/stats` read a JSONL file (`fallback_events.jsonl`)
+ per-scenario stats JSON. The SQLite hash-chained fallback log of 後端資料規格書
§五 is engine-layer and **deferred** (PROMPT §3 #2 keeps it as engine concern);
the mock's JSONL is enough for the read endpoints' output fidelity. Swapping in the
real SQLite source later touches only `EngineFileRepository`.

### D3.4 Simulator runs in the worker, not the API (acceptance #4)

The mock simulator publishes events from **arq worker cron jobs** (§十三 cadence:
`l1:summary` 1s, `l2:finetune` 1min, `fallback`/`shap` event-type at 5min in mock)
and generates the engine file tree on worker startup — **not** an API lifespan
task. The API stays stateless; a simulator crash cannot take down API serving.
All events use the §11 envelope (`EventPublisher` + `make_envelope`); tests use
fakeredis. `MOCK_MODE=false` disables it (prod). FastAPI only publishes to Redis
and never opens a browser WebSocket (PROMPT §3 #3).

### D3.5 Output fidelity is enforced by response models

Engine responses use pydantic response models whose field names mirror 後端資料
規格書 §二/§七/§八/§九/§十 exactly (acceptance #1). FastAPI serializes only the
declared fields, so responses cannot drift from the spec that the Flask normalizer
depends on. Deeply nested / variable blobs (L3 pools, SHAP force plots) are typed
`dict`/`list` to pass through faithfully while keeping their container field names
exact. Engine reads require `dashboard.read` (all roles) — L3 uses `model.read` —
enforced via `require_permission`, on top of the service token.

### D3.6 Reads require identity headers → Flask BFF must always send them

Engine (and governance) **read** endpoints gate on a permission via
`require_permission`, which raises 400 if `X-User-Role` is absent/invalid. This is
**stricter than the middleware minimum** (D1.4: the trust boundary only *requires*
the three identity headers on mutations). Consequence and contract for the Flask
BFF: it must attach `X-Correlation-ID` / `X-User-ID` / `X-User-Role` on **every**
request — reads included — not only mutations. A read without a valid role → 400.
This is intentional: per-role read gating + a caller identity for auditing on
every call. (Confirmed per batch-3 review; complements D3.5.)
