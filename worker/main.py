"""arq worker entrypoint: ``arq worker.main.WorkerSettings``.

Batch 2 registers the hourly audit-chain re-verification (and a boot-time run so
the VERIFIED badge is populated immediately). Later batches add command-timeout
scanning, data cleanup, and the mock scheduler here.
"""

from __future__ import annotations

from typing import Any

from app.core.db import dispose_engine
from app.core.settings import get_settings
from arq import cron
from arq.connections import RedisSettings

from worker.tasks import reverify_audit_chain


async def _on_startup(ctx: dict[str, Any]) -> None:
    # Verify once at boot so /audit/chain/verify has a fresh result to serve.
    await reverify_audit_chain(ctx)


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")


class WorkerSettings:
    functions = [reverify_audit_chain]
    # Re-verify at the top of every hour (PROMPT §4: 稽核鏈重驗每小時).
    cron_jobs = [cron(reverify_audit_chain, minute=0)]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    redis_settings = _redis_settings()
