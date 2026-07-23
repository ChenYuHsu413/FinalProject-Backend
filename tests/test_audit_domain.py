"""Unit tests for the audit hash chain (pure logic, no DB)."""

from __future__ import annotations

from app.domain.audit import (
    GENESIS_HASH,
    canonical_json,
    compute_entry_hash,
    verify_chain,
)


def _entry(prev_hash: str, **over):
    base = {
        "event_id": "evt-1",
        "ts": "2026-07-23T00:00:00Z",
        "correlation_id": "cid-1",
        "command_id": None,
        "user_id": "user-1",
        "role": "operator",
        "source_ip": "10.0.0.1",
        "action": "cycle.start",
        "target_device": "AXIS-04",
        "scenario_id": "01_Pick_and_Place",
        "old_value": None,
        "new_value": {"state": "running"},
        "reason": "換線",
        "proposed_at": None,
        "approved_at": None,
        "executed_at": "2026-07-23T00:00:00Z",
        "result": "submitted",
        "model_version": None,
        "mode": None,
    }
    base.update(over)
    base["prev_hash"] = prev_hash
    base["entry_hash"] = compute_entry_hash(prev_hash, base)
    return base


def _chain(n: int) -> list[dict]:
    entries = []
    prev = GENESIS_HASH
    for i in range(n):
        e = _entry(prev, event_id=f"evt-{i}")
        entries.append(e)
        prev = e["entry_hash"]
    return entries


# --- canonical_json ----------------------------------------------------------
def test_canonical_json_is_sorted_and_compact():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert " " not in canonical_json({"a": [1, 2, 3]})


def test_canonical_json_keeps_non_ascii():
    # ensure_ascii off: Chinese hashes by its real bytes, not \uXXXX escapes.
    out = canonical_json({"reason": "換線前停機"})
    assert "換線前停機" in out


def test_canonical_json_key_order_independent():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


# --- compute_entry_hash ------------------------------------------------------
def test_entry_hash_ignores_db_generated_fields():
    prev = GENESIS_HASH
    e = _entry(prev)
    h1 = compute_entry_hash(prev, e)
    tampered = {**e, "id": 999, "created_at": "2099-01-01T00:00:00Z"}
    assert compute_entry_hash(prev, tampered) == h1


def test_entry_hash_changes_when_business_field_changes():
    prev = GENESIS_HASH
    e = _entry(prev)
    h1 = compute_entry_hash(prev, e)
    assert compute_entry_hash(prev, {**e, "reason": "different"}) != h1


def test_entry_hash_depends_on_prev_hash():
    e = _entry(GENESIS_HASH)
    assert compute_entry_hash(GENESIS_HASH, e) != compute_entry_hash("f" * 64, e)


# --- verify_chain: empty / single / multi ------------------------------------
def test_verify_empty_chain_is_vacuously_true():
    r = verify_chain([])
    assert r.verified is True
    assert r.entries == 0
    assert r.head_hash is None


def test_verify_single_entry():
    r = verify_chain(_chain(1))
    assert r.verified is True
    assert r.entries == 1
    assert r.head_hash is not None


def test_verify_multi_entry():
    r = verify_chain(_chain(5))
    assert r.verified is True
    assert r.entries == 5


def test_verify_detects_content_tamper():
    chain = _chain(3)
    chain[1]["reason"] = "tampered after the fact"  # entry_hash now stale
    r = verify_chain(chain)
    assert r.verified is False
    assert r.first_bad_position == 2
    assert r.reason == "entry_hash content mismatch"


def test_verify_detects_broken_link():
    chain = _chain(3)
    chain[2]["prev_hash"] = "0" * 64  # points at genesis instead of entry 2
    r = verify_chain(chain)
    assert r.verified is False
    assert r.first_bad_position == 3
    assert r.reason == "prev_hash link mismatch"


def test_verify_detects_deleted_middle_entry():
    chain = _chain(4)
    del chain[1]  # removing a row breaks the link at the new position 2
    r = verify_chain(chain)
    assert r.verified is False
    assert r.first_bad_position == 2
