"""Event envelope + simulator publishing (fakeredis)."""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
from app.events import channels
from app.events.envelope import make_envelope
from app.events.publisher import EventPublisher
from app.mock import simulator


def test_envelope_shape():
    env = make_envelope(
        event_type="l1:summary",
        payload={"DV_mean": 0.13},
        scenario_id="01_Pick_and_Place",
        correlation_id="cid-1",
    )
    d = env.model_dump()
    assert set(d) == {
        "event_id",
        "event_type",
        "timestamp",
        "scenario_id",
        "schema_version",
        "correlation_id",
        "payload",
    }
    assert d["schema_version"] == "1.0"
    assert d["event_type"] == "l1:summary"
    assert d["timestamp"].endswith("Z")


async def test_publisher_returns_and_sends_envelope():
    redis = fakeredis.aioredis.FakeRedis()
    pub = EventPublisher(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channels.L1_SUMMARY)

    await simulator.publish_l1_summary(pub, "01_Pick_and_Place")
    received = None
    for _ in range(10):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg is not None:
            received = msg
            break
    assert received is not None, "no message delivered"
    body = json.loads(received["data"])
    assert body["event_type"] == "l1:summary"
    assert body["scenario_id"] == "01_Pick_and_Place"
    assert body["payload"]["DV_mean"] == 0.13
    assert body["event_id"] and body["schema_version"] == "1.0"
    await pubsub.unsubscribe(channels.L1_SUMMARY)
    await redis.aclose()


@pytest.mark.parametrize(
    "fn,channel,event_type",
    [
        (simulator.publish_l2_finetune, channels.L2_FINETUNE, "l2:finetune"),
        (simulator.publish_fallback_event, channels.FALLBACK_EVENT, "fallback:event"),
        (simulator.publish_shap_diagnosis, channels.SHAP_DIAGNOSIS, "shap:diagnosis"),
    ],
)
async def test_other_simulator_events(fn, channel, event_type):
    redis = fakeredis.aioredis.FakeRedis()
    pub = EventPublisher(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    await fn(pub, "18_Ball_Screw")
    received = None
    for _ in range(10):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg is not None:
            received = msg
            break
    assert received is not None
    body = json.loads(received["data"])
    assert body["event_type"] == event_type
    assert body["scenario_id"] == "18_Ball_Screw"
    await pubsub.unsubscribe(channel)
    await redis.aclose()
