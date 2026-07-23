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

---

## Batch 4 — Snapshot + trends

### D4.1 Snapshot field fidelity + long-form scenario id

`GET /ui/snapshot?device=` mirrors design-backend §2 field-for-field (the Flask
normalizer depends on it). `dv`/`residual` values are the current samples from the
moving time-series (D4.5); `dv.delta_5min` and `residual.sigma3_margin_pct` are
**backend-computed** (batch-4 pre-check #1), not frontend-derived. The example in
§2 shows `scenario.id: "S01"`; we emit the **long form** `01_Pick_and_Place`
(PROMPT §3 #5 overrides the example).

### D4.2 Snapshot `alarms` block is a placeholder until batch 5  — RESOLVED (batch 5, D5.3)

The alarm subsystem is batch 5. Until then the snapshot's `alarms`
`{active, critical, warning, oldest_pending_s}` is a fixed representative
placeholder. **Resolved in batch 5**: the snapshot now queries real alarm counts
via `AlarmRepository.counts(device)` (see D5.3).

### D4.3 Device registry (static for now)

Devices resolve through `app/domain/devices.py` — currently a static map with the
single device `AXIS-04 → {cell: Hsinchu-CellA, line: Line02, scenario: 01_Pick_and_Place}`.
An unknown `device` query value → 404 (never flows through untrusted). Future
sourcing (config file / DB / discovery) is deferred; when added, keep the
`get_device()` interface so call sites don't change. Recorded per pre-check #4.

### D4.4 Trends response shape (§10 leaves it open)

design-backend §10 fixes the query (`metrics`, `window=1h|8h|24h`, `device`) and
the ≤500-points/series downsample rule but not the body shape. We return:
`{device, window, generated_at, series: {<metric>: {points: [{t, value}], threshold}}}`.
Each series is backend-downsampled to ≤500 points (§10.3 — the browser must not
accumulate). Unknown metric → 400; unknown device → 404; bad window → 422.

### D4.5 Moving, deterministic time-series (resolves batch-3 observation #1)

Batch 3's engine values were static constants (a frontend chart would flat-line).
`app/domain/timeseries.py` generates a moving series — base + sine + seeded noise
+ occasional spike — that is **deterministic** per `(metric, device, window)` via a
fixed digest seed (NOT builtin `hash()`, which is PYTHONHASHSEED-salted), so tests
are stable while the shape animates. The simulator's `l1:summary` event payload and
the `/l1/realtime` file (refreshed each worker tick) use `current_value`, so the
polling endpoint and the WS stream both move. The snapshot degrades gracefully —
it computes from the generator + registry and never 404s on missing engine files,
because it is the always-on first screen.

---

## Batch 5 — 警報 + 維修回報 (alarms + maintenance)

### D5.1 Alarm lifecycle state machine (pure)

`app/domain/alarms.py`: `active → acknowledged → resolved` (+ `active → resolved`
for system auto-resolve on residual recovery). All legal/illegal transitions are
pure functions with full-path unit tests (same standard as the command SM). `ack`
only claims/marks-read — it does NOT clear the device fault (frontend §8.3). An
illegal transition raises `InvalidAlarmTransition` → 409.

### D5.2 Fallback-escalation auto-alarm dedup

`AlarmService.raise_from_fallback` de-duplicates: if an **active** alarm already
exists for the same `(device, rule)`, it is updated (correlation refreshed), not
re-opened — a persisting escalation cannot flood the alarm centre. The fallback
event and the alarm share one `correlation_id`. Dedup lookup is indexed
(`ix_alarms_device_rule_status`). Only the *create* is audited (`alarm.raised`);
dedup updates are not, to avoid audit flooding.

### D5.3 Snapshot alarms are now real (supersedes D4.2)

The snapshot `alarms` block is computed from `AlarmRepository.counts(device)`
(active count, critical/warning breakdown, oldest-pending age) instead of the
batch-4 placeholder.

### D5.4 Alarm events

`alarm:new` / `alarm:updated` publish on `ai_servo:alarm` with the §11 envelope.
Publishing is **best-effort** (a Redis outage never fails the mutation); verified
with fakeredis. The API publishes to Redis only (the Flask BFF fans out to
browsers).

### D5.5 alarm.ack permission — admin is read-only

`alarm.ack` = operator + engineer; admin is read-only on alarm handling
(frontend §6.3). An admin ack attempt → 403 **and** an `authz.denied` audit row
(reverse-test enforced). 403 is documented on the alarm/maintenance routers so
the contract test accepts it (a single fuzz role can't satisfy every endpoint's
permission).

### D5.6 Input defense on the new write surface

ack `note`, resolve `maintenance_report_id`, and maintenance-report bodies are
length-capped + recursively NUL-checked (batch-2 lesson). schemathesis also
caught a NUL byte in a **path/query param** reaching a Postgres query
(`CharacterNotInRepertoireError` → 500); fixed globally in the trust-boundary
middleware, which now rejects NUL in path/query with 422 (documented on any
param'd endpoint). Body NUL stays handled by the per-model validators.

### D5.7 Maintenance report links alarm resolve; residual recovery is mock

Creating a maintenance report with an `alarm_id` also resolves that alarm (§8).
The "residual recovery observation" field (`residual_recovery_status`) is set to
`"observing"` — simulator-filled for now; a later batch wires it to the real
residual-recovery watcher.

### D5.8 ID generation

`alarm_id` / `report_id` are `ALM-<hex12>` / `MNT-<hex12>` (uuid-based, unique).
The §-example zero-padded sequence (`CMD-2026-000123`) would need a counter table;
uuid-based ids are sufficient for the mock and remain unique. Revisit if a
human-friendly monotonic sequence is required.

---

## Batch 6 — 命令子系統 (commands)

### D6.1 Command state machine (transition table)

`app/domain/commands.py`, pure logic. Legal transitions ONLY:

| from | to | who |
|---|---|---|
| submitted | accepted | downstream/device (mock confirmer) |
| submitted | rejected | validation/downstream |
| submitted | timeout | worker timeout scan |
| accepted | completed | device confirm |
| accepted | failed | device |
| accepted | timeout | worker timeout scan |

`completed / failed / timeout / rejected` are terminal (no outgoing transitions).
Every illegal transition raises `InvalidCommandTransition` → 409. Full transition
matrix is unit-tested.

### D6.2 Idempotency ≠ conflict

- **Idempotency**: DB unique `(command_type, device, idempotency_key)`. A duplicate
  submit returns the **original** command's current state with **HTTP 200** (not
  409). A concurrent duplicate that loses the insert race raises `IntegrityError`,
  which the service catches → rollback → return the original (concurrency test
  asserts exactly one row). Implements the frontend §10.2 double-click guard.
- **In-progress conflict** is separate: a `cycle.start` while a cycle is already
  running (most recent live cycle command is a start) → **409 CONFLICT**.

### D6.3 Timeout is worker-decided and terminal

Only the worker's `scan_command_timeouts` (every 1s) marks a command `timeout`
once `confirm_timeout_s` elapses since `submitted_at`. The API request path never
decides timeout. Timeout presumes **neither success nor failure** (PROMPT §7,
design-frontend §9.4) — HTTP 202 never means the device acted.

### D6.4 Mock device confirmer

`worker.mock_confirm_commands` (every 2s, MOCK_MODE) drives submitted→accepted
then accepted→completed over two ticks (realistic delay), and deliberately leaves
~20% of commands (deterministic by command_id hash) unconfirmed so they reach the
`timeout` path. This is the basis for the batch-8 deploy command-flow demo. Swaps
out for the real device/dispatcher interface later.

### D6.5 E-Stop Request

`safety.stop_request` command: `high_risk=True`, shorter confirm window
(`confirm_timeout_s=5` vs 10), audit carries the high-risk flag, and all three
roles may submit it (D1.5b). Same state machine; queue-priority is a future
concern (single mock device now).

### D6.6 Events

`command:status` publishes on **every** transition (submitted/accepted/completed/
failed/timeout); `mode:changed` publishes **only** when a `mode.switch` command
reaches `completed` (ruling #1 — the backend is authoritative, Flask does not
infer it). Both go on `ai_servo:command` with the §11 envelope, best-effort.

### D6.7 202 semantics

A fresh submit returns **202** with a body containing only submitted-semantics
fields (`command_id, status, submitted_at, confirm_timeout_s`) — no `result` /
`completed_at`. An idempotent replay returns **200** with the original command's
current state. Both documented on the router so the contract test accepts them.

### D6.8 In-progress conflict is a general rule; commands validate the device (batch-6 review)

Two fixes after the batch-6 review:

1. **General pending conflict** (design-backend §3.3, not just cycle-start-vs-running).
   `submit` rejects a new command when one of the same `(device, command_type)` is
   already in `submitted`/`accepted` → **409 CONFLICT** with
   `details.pending_command_id` so the UI can show "same command already awaiting
   confirmation". This applies to **E-Stop** too — a second request while one is
   pending returns 409 + the original command_id (correct and safe: the first is
   already in the highest-priority queue). `idempotency_key` only guards a single
   click's replay (→ 200); a new key seconds later is a *new* command and would
   otherwise stack duplicates (worsened by the mock confirmer leaving ~20%
   unconfirmed). The cycle *running* check (a completed start not yet stopped)
   remains as an additional cycle-specific conflict.
2. **Device validation on commands.** `submit` resolves the device via
   `app/domain/devices.py::get_device` → unknown device is **404**, consistent with
   the snapshot/trends endpoints (batch 4). Commands are a more dangerous surface
   than reads and must be at least as strict.

---

## Batch 7 — 治理核准 + 訓練 REST + 整合狀態 (approvals + training + integrations)

### D7.1 Approval state machine (pure)

`app/domain/approvals.py`: `pending → approved | rejected | withdrawn` (design-backend
§6.1 `state` field). All three decided states are **terminal** — a decided approval
never transitions again, so a double-approve (or approve-after-reject) raises
`InvalidApprovalTransition` → **409** (same standard as the command/alarm SMs, D6.1).
Full transition matrix unit-tested. IO-free: it knows nothing about who decided,
the summary, or side effects.

### D7.2 同人禁核 is 403, enforced at two layers

design-backend §6.2: `decided_by != proposed_by`, violation → **403** (not 409 —
409 is the double-approve/terminal case, D7.1). Enforced twice:

1. **Permission layer** (D1.5a): propose/approve codes are split per type, and no
   role holds both. Admin holds **no** propose code, so an admin propose is a 403
   and the path effectively does not exist (pre-check #1, reverse-tested). Engineer
   holds no approve code.
2. **Service layer** (`ApprovalService._check_can_decide`): `proposed_by == decided_by`
   → 403 regardless of permissions, *plus* a defence-in-depth re-check that the
   decider's role holds the per-type approve code. This is belt-and-suspenders so a
   future permission-table change cannot silently remove the guard (the explicit
   ask in pre-check #1).

Approve/reject are coarse-gated at the router on `approval.read` (admin-only, so a
non-admin approve → 403 **and** an `authz.denied` audit row, reverse-tested); the
service checks run on top.

### D7.3 Decision ≠ application (the model-promotion side-effect ruling)

Approving is the **first time the governance layer writes into an engine-layer
file** (`models.jsonl`). The decision (`state = approved`) and the side effect
(models.jsonl rewrite / param five-check) are **separate transactions**. If the
side effect fails, the approval **stays `approved`** — it is recorded
`side_effect_status = apply_failed` (registry write failed) / `failed` (a param
check failed) with a raised alarm, and is **never rolled back**. Rationale: on the
audit trail the approval *did happen*; pretending otherwise by reverting it would
be a lie. The admin sees the alarm + `apply_failed` and can retry/investigate.
Integration-tested both ways (apply success → `applied` + `model:changed`; missing
target version → `apply_failed` + alarm).

### D7.4 Event channels: governance vs deploy

`approval:new` / `approval:decided` publish on `ai_servo:governance` (§6.2).
`model:changed` publishes on **`ai_servo:l3_deploy`** — NOT governance — reusing
the existing deploy topic (§6.2 / design-frontend §9.3): payload
`{model_version, scenario, status: active, hash}`. `training:progress` reuses the
existing `ai_servo:l2_finetune` topic (§9 "沿用既有 topic"), payload gains
`job_id` / `progress_pct` / `status`. All best-effort §11 envelopes — a Redis
outage never fails the mutation (D6.6/D5.4 precedent).

### D7.5 param_tuning five-check chain + mock policy

`app/domain/param_tuning.py`: whitelist → type → bounds → rate-of-change →
device-state, **in that order**, short-circuiting at the first failure
(design-frontend §11.3, design-backend §6.2). Pure/IO-free, fully unit-tested. The
whitelist and rate cap are mock-stage initial values (design-backend §13 item 6 is
an open decision): `PARAM_WHITELIST = {Kp, Ki}`, `MAX_DELTA_PCT = 10.0`,
`SAFE_DEVICE_STATES = {idle, normal}`. The chain runs **after** approval (§6.2
"核准後...五重檢查"); any failure → `side_effect_status = failed` + audit + alarm
(D7.3), the approval itself stays `approved`.

### D7.6 Training-job state machine + mock progression + auto-proposal

`app/domain/training.py`: `queued → running → evaluating → shadow → passed`
(+ `failed` from evaluating/shadow, `cancelled` from any non-terminal). Terminal:
`passed/failed/cancelled`. The mock worker (`advance_training_jobs`, every 3s,
MOCK_MODE) walks a job one happy-path step per tick. Entering `shadow` registers a
shadow candidate in `models.jsonl`; entering `passed` **spawns a `model_promotion`
pending approval** proposed on behalf of the job's engineer (`proposed_by =
requested_by`) with the §6.1 summary (`from`/`to`/`rmse_improvement_pct`/
`shadow_passed`/`shadow_window_h`). This is the head of the batch-8 demo chain:
train → propose → approve → `model:changed`. Captured event sequence:
`training:progress`×4 → `approval:new` → `training:progress`(passed) →
`approval:decided` → `model:changed`.

### D7.7 Spec gaps filled (propose / withdraw / detail / cancel)

design-backend §6.2 lists only list/summary/approve/reject endpoints, but the
permission model (D1.5a) and the frontend proposal flows (§8.4/§8.5/§11.3) require
a propose path, and §6.1 names a `withdrawn` state with no endpoint. Filled:

- `POST /approvals` (propose) — per-type propose code (engineer); admin → 403.
- `POST /approvals/{id}/withdraw` — proposer-only (`proposed_by == caller` else
  403), `pending → withdrawn`.
- `GET /approvals/{id}` — detail read (`approval.read`).
- `POST /training/jobs/{id}/cancel` resulting state is `cancelled` (§9 names the
  endpoint, not the state).

### D7.8 models.jsonl status enum (no `candidate`)

The spec enum (資料規格書 §四 `/l3/models`) is **`active / shadow / rolled_back /
archived`** — there is **no `candidate`**. A `model_promotion` sets the target
version `shadow → active` and demotes the prior `active → archived`
(`ModelRegistryFileRepository.promote`). The rewrite is **atomic** (temp file +
`os.replace`) so a crash can never leave a truncated registry that would break
every `/l1/model` / `/l3/models` read. Missing file / absent target version →
`ModelRegistryError` → `apply_failed` (D7.3).

### D7.9 /system/integrations honesty flag + degrade-not-500

`GET /system/integrations` (design-backend §7) adds a top-level **`mock_mode`**
flag mandated by PROMPT §7 "全域約束" (誠實敘述 — never *imply* real hardware).
Each dependency is probed (Redis `ping`, Postgres `SELECT 1`, ≤2s timeout); a
failed probe degrades that service to `disconnected` and the endpoint **never
500s** (same best-effort discipline as the publisher). When anything is down it
emits a best-effort `system:connection` event on `ai_servo:system`
(design-frontend §9.3 gap-fill). `version_consistency.verified` + `components`
{api, dispatcher, schema} map to the admin "服務版本一致 VERIFIED" badge. Gated on
`system.settings` (admin — it is the admin integrations screen, §7.5).
