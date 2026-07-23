"""Training-job endpoints (design-backend §9).

* POST /training/jobs              — `model.retrain` (engineer); 202 queued
* GET  /training/jobs?status=&page= — `model.read`
* GET  /training/jobs/{id}         — `model.read`
* POST /training/jobs/{id}/cancel  — `model.retrain`
* GET  /shadow/comparisons?scenario= — `model.read`
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.permissions import MODEL_READ, MODEL_RETRAIN
from app.core.security import Principal, require_permission
from app.core.validation import reject_nul
from app.domain.training import JOB_TYPES
from app.events.deps import get_publisher
from app.events.publisher import EventPublisher
from app.repositories.pg.training_repo import TrainingRepository
from app.services.training_service import TrainingService

router = APIRouter(
    tags=["training"],
    responses={
        403: {"description": "role lacks permission"},
        404: {"description": "training job not found"},
        409: {"description": "illegal state transition (already terminal)"},
    },
)


class TrainingJobIn(BaseModel):
    type: str = Field(max_length=16)
    scenario_id: str = Field(max_length=64)
    reason: str | None = Field(default=None, max_length=1000)
    data_window: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _validate(self) -> TrainingJobIn:
        reject_nul(self.scenario_id)
        reject_nul(self.reason)
        reject_nul(self.data_window)
        if self.type not in JOB_TYPES:
            raise ValueError(f"type must be one of {sorted(JOB_TYPES)}")
        return self


class TrainingJobOut(BaseModel):
    job_id: str
    type: str
    scenario_id: str
    reason: str | None
    data_window: str | None
    status: str
    progress_pct: int
    rmse: float | None
    shadow_comparison: dict | None
    result_model_version: str | None
    approval_id: str | None
    requested_by: str | None
    correlation_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TrainingJobsPage(BaseModel):
    jobs: list[TrainingJobOut]
    total: int
    page: int
    page_size: int


class ShadowComparisonOut(BaseModel):
    job_id: str
    scenario_id: str
    status: str
    comparison: dict


class ShadowComparisonsOut(BaseModel):
    comparisons: list[ShadowComparisonOut]


@router.post("/training/jobs", response_model=TrainingJobOut, status_code=202)
async def create_training_job(
    body: TrainingJobIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(MODEL_RETRAIN)),
) -> TrainingJobOut:
    job = await TrainingService(session, publisher).create(
        type=body.type,
        scenario_id=body.scenario_id,
        reason=body.reason,
        data_window=body.data_window,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    response.status_code = 202  # queued; the worker advances it (§9)
    return TrainingJobOut.model_validate(job)


@router.get("/training/jobs", response_model=TrainingJobsPage)
async def list_training_jobs(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(MODEL_READ)),
    status: str | None = None,
    scenario_id: str | None = None,
    page: int = Query(default=1, ge=1, le=1_000_000),
    page_size: int = Query(default=50, ge=1, le=500),
) -> TrainingJobsPage:
    rows, total = await TrainingRepository(session).list(
        status=status, scenario_id=scenario_id, limit=page_size, offset=(page - 1) * page_size
    )
    return TrainingJobsPage(
        jobs=[TrainingJobOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/training/jobs/{job_id}", response_model=TrainingJobOut)
async def get_training_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(MODEL_READ)),
) -> TrainingJobOut:
    job = await TrainingRepository(session).get(job_id)
    if job is None:
        raise AppError(code="NOT_FOUND", message="training job not found", status_code=404)
    return TrainingJobOut.model_validate(job)


@router.post("/training/jobs/{job_id}/cancel", response_model=TrainingJobOut)
async def cancel_training_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    publisher: EventPublisher = Depends(get_publisher),
    principal: Principal = Depends(require_permission(MODEL_RETRAIN)),
) -> TrainingJobOut:
    job = await TrainingService(session, publisher).cancel(
        job_id,
        user_id=principal.user_id,
        role=principal.role,
        correlation_id=principal.correlation_id,
    )
    return TrainingJobOut.model_validate(job)


@router.get("/shadow/comparisons", response_model=ShadowComparisonsOut)
async def shadow_comparisons(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_permission(MODEL_READ)),
    scenario: str | None = None,
) -> ShadowComparisonsOut:
    jobs = await TrainingRepository(session).shadow_comparisons(scenario_id=scenario)
    return ShadowComparisonsOut(
        comparisons=[
            ShadowComparisonOut(
                job_id=j.job_id,
                scenario_id=j.scenario_id,
                status=j.status,
                comparison=j.shadow_comparison or {},
            )
            for j in jobs
        ]
    )
