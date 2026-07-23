"""Deterministic moving time-series for the mock (batch-4 observation #1).

Batch 3's engine values were static constants, so a frontend chart would be a
flat line. This produces a *moving* series — base + sine + seeded noise +
occasional spike — that is fully **deterministic** for a given
``(metric, device, window, seed, end)``, so tests are stable while the shape
still animates over time. Downsamples to ≤500 points/series (design-backend §10;
the browser must not accumulate).

Pure logic, no IO. ``random.Random(seed)`` is deterministic given the seed.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_POINTS = 500

WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "8h": 8 * 3600,
    "24h": 24 * 3600,
}


@dataclass(frozen=True)
class MetricProfile:
    base: float
    amplitude: float
    period_s: float
    noise: float
    threshold: float
    spike_every: int  # every Nth point gets an upward spike (0 = none)
    spike_mult: float


METRIC_PROFILES: dict[str, MetricProfile] = {
    "dv": MetricProfile(0.13, 0.05, 1800, 0.01, 0.35, spike_every=97, spike_mult=2.2),
    "residual": MetricProfile(0.021, 0.006, 2400, 0.002, 0.035, spike_every=131, spike_mult=1.8),
    "latency": MetricProfile(0.31, 0.08, 900, 0.03, 1.0, spike_every=0, spike_mult=1.0),
}


def is_known_metric(metric: str) -> bool:
    return metric in METRIC_PROFILES


def _seed(metric: str, device: str, window: str) -> int:
    # Stable per (metric, device, window) across processes — builtin hash() is
    # PYTHONHASHSEED-salted, so use a fixed digest instead (deterministic tests).
    digest = hashlib.sha256(f"{metric}|{device}|{window}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _value_at(profile: MetricProfile, rng: random.Random, index: int, t_s: float) -> float:
    v = profile.base + profile.amplitude * math.sin(2 * math.pi * t_s / profile.period_s)
    v += rng.uniform(-profile.noise, profile.noise)
    if profile.spike_every and index % profile.spike_every == 0 and index > 0:
        v *= profile.spike_mult
    return round(max(0.0, v), 6)


def series(
    *,
    metric: str,
    device: str,
    window: str,
    end: datetime | None = None,
    max_points: int = MAX_POINTS,
) -> list[dict[str, float | str]]:
    """Downsampled series of ``{"t": iso, "value": float}`` spanning the window."""
    profile = METRIC_PROFILES[metric]
    window_s = WINDOW_SECONDS[window]
    end = end or datetime.now(UTC)
    n = min(max_points, window_s)  # ≤500 points, ≤1 point/sec
    step = window_s / n
    rng = random.Random(_seed(metric, device, window))
    points: list[dict[str, float | str]] = []
    for i in range(n):
        t_s = i * step
        ts = end - timedelta(seconds=window_s) + timedelta(seconds=t_s)
        points.append(
            {"t": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "value": _value_at(profile, rng, i, t_s)}
        )
    return points


def current_value(metric: str, device: str, *, now: datetime | None = None) -> float:
    """A single 'live' value for the metric (used by snapshot / l1 realtime)."""
    profile = METRIC_PROFILES[metric]
    now = now or datetime.now(UTC)
    rng = random.Random(_seed(metric, device, "live"))
    t_s = now.timestamp() % profile.period_s
    return _value_at(profile, rng, 0, t_s)


def delta_5min(metric: str, device: str, *, now: datetime | None = None) -> float:
    """Backend-computed change over the last 5 minutes (design-backend §2)."""
    profile = METRIC_PROFILES[metric]
    now = now or datetime.now(UTC)
    rng = random.Random(_seed(metric, device, "live"))
    now_v = _value_at(profile, rng, 0, now.timestamp() % profile.period_s)
    past = now - timedelta(minutes=5)
    past_v = _value_at(profile, rng, 0, past.timestamp() % profile.period_s)
    return round(now_v - past_v, 6)
