"""param_verify read endpoints (B3) — executor digital-twin verification reports.

Read-only surface over the JSON reports the bypass executor
(executor/run_executor.py) writes to ``ENGINE_DATA_DIR/param_verify/`` after a
param_tuning approval is applied (one file per approval). The frontend governance
/tuning page (F2+) reads these to render verified / rolled_back / skipped_stale
outcomes. Engine layer, so it is read-only and audit-free like its siblings and
gated on the existing engine read code (``model.read``). See DECISIONS D3.7.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions import MODEL_READ
from app.core.security import require_permission
from app.repositories.files.engine_repo import EngineFileRepository
from app.routers.engine.deps import NOT_FOUND_RESPONSES, get_engine_repo
from app.routers.engine.models import ParamVerifyPage, ParamVerifyReport

router = APIRouter(tags=["engine:param_verify"], responses=NOT_FOUND_RESPONSES)


@router.get("/engine/param-verify", response_model=ParamVerifyPage)
def list_param_verify(
    device: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=500),
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(MODEL_READ)),
) -> dict:
    items, total = repo.param_verify_list(device=device, limit=limit)
    return {"param_verify": items, "total": total, "limit": limit}


@router.get("/engine/param-verify/{report_id}", response_model=ParamVerifyReport)
def get_param_verify(
    report_id: str,
    repo: EngineFileRepository = Depends(get_engine_repo),
    _=Depends(require_permission(MODEL_READ)),
) -> dict:
    # Malformed id / missing file → EngineDataNotFound → documented 404.
    return repo.param_verify_get(report_id)
