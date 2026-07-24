"""External model service client (batch 8).

SEAM B — the inference service. Today it is a Hugging Face Space standing in for
the model team's deliverable; when they ship, only ``model_service_url`` and the
field mapping in ``_to_prediction`` change. Nothing outside this module knows
which service answered.

Two rules that are NOT optional:

* **Never propagate a failure.** ``snapshot_service`` must always render the
  first screen (its own docstring). Every error path here raises
  ``ModelServiceError``; the caller degrades to the deterministic generator.
* **Never call on every request.** Measured RTT is ~0.9 s and a free Space
  sleeps when idle. Results are cached for ``model_cache_ttl_s``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.core.settings import get_settings

_INFO_TTL_S = 300.0  # version / r2 rarely move


class ModelServiceError(Exception):
    """The model service could not be reached or returned an unusable body."""


@dataclass(frozen=True)
class ModelPrediction:
    dv: float  # 0..1 degradation value
    state: str  # LN | LO | MED | HI
    proba: dict[str, float]
    version: str | None = None


_cache: dict[str, tuple[float, ModelPrediction]] = {}
_info_cache: tuple[float, dict] | None = None
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_lock() -> asyncio.Lock:
    """The herd-collapsing lock, resolved per running loop.

    A module-level ``asyncio.Lock`` binds to the loop it is first *contended* on
    and raises on every other loop afterwards. One uvicorn worker has one loop
    for its lifetime, but the test suite gives each test its own — so resolve
    the lock against the current loop instead of at import time.
    """
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def _to_prediction(body: dict, version: str | None) -> ModelPrediction:
    """MAPPING — the only place the external field names appear."""
    try:
        dv = float(body["degradation_score"])
        state = str(body["predicted_health_state"])
        proba = {str(k): float(v) for k, v in dict(body["health_state_proba"]).items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelServiceError(f"unexpected model response: {exc}") from exc
    return ModelPrediction(dv=max(0.0, min(1.0, dv)), state=state, proba=proba, version=version)


async def _request(method: str, path: str, payload: dict | None = None) -> dict:
    s = get_settings()
    url = s.model_service_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=s.model_service_timeout_s) as c:
            r = await c.request(method, url, json=payload)
            r.raise_for_status()
            body = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelServiceError(str(exc)) from exc
    if not isinstance(body, dict):
        raise ModelServiceError(f"expected a JSON object, got {type(body).__name__}")
    return body


async def info() -> dict:
    """Model metadata (version, reg_r2). Cached; empty dict on failure."""
    global _info_cache
    s = get_settings()
    if not s.model_enabled:
        return {}
    now = time.monotonic()
    if _info_cache and now - _info_cache[0] < _INFO_TTL_S:
        return _info_cache[1]
    try:
        body = await _request("GET", "/servo/model_info")
    except ModelServiceError:
        return _info_cache[1] if _info_cache else {}
    _info_cache = (now, body)
    return body


async def predict(device: str, features: dict[str, float]) -> ModelPrediction:
    """Cached inference for one device. Raises ModelServiceError on failure."""
    s = get_settings()
    if not s.model_enabled:
        raise ModelServiceError("model service disabled (model_source != http)")

    hit = _cache.get(device)
    if hit and time.monotonic() - hit[0] < s.model_cache_ttl_s:
        return hit[1]

    async with _get_lock():  # collapse a thundering herd onto one ~0.9s call
        hit = _cache.get(device)
        if hit and time.monotonic() - hit[0] < s.model_cache_ttl_s:
            return hit[1]
        body = await _request("POST", "/servo/predict", {"features": features})
        meta = await info()
        pred = _to_prediction(body, meta.get("model_version"))
        _cache[device] = (time.monotonic(), pred)
        return pred


def reset_cache() -> None:
    """Test hook."""
    global _info_cache, _lock, _lock_loop
    _cache.clear()
    _info_cache = None
    _lock = _lock_loop = None
