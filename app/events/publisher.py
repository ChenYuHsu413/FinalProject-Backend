"""Redis event publisher.

Thin wrapper around a redis.asyncio client so the simulator/worker publish
enveloped events, and tests can inject fakeredis. Publishing is best-effort at
the call sites that need it, but this class itself surfaces errors — callers
decide whether to swallow.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.events.envelope import EventEnvelope, make_envelope


class _RedisLike(Protocol):
    async def publish(self, channel: str, message: str) -> Any: ...


class EventPublisher:
    def __init__(self, redis: _RedisLike) -> None:
        self._redis = redis

    async def publish_envelope(self, channel: str, envelope: EventEnvelope) -> None:
        await self._redis.publish(channel, envelope.model_dump_json())

    async def publish(
        self,
        *,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        scenario_id: str | None = None,
        correlation_id: str | None = None,
    ) -> EventEnvelope:
        envelope = make_envelope(
            event_type=event_type,
            payload=payload,
            scenario_id=scenario_id,
            correlation_id=correlation_id,
        )
        await self.publish_envelope(channel, envelope)
        return envelope
