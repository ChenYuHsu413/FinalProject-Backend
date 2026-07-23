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

## Status — batch 4 of 8 done (Snapshot + trends)

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

Not yet implemented (later batches): alarms, commands, approvals/training,
retention, deployment hardening. See `docs/DECISIONS.md`.

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

Engine endpoints read files under `ENGINE_DATA_DIR`; missing data / unknown
scenario → documented **404**. In `MOCK_MODE` the worker generates the files and
publishes events (channels `ai_servo:*`, §11 envelope).

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
