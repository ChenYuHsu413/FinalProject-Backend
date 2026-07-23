"""Snapshot + trends endpoint tests (no Postgres needed).

Both endpoints compute from the time-series generator + device registry, so no
engine files or DB are required for the success path.
"""

from __future__ import annotations

SERVICE_TOKEN = "test-service-token"
HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-User-ID": "op-1",
    "X-User-Role": "operator",
    "X-Correlation-ID": "cid-ui",
}


# --- snapshot (design-backend §2) -------------------------------------------
async def test_snapshot_shape_and_fields(client):
    resp = await client.get("/api/v1/ui/snapshot?device=AXIS-04", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["schema_version"] == "1.0"
    assert b["device"] == {"id": "AXIS-04", "cell": "Hsinchu-CellA", "line": "Line02"}
    # long-form scenario id (PROMPT §3 #5), NOT "S01"
    assert b["scenario"]["id"] == "01_Pick_and_Place"
    # backend-computed fields present
    assert "delta_5min" in b["dv"]
    assert "sigma3_margin_pct" in b["residual"]
    assert set(b["alarms"]) == {"active", "critical", "warning", "oldest_pending_s"}
    assert {"stages", "e2e_latency_ms", "sla_ms"} <= set(b["pipeline"])
    assert {"comm", "data_quality", "model", "fallback", "latency"} <= set(b["health_cards"])


async def test_snapshot_default_device(client):
    resp = await client.get("/api/v1/ui/snapshot", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["device"]["id"] == "AXIS-04"


async def test_snapshot_unknown_device_404(client):
    resp = await client.get("/api/v1/ui/snapshot?device=NOPE", headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# --- trends (design-backend §10) --------------------------------------------
async def test_trends_downsampled_series(client):
    resp = await client.get(
        "/api/v1/trends?metrics=dv,residual&window=24h&device=AXIS-04", headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["window"] == "24h"
    assert set(b["series"]) == {"dv", "residual"}
    for m in ("dv", "residual"):
        assert len(b["series"][m]["points"]) <= 500
        assert "threshold" in b["series"][m]


async def test_trends_all_windows(client):
    for window in ("1h", "8h", "24h"):
        resp = await client.get(f"/api/v1/trends?metrics=dv&window={window}", headers=HEADERS)
        assert resp.status_code == 200, (window, resp.text)


async def test_trends_bad_window_422(client):
    resp = await client.get("/api/v1/trends?metrics=dv&window=99h", headers=HEADERS)
    assert resp.status_code == 422


async def test_trends_unknown_metric_400(client):
    resp = await client.get("/api/v1/trends?metrics=dv,bogus", headers=HEADERS)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_trends_unknown_device_404(client):
    resp = await client.get("/api/v1/trends?metrics=dv&device=NOPE", headers=HEADERS)
    assert resp.status_code == 404
