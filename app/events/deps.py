"""FastAPI dependency providing an EventPublisher (overridable in tests)."""

from __future__ import annotations

from app.core.redis import get_redis
from app.events.publisher import EventPublisher


def get_publisher() -> EventPublisher:
    return EventPublisher(get_redis())
