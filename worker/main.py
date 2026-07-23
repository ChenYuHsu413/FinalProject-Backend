"""arq worker entrypoint: ``arq worker.main.WorkerSettings``.

Runs the hourly audit-chain re-verification (batch 2) and the mock simulator's
scheduled event publishing (batch 3, per 後端資料規格書 §十三). On startup it also
generates the engine data files (MOCK_MODE) so the read endpoints have data
immediately, and verifies the audit chain once. Later batches add command-timeout
scanning and data cleanup here.
"""

from __future__ import annotations

from typing import Any

from app.core.db import dispose_engine
from app.core.settings import get_settings
from app.mock.simulator import MockSimulator
from arq import cron
from arq.connections import RedisSettings

from worker.tasks import (
    advance_training_jobs,
    mock_confirm_commands,
    reverify_audit_chain,
    scan_command_timeouts,
    sim_fallback_escalation,
    sim_fallback_event,
    sim_l1_summary,
    sim_l2_finetune,
    sim_shap_diagnosis,
)


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    # Generate the engine file tree so read endpoints have data at boot.
    if settings.mock_mode:
        MockSimulator(settings.engine_data_dir).generate_all()
    # Verify once at boot so /audit/chain/verify has a fresh result to serve.
    await reverify_audit_chain(ctx)


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")


class WorkerSettings:
    functions = [
        reverify_audit_chain,
        sim_l1_summary,
        sim_l2_finetune,
        sim_fallback_event,
        sim_shap_diagnosis,
        sim_fallback_escalation,
        scan_command_timeouts,
        mock_confirm_commands,
        advance_training_jobs,
    ]
    # Schedules per 後端資料規格書 §十三 (mock simulator + audit re-verify):
    cron_jobs = [
        cron(reverify_audit_chain, minute=0),  # hourly (PROMPT §4)
        cron(sim_l1_summary, second=set(range(60))),  # 1s L1 summary
        cron(sim_l2_finetune, second={0}),  # 1min L2 finetune
        cron(
            sim_fallback_event, minute=set(range(0, 60, 5)), second={0}
        ),  # event-type (mock: 5min)
        cron(
            sim_shap_diagnosis, minute=set(range(0, 60, 5)), second={30}
        ),  # event-type (mock: 5min)
        cron(
            sim_fallback_escalation, minute=set(range(0, 60, 10)), second={15}
        ),  # escalation -> auto-alarm (mock: 10min)
        cron(scan_command_timeouts, second=set(range(60))),  # 1s timeout scan (§3.1)
        cron(mock_confirm_commands, second=set(range(0, 60, 2))),  # mock device confirmer
        cron(advance_training_jobs, second=set(range(0, 60, 3))),  # mock training progression
    ]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    redis_settings = _redis_settings()
