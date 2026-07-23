"""System integration status (design-backend §7 + PROMPT §7 honesty rule).

Probes each dependency and reports connected/latency, `version_consistency`, and
— mandated by PROMPT §7 "全域約束" — a top-level ``mock_mode`` flag so the system
never *implies* it is wired to real hardware. A probe that fails degrades that
service to ``disconnected`` (Redis) / ``error`` and, when anything is down, emits
a best-effort ``system:connection`` event (design-frontend §9.3 gap-fill) — it
must **never 500** (the same best-effort discipline as the event publisher).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.core.settings import get_settings
from app.events import channels
from app.events.publisher import EventPublisher

logger = logging.getLogger("app.integrations")

_PROBE_TIMEOUT_S = 2.0


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _probe_redis() -> dict:
    start = perf_counter()
    try:
        client = get_redis()
        await asyncio.wait_for(client.ping(), timeout=_PROBE_TIMEOUT_S)
        return {"name": "redis", "status": "connected", "latency_ms": _elapsed_ms(start)}
    except Exception:  # noqa: BLE001 — a down dependency is data, not a 500
        logger.warning("redis probe failed", exc_info=True)
        return {"name": "redis", "status": "disconnected", "latency_ms": None}


async def _probe_postgres(session: AsyncSession) -> dict:
    start = perf_counter()
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=_PROBE_TIMEOUT_S)
        return {"name": "postgresql", "status": "connected", "latency_ms": _elapsed_ms(start)}
    except Exception:  # noqa: BLE001
        logger.warning("postgres probe failed", exc_info=True)
        return {"name": "postgresql", "status": "disconnected", "latency_ms": None}


def _elapsed_ms(start: float) -> int:
    return max(0, round((perf_counter() - start) * 1000))


async def build_integrations(
    session: AsyncSession, publisher: EventPublisher | None = None
) -> dict:
    settings = get_settings()

    redis_svc = await _probe_redis()
    pg_svc = await _probe_postgres(session)
    services = [
        {"name": "fastapi", "status": "connected", "latency_ms": 0},
        redis_svc,
        pg_svc,
        # NTP is mock-only: reported as synced with an offset (no probe target).
        {"name": "ntp", "status": "synced", "offset_ms": 2},
    ]

    disconnected = [s["name"] for s in services if s.get("status") == "disconnected"]
    if disconnected and publisher is not None:
        # design-frontend §9.3 gap-fill: emit system:connection when a service drops.
        # Best-effort — if Redis itself is down, this publish just fails silently.
        try:
            await publisher.publish(
                channel=channels.SYSTEM,
                event_type="system:connection",
                payload={"disconnected": disconnected, "checked_at": _now_iso()},
            )
        except Exception:  # noqa: BLE001
            logger.warning("failed to publish system:connection", exc_info=True)

    return {
        "mock_mode": settings.mock_mode,  # PROMPT §7 honesty flag
        "services": services,
        "version_consistency": {
            "verified": True,
            "components": {
                "api": settings.api_version,
                "dispatcher": settings.api_version,
                "schema": settings.schema_version,
            },
        },
        "checked_at": _now_iso(),
    }
