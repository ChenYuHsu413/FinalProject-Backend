# AI SERVO PLATFORM — Backend

Governance + engine layers for the AI SERVO PLATFORM, as **one** FastAPI app
(different router groups), deployed on a single GCP Compute Engine VM via
docker compose. See `PROMPT_backend_new_repo.md` and the three specs in `docs/`
for the authoritative requirements.

## 是什麼 / 不是什麼 (What this is / is not)

**Is:** a governance layer (commands, alarms, audit hash-chain, approvals,
snapshot) over PostgreSQL, and a read-only engine layer serving ML-pipeline
output files. Honest about its limits.

**Is not:** it does **not** connect to real 50 kHz data or a real model service
yet — the engine layer is fed by a **mock simulator** producing spec-shaped
data (Stage A/B). It is **not** IEC 61508 certified, is **not** connected to
real EtherCAT/PLC hardware, and is **not** a functional-safety stop path. The
browser never connects directly to FastAPI/Redis/Postgres — only the Flask BFF
does, over a service token.

> **Mock mode** is surfaced honestly: `/api/v1/system/integrations` reports
> `"mock_mode": true` (from batch 3). HTTP 200/202 never means "the device did
> it" — commands can end in `timeout` (batch 6).

## Status — PROMPT §5 batches 1–7 done, plus the external model service

Implemented so far (PROMPT §5):

**Batch 1 — 骨架 + CI:** app factory + lifespan, settings, unified error format
(design-backend §1.2), trust boundary (service token + `X-User-*` middleware, §1),
role→permission table + `GET /api/v1/authz/permissions`, CI (ruff + pytest +
docker build).

**Batch 2 — 稽核子系統:** PostgreSQL append-only hash chain (`app/domain/audit.py`,
design-backend §5.1), 3-layer append-only protection in an Alembic migration
(REVOKE + BEFORE UPDATE/DELETE/TRUNCATE triggers), `/audit/*` endpoints, arq
worker hourly re-verify, and failed-attempt (`authz.denied`) auditing at the
trust boundary. All mutations flow through the audit service.

**Batch 3 — 引擎層唯讀端點 + mock simulator:** all engine GET endpoints
(後端資料規格書 §二/§七/§八/§九/§十) reading spec-shaped files under
`ENGINE_DATA_DIR` via an interface-first file repository (missing data → 404,
scenario ids path-validated); event envelope (§11) + Redis channels (§3.2); the
mock simulator generates the files and publishes enveloped events on the worker's
§十三 schedule. Swapping in the real pipeline touches only the file repository +
simulator.

**Batch 4 — Snapshot + trends:** `GET /ui/snapshot` (design-backend §2, field-exact,
backend-computed `delta_5min`/`sigma3_margin_pct`) and `GET /trends` (§10,
backend-downsampled ≤500 pts/series, 1h/8h/24h). A deterministic moving
time-series generator (`app/domain/timeseries.py`) drives dv/residual so charts
animate instead of flat-lining; a device registry resolves `device` (unknown →
404). This batch completes Stage A→B: the Flask frontend can consume real format
everywhere.

**Batch 5 — 警報 + 維修回報:** alarm lifecycle state machine
(`active→acknowledged→resolved`, pure logic in `app/domain/alarms.py`), `/alarms/*`
+ `/maintenance-reports` endpoints, fallback-escalation auto-opens an alarm with
`(device, rule)` dedup, `alarm:new`/`alarm:updated` events on `ai_servo:alarm`,
snapshot alarm counts now real. All mutations audited; `alarm.ack` = operator +
engineer (admin read-only).

**Batch 6 — 命令子系統:** command state machine
(`submitted→accepted→completed/failed/timeout` +`rejected`, pure in
`app/domain/commands.py`), `/commands/*` endpoints (202=submitted only),
idempotency via DB unique `(command_type, device, idempotency_key)` (replay→200,
distinct from in-progress conflict→409), worker-only timeout scan, mock device
confirmer, E-Stop (`high_risk`, shorter timeout, all roles), `command:status` on
every transition + `mode:changed` only on mode-command completion.

**Batch 7 — 治理核准 + 訓練 REST + 整合狀態:** approval state machine
(`pending→approved|rejected|withdrawn`, all decided states terminal → double-decide
= 409, pure in `app/domain/approvals.py`), `/approvals/*` (propose / withdraw /
detail filled the §6.2 spec gaps) and `/training/jobs/*`. An approved
`param_tuning` runs the five-check chain (whitelist → type → bounds →
rate-of-change → device-state, short-circuiting); a failure marks the side effect
`failed` + audits + opens an alarm while the approval stays `approved`. Training
jobs walk `queued→running→evaluating→shadow→passed` on the mock worker; entering
`shadow` registers a candidate in `models.jsonl` and `passed` auto-spawns a
`model_promotion` approval, closing the train→propose→approve→`model:changed`
loop (the registry rewrite is atomic). `GET /system/integrations` probes
Redis/Postgres, degrades to `disconnected` instead of 500ing, and carries the
mandated `mock_mode` flag.

**Batch 8 — 外部模型服務:** snapshot `dv` now comes from a real trained model over
HTTP instead of the deterministic generator. Two independently-swappable seams:
`app/domain/servo_features.py` (SEAM A — the aggregated feature row, data team)
and `app/repositories/http/model_service.py` (SEAM B — the inference service,
model team; changing models = `MODEL_SERVICE_URL` + one field mapping).
`MODEL_SOURCE` defaults to `mock`, so tests and CI make no outbound calls. Every
failure mode (timeout / 4xx / 5xx / malformed body) degrades silently back to the
generated value — the first screen must always render — and results are cached
for `MODEL_CACHE_TTL_S` because the measured RTT is ~0.9s. See DECISIONS D8.1.

