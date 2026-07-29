"""param_verify read endpoint tests (B3) — engine layer, no Postgres.

Covers: empty/missing dir → empty list; list with files (+ internal-file skip +
device filter + limit); single 200 (raw passthrough); 404 on missing; path-
traversal guard → 404; and the permission gate (no service token → 403). A 403
denial's audit write is best-effort and swallowed, so these need no DB.
"""

from __future__ import annotations

import json
import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

SERVICE_TOKEN = "test-service-token"
HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-User-ID": "eng-1",
    "X-User-Role": "engineer",  # engineer holds model.read → may read (B4/D1.8 unrelated)
    "X-Correlation-ID": "cid-pv",
}

_VERIFIED = {
    "device": "M07",
    "param": "Acc",
    "old": 600.0,
    "new": 540.0,
    "dv_estimate": 1600.0,
    "before": {
        "follow_err_mrad": 64.8,
        "settle_ms": 278.0,
        "iq_plateau_A": 7.33,
        "iq_peak_A": 16.2,
    },
    "after": {"follow_err_mrad": 61.1, "settle_ms": 288.0, "iq_plateau_A": 7.36, "iq_peak_A": 15.5},
    "outcome": "verified",
    "executed_on": "simulated_device",
    "peak_current_delta_pct": -4.3,
    "approval_id": "APR-aaa",
    "approved_by": "eng-1",
    "ts": "2026-07-29T02:35:11Z",
}
_STALE = {
    "device": "AXIS-04",
    "param": "Acc",
    "old": 540.0,
    "new": 540.0,
    "proposal_current": 600.0,
    "outcome": "skipped_stale",
    "executed_on": "simulated_device",
    "note": "提案 current 與設備現值不符,基於過期狀態,拒絕執行",
    "approval_id": "APR-bbb",
    "approved_by": "eng-1",
    "ts": "2026-07-29T02:35:11Z",
}


def _set_engine_dir(path: str | None) -> str | None:
    from app.core import settings as settings_mod

    old = os.environ.get("ENGINE_DATA_DIR")
    if path is None:
        os.environ.pop("ENGINE_DATA_DIR", None)
    else:
        os.environ["ENGINE_DATA_DIR"] = path
    settings_mod.get_settings.cache_clear()
    return old


def _write(pv_dir, report_id: str, report: dict) -> None:
    pv_dir.mkdir(parents=True, exist_ok=True)
    (pv_dir / f"{report_id}.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


@pytest_asyncio.fixture
async def pv_client(tmp_path):
    pv = tmp_path / "param_verify"
    _write(pv, "M07_APR-aaa", _VERIFIED)
    _write(pv, "AXIS-04_APR-bbb", _STALE)
    # executor-internal state file the listing must skip.
    (pv / "_executed.json").write_text("[]", encoding="utf-8")
    old = _set_engine_dir(str(tmp_path))
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    _set_engine_dir(old)


@pytest_asyncio.fixture
async def empty_pv_client(tmp_path):
    # Engine dir exists but has no param_verify/ subdir at all.
    old = _set_engine_dir(str(tmp_path / "empty"))
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    _set_engine_dir(old)


# --- (a) empty / missing dir → empty list, not an error ----------------------
async def test_list_missing_dir_returns_empty(empty_pv_client):
    r = await empty_pv_client.get("/api/v1/engine/param-verify", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["param_verify"] == []
    assert body["total"] == 0


# --- (b) list with files: both reports, internal file skipped ----------------
async def test_list_with_files(pv_client):
    r = await pv_client.get("/api/v1/engine/param-verify", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    ids = {i["report_id"] for i in body["param_verify"]}
    assert ids == {"M07_APR-aaa", "AXIS-04_APR-bbb"}  # _executed.json skipped

    v = next(i for i in body["param_verify"] if i["report_id"] == "M07_APR-aaa")
    assert v["device"] == "M07"
    assert v["outcome"] == "verified"
    assert v["peak_current_delta_pct"] == -4.3
    assert v["created_at"]  # ISO mtime present

    s = next(i for i in body["param_verify"] if i["report_id"] == "AXIS-04_APR-bbb")
    assert s["outcome"] == "skipped_stale"
    assert s["before"] is None and s["after"] is None  # optional fields tolerated


async def test_list_device_filter_and_limit(pv_client):
    r = await pv_client.get("/api/v1/engine/param-verify?device=M07", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["param_verify"][0]["device"] == "M07"

    # limit caps the returned list but total reflects the full filtered count.
    r2 = await pv_client.get("/api/v1/engine/param-verify?limit=1", headers=HEADERS)
    body2 = r2.json()
    assert len(body2["param_verify"]) == 1
    assert body2["total"] == 2
    assert body2["limit"] == 1


# --- (c) single 200 → raw report passthrough + metadata ----------------------
async def test_get_single_200_passthrough(pv_client):
    r = await pv_client.get("/api/v1/engine/param-verify/M07_APR-aaa", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_id"] == "M07_APR-aaa"
    assert body["device"] == "M07"
    assert body["approval_id"] == "APR-aaa"
    assert body["created_at"]
    # raw passthrough keeps every field, including nested before/after.
    assert body["report"]["executed_on"] == "simulated_device"
    assert body["report"]["before"]["iq_peak_A"] == 16.2


# --- (d) missing report → 404 ------------------------------------------------
async def test_get_missing_is_404(pv_client):
    r = await pv_client.get("/api/v1/engine/param-verify/M07_APR-nope", headers=HEADERS)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


# --- (e) path-traversal guard → 404 -----------------------------------------
async def test_get_path_traversal_rejected(pv_client):
    # A dot-containing id (the building block of '..') fails ^[A-Za-z0-9_\-]+$;
    # a '/' can never reach the single-segment path param.
    r = await pv_client.get("/api/v1/engine/param-verify/evil.report", headers=HEADERS)
    assert r.status_code == 404
    # The executor-internal _executed.json (exists on disk, is a JSON array) is not
    # a report: the leading-'_' guard hides it from the detail endpoint too → 404,
    # never a 500 from validating a list against the report model.
    r2 = await pv_client.get("/api/v1/engine/param-verify/_executed", headers=HEADERS)
    assert r2.status_code == 404


# --- (f) permission gate: no service token → 403 (audit write is swallowed) ---
async def test_no_token_is_403(pv_client):
    r = await pv_client.get("/api/v1/engine/param-verify")  # no auth headers
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
