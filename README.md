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

## Status — batch 1 of 8 (骨架 + CI)

Implemented (PROMPT §5, batch 1):

- App factory + lifespan seam (`app/main.py`)
- Settings via pydantic-settings (`app/core/settings.py`)
- Unified error format (`app/core/errors.py`, design-backend §1.2)
- Trust boundary: service token + `X-User-*` middleware (`app/core/security.py`, §1)
- Role → permission table + `GET /api/v1/authz/permissions` (`app/core/permissions.py`)
- CI: ruff + pytest + docker build (`.github/workflows/ci.yml`)

Not yet implemented (later batches): audit subsystem, engine read endpoints +
simulator, snapshot/trends, alarms, commands, approvals/training, retention,
deployment hardening. See `docs/DECISIONS.md` for per-batch rulings.

## Endpoints (batch 1)

| Method | Path | Auth |
|---|---|---|
| GET | `/api/v1/health` | none (healthcheck) |
| GET | `/api/v1/authz/permissions` | service token |

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

Brings up `api` (bound to `127.0.0.1:8000`) plus `postgres`/`redis` (internal
only; not used by the app until later batches).

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
  core/              # settings, errors, permissions, security (trust boundary)
  routers/           # health, authz  (governance/ and engine/ added later)
tests/               # unit + middleware tests
docs/                # 3 specs + DECISIONS.md
```
