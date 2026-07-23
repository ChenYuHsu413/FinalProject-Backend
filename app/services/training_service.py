"""Training-job orchestration (design-backend §9).

Triggering a job is a **mutation** (§9): it writes audit and publishes
`training:progress` (reusing the existing finetune topic, payload gains `job_id`
/ `progress_pct` — D7.6). In mock mode the worker walks a job along the happy
path (`queued→running→evaluating→shadow→passed`); reaching `shadow` registers a
shadow candidate in `models.jsonl`, and reaching `passed` **spawns a
`model_promotion` pending approval** proposed on behalf of the job's engineer —
this is the head of the batch-8 demo chain: train → propose → approve →
model:changed (D7.6).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.settings import get_settings
from app.domain.approvals import MODEL_PROMOTION
from app.domain.training import (
    CANCELLED,
    PASSED,
    PROGRESS_PCT,
    SHADOW,
    next_happy,
    transition,
)
from app.events import channels
from app.events.publisher import EventPublisher
from app.repositories.files.model_registry_repo import (
    ModelRegistryError,
    ModelRegistryFileRepository,
)
from app.repositories.pg.models import TrainingJob
from app.repositories.pg.training_repo import TrainingRepository
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService

logger = logging.getLogger("app.training")


def _now() -> datetime:
    return datetime.now(UTC)


def _new_job_id() -> str:
    return f"TRJ-{uuid4().hex[:12]}"


def _shadow_comparison(scenario_id: str, new_version: str) -> dict:
    """Representative shadow result (mirrors engine l3_shadow shape, §9 fields)."""
    return {
        "scenario": scenario_id,
        "new_version": new_version,
        "new_model": {"RMSE": 0.0172, "MAE": 0.011, "R2": 0.94, "latency_ms": 0.20},
        "old_model": {"RMSE": 0.0220, "MAE": 0.014, "R2": 0.91},
        "false_positive_rate": 0.01,
        "false_negative_rate": 0.02,
        "sample_coverage_pct": 98.5,
        "comparison": {"rmse_improvement_pct": 21.8, "threshold_met": True},
        "decision": "DEPLOY",
    }


class TrainingService:
    def __init__(self, session: AsyncSession, publisher: EventPublisher | None = None) -> None:
        self.session = session
        self.repo = TrainingRepository(session)
        self.publisher = publisher

    async def _publish_progress(self, job: TrainingJob) -> None:
        if self.publisher is None:
            return
        try:
            await self.publisher.publish(
                channel=channels.L2_FINETUNE,  # reuse existing finetune topic (§9, D7.6)
                event_type="training:progress",
                payload={
                    "job_id": job.job_id,
                    "status": job.status,
                    "progress_pct": job.progress_pct,
                    "scenario_id": job.scenario_id,
                    "RMSE": job.rmse,
                },
                scenario_id=job.scenario_id,
                correlation_id=job.correlation_id,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("failed to publish training:progress for %s", job.job_id, exc_info=True)

    async def create(
        self,
        *,
        type: str,
        scenario_id: str,
        reason: str | None,
        data_window: str | None,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> TrainingJob:
        job = TrainingJob(
            job_id=_new_job_id(),
            type=type,
            scenario_id=scenario_id,
            reason=reason,
            data_window=data_window,
            status="queued",
            progress_pct=PROGRESS_PCT["queued"],
            requested_by=user_id,
            requested_role=role,
            correlation_id=correlation_id,
        )
        await self.repo.add(job)
        await AuditService(self.session).record(
            action=f"training.trigger:{type}",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            scenario_id=scenario_id,
            new_value={"job_id": job.job_id, "type": type, "data_window": data_window},
            reason=reason,
            result="queued",
        )
        await self._publish_progress(job)
        return job

    async def _get_or_404(self, job_id: str) -> TrainingJob:
        job = await self.repo.get(job_id)
        if job is None:
            raise AppError(code="NOT_FOUND", message="training job not found", status_code=404)
        return job

    async def cancel(
        self,
        job_id: str,
        *,
        user_id: str | None,
        role: str | None,
        correlation_id: str | None,
    ) -> TrainingJob:
        job = await self._get_or_404(job_id)
        job.status = transition(job.status, CANCELLED)  # 409 if already terminal
        job.progress_pct = PROGRESS_PCT[CANCELLED]
        job.completed_at = _now()
        await AuditService(self.session).record(
            action="training.cancel",
            user_id=user_id,
            role=role,
            correlation_id=correlation_id,
            scenario_id=job.scenario_id,
            new_value={"job_id": job.job_id, "status": job.status},
            result="cancelled",
        )
        await self._publish_progress(job)
        return job

    async def advance(self, job: TrainingJob) -> TrainingJob:
        """Worker-only: step a job one state along the happy path (D7.6).

        On entering `shadow`: register a shadow candidate in models.jsonl + attach
        the shadow comparison. On entering `passed`: spawn a `model_promotion`
        pending approval proposed on behalf of the job's engineer.
        """
        nxt = next_happy(job.status)
        if nxt is None:
            return job  # terminal or off-path — nothing to do

        job.status = transition(job.status, nxt)
        job.progress_pct = PROGRESS_PCT[nxt]
        if job.status == "running" and job.started_at is None:
            job.started_at = _now()

        if nxt == SHADOW:
            await self._enter_shadow(job)
        elif nxt == PASSED:
            job.completed_at = _now()
            await self._spawn_promotion_approval(job)

        await AuditService(self.session).record(
            action=f"training.progress:{nxt}",
            correlation_id=job.correlation_id,
            scenario_id=job.scenario_id,
            new_value={
                "job_id": job.job_id,
                "status": job.status,
                "progress_pct": job.progress_pct,
            },
            result=job.status,
        )
        await self._publish_progress(job)
        return job

    async def _enter_shadow(self, job: TrainingJob) -> None:
        version = f"v1.0.4-job{job.id}"
        job.result_model_version = version
        job.rmse = 0.0172
        job.shadow_comparison = _shadow_comparison(job.scenario_id, version)
        # Register the shadow candidate so a later promotion has a row to flip.
        try:
            ModelRegistryFileRepository(get_settings().engine_data_dir).add_shadow(
                scenario_id=job.scenario_id,
                version=version,
                file_hash=f"sha-{uuid4().hex[:8]}",
                metrics={"RMSE": 0.0172},
                trained_at=_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except ModelRegistryError:
            # Registry unavailable in this env — the job still advances; a later
            # promotion apply will fail loudly (apply_failed + alarm, D7.3).
            logger.warning("could not register shadow candidate for %s", job.job_id, exc_info=True)

    async def _spawn_promotion_approval(self, job: TrainingJob) -> None:
        """A passed job proposes a model_promotion (proposed_by the job engineer)."""
        # Resolve the current active version (the promotion's `from`), if readable.
        from_version = None
        try:
            models = ModelRegistryFileRepository(get_settings().engine_data_dir).list_for_scenario(
                job.scenario_id
            )
            from_version = next(
                (m.get("version") for m in models if m.get("status") == "active"), None
            )
        except ModelRegistryError:
            pass

        summary = {
            "from": from_version,
            "to": job.result_model_version,
            "rmse_improvement_pct": 21.8,
            "shadow_passed": True,
            "shadow_window_h": 24,
        }
        approval = await ApprovalService(self.session, self.publisher).propose(
            type=MODEL_PROMOTION,
            summary=summary,
            reason=f"auto-proposed from passed training job {job.job_id}",
            risk="medium",
            scenario_id=job.scenario_id,
            device=None,
            user_id=job.requested_by,
            role=job.requested_role,
            correlation_id=job.correlation_id,
        )
        job.approval_id = approval.approval_id