Not yet implemented: **PROMPT §5 batch 8 — retention 合併端點 + 匯出 + 部署硬化**
(no `deploy/` directory yet: prod compose, Caddyfile, `gcp-setup.sh`, `deploy.sh`,
`pg_dump` backups, `docs/DEPLOYMENT.md`). Note the numbering collision: the
external-model-service work above is *also* labelled "batch 8" by its change order
and by DECISIONS D8.1 — they are different pieces of work. See
`docs/DECISIONS.md`.

## Endpoints

| Method | Path | Auth |
|---|---|---|
| GET | `/api/v1/health` | none (healthcheck) |
| GET | `/api/v1/authz/permissions` | service token |
| POST | `/api/v1/audit/events` | service token (no `X-User-*`) |
| GET | `/api/v1/audit/events` | `audit.read` (operator → own only) |
| GET | `/api/v1/audit/chain/verify` | `audit.read` |
| GET | `/api/v1/audit/export` | `audit.export` (admin) |
| GET | `/api/v1/l1/{realtime,latency,model}` | `dashboard.read` |
| GET | `/api/v1/l2/{latest,trend}` | `dashboard.read` |
| GET | `/api/v1/l3/{latest,shadow,models}` | `model.read` |
| GET | `/api/v1/shap/{diagnosis,summary}` | `dashboard.read` |
| GET | `/api/v1/fallback/{events,stats}` | `dashboard.read` |
| GET | `/api/v1/scenarios`, `/scenario-library` | `dashboard.read` |
| GET | `/api/v1/residual/status` | `dashboard.read` |
| GET | `/api/v1/ensemble/status` | `dashboard.read` |
| GET | `/api/v1/control-mode` | `dashboard.read` |
| GET | `/api/v1/data-lifecycle` | `dashboard.read` |
| GET | `/api/v1/ui/snapshot` | `dashboard.read` |
| GET | `/api/v1/trends` | `trend.read` |
| GET | `/api/v1/alarms`, `/alarms/{id}` | `alarm.read` |
| POST | `/api/v1/alarms/{id}/ack`, `/resolve` | `alarm.ack` (operator/engineer) |
| POST | `/api/v1/maintenance-reports` | `maintenance.report` |
| GET | `/api/v1/maintenance-reports` | `alarm.read` |
| POST | `/api/v1/commands/cycle/start` | `cycle.start` |
| POST | `/api/v1/commands/cycle/stop` | `cycle.stop` |
| POST | `/api/v1/commands/mode` | `mode.switch` |
| POST | `/api/v1/commands/estop-request` | `safety.stop_request` |
| GET | `/api/v1/commands`, `/commands/{id}` | `dashboard.read` |
| GET | `/api/v1/approvals`, `/approvals/summary`, `/approvals/{id}` | `approval.read` |
| POST | `/api/v1/approvals` (propose) | per-type propose code (engineer; admin → 403) |
| POST | `/api/v1/approvals/{id}/{approve,reject}` | `approval.read` + per-type approve code |
| POST | `/api/v1/approvals/{id}/withdraw` | proposer only (else 403) |
| POST | `/api/v1/training/jobs`, `/training/jobs/{id}/cancel` | `model.retrain` |
| GET | `/api/v1/training/jobs`, `/training/jobs/{id}` | `model.read` |
| GET | `/api/v1/shadow/comparisons` | `model.read` |
| GET | `/api/v1/system/integrations` | `system.settings` (admin) |

Engine endpoints read files under `ENGINE_DATA_DIR`; missing data / unknown
scenario → documented **404**. In `MOCK_MODE` the worker generates the files and
publishes events (channels `ai_servo:*`, §11 envelope).

`/ui/snapshot`'s `dv` calls the external model service when `MODEL_SOURCE=http`
and `MODEL_SERVICE_URL` is set; it degrades to the built-in generator on any
failure, so the endpoint's contract and status codes are unchanged either way.
The default (`MODEL_SOURCE=mock`) makes no outbound calls.

## Database & worker

```bash
# apply migrations (needs DATABASE_URL, e.g. from .env)
alembic upgrade head
# run the background worker (hourly audit-chain re-verify)
arq worker.main.WorkerSettings
```

With docker compose, run migrations once after the stack is up:

```bash
docker compose run --rm api alembic upgrade head
```

## Local development

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                            # set SERVICE_TOKEN
uvicorn app.main:app --reload
```

Then:

```bash
curl localhost:8000/api/v1/health
curl -H "Authorization: Bearer <SERVICE_TOKEN>" localhost:8000/api/v1/authz/permissions
```

### With docker compose

```bash
docker compose up --build
```

Brings up `api` (bound to `127.0.0.1:8000`), `worker`, and `postgres`/`redis`
(internal only). Run `alembic upgrade head` once before hitting audit endpoints.

## Tests & lint

```bash
ruff check .
ruff format --check .
pytest -q
```

## Layout

```
app/
  main.py            # app factory + lifespan
  core/              # settings, db, errors, permissions, security (trust boundary)
  domain/            # pure logic (audit hash chain)  — no IO
  repositories/pg/   # SQLAlchemy models + audit repository
  services/          # orchestration (audit service)
  routers/           # health, authz, governance/audit  (engine/ added later)
worker/              # arq worker (hourly audit-chain re-verify)
alembic/             # migrations (append-only audit protection)
tests/               # unit (no DB) + integration (needs PG) + schemathesis
docs/                # 3 specs + DECISIONS.md
```
