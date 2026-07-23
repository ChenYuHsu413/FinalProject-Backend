"""Unit tests for the deterministic moving time-series generator."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain import timeseries as ts

END = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def test_series_is_deterministic():
    a = ts.series(metric="dv", device="AXIS-04", window="24h", end=END)
    b = ts.series(metric="dv", device="AXIS-04", window="24h", end=END)
    assert a == b


def test_series_capped_at_500_points():
    for window in ("1h", "8h", "24h"):
        pts = ts.series(metric="dv", device="AXIS-04", window=window, end=END)
        assert len(pts) <= ts.MAX_POINTS


def test_series_actually_moves():
    vals = [p["value"] for p in ts.series(metric="dv", device="AXIS-04", window="24h", end=END)]
    assert min(vals) != max(vals)  # not a flat line
    assert max(vals) > 0.35  # occasional spike clears the dv threshold


def test_known_metrics():
    assert ts.is_known_metric("dv")
    assert ts.is_known_metric("residual")
    assert not ts.is_known_metric("nope")


def test_delta_5min_is_signed_and_deterministic():
    d1 = ts.delta_5min("dv", "AXIS-04", now=END)
    d2 = ts.delta_5min("dv", "AXIS-04", now=END)
    assert d1 == d2
    assert isinstance(d1, float)
