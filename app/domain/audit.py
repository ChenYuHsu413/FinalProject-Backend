"""Audit hash-chain — pure logic, no IO (design-backend.md §5.1).

The chain formula is fixed here and nowhere else::

    entry_hash = SHA256( prev_hash + canonical_json(business_view(entry)) )

* ``canonical_json`` is pinned: sorted keys, no whitespace, UTF-8, ``ensure_ascii``
  off so non-ASCII (Chinese device/scenario names) hash by their real bytes.
  If this ever changes, every stored hash breaks — so it lives in one tested
  function (batch-2 acceptance).
* Only **business** fields enter the hash. DB-generated columns (``id`` autopk,
  ``created_at`` server default) and the output ``entry_hash`` are excluded — the
  hash must be computable *before* the row is inserted (DECISIONS D2.2).
* The first entry chains from ``GENESIS_HASH`` (DECISIONS D2.1).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# Genesis: the prev_hash of the very first entry. 64 zeros mirrors the fallback
# hash-chain convention in 後端資料規格書 §五 ("prev_hash": "0000...").
GENESIS_HASH = "0" * 64

# Business fields that enter the hash, in no particular order (canonical_json
# sorts keys anyway). Deliberately excludes: id, created_at (DB-generated),
# entry_hash (the output). prev_hash is NOT here — it is mixed in by
# concatenation per the formula, not inside the JSON body.
HASHED_FIELDS: tuple[str, ...] = (
    "event_id",
    "ts",
    "correlation_id",
    "command_id",
    "user_id",
    "role",
    "source_ip",
    "action",
    "target_device",
    "scenario_id",
    "old_value",
    "new_value",
    "reason",
    "proposed_at",
    "approved_at",
    "executed_at",
    "result",
    "model_version",
    "mode",
)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def business_view(entry: dict[str, Any]) -> dict[str, Any]:
    """Project an entry down to exactly the hashed business fields."""
    return {field: entry.get(field) for field in HASHED_FIELDS}


def compute_entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    """entry_hash = SHA256(prev_hash + canonical_json(business_view(entry)))."""
    payload = prev_hash + canonical_json(business_view(entry))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerification:
    verified: bool
    entries: int
    # 1-based position of the first bad entry (None when verified / empty).
    first_bad_position: int | None = None
    # The entry_hash of the last entry, for quick head comparison (None if empty).
    head_hash: str | None = None
    reason: str | None = None


def verify_chain(entries: list[dict[str, Any]]) -> ChainVerification:
    """Recompute the chain over `entries` (ordered oldest→newest).

    Each entry must carry stored ``prev_hash`` and ``entry_hash``. Both the link
    (stored prev_hash == expected) and the content (recomputed == stored) are
    checked. An empty chain is vacuously verified.
    """
    if not entries:
        return ChainVerification(verified=True, entries=0)

    expected_prev = GENESIS_HASH
    for idx, entry in enumerate(entries, start=1):
        stored_prev = entry.get("prev_hash")
        stored_hash = entry.get("entry_hash")

        if stored_prev != expected_prev:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                first_bad_position=idx,
                reason="prev_hash link mismatch",
            )

        recomputed = compute_entry_hash(expected_prev, entry)
        if recomputed != stored_hash:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                first_bad_position=idx,
                reason="entry_hash content mismatch",
            )

        expected_prev = stored_hash

    return ChainVerification(
        verified=True,
        entries=len(entries),
        head_hash=expected_prev,
    )
