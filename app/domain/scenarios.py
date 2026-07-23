"""Scenario identifiers — long form only (PROMPT §3 ruling #5).

The backend never uses the short form (`S01`); Flask's normalizer handles display
abbreviation. Validation here is also the path-traversal guard: an id must be a
known active scenario (or match the strict pattern) **before** it is ever used to
assemble a file path under ENGINE_DATA_DIR (batch-3 acceptance #3).
"""

from __future__ import annotations

import re

# The three active scenarios (後端資料規格書 §六 / §2.6).
ACTIVE_SCENARIOS: tuple[str, ...] = (
    "01_Pick_and_Place",
    "18_Ball_Screw",
    "34_Rotor_Demag",
)

# Strict format: two digits, underscore, alnum/underscore words. No separators
# that could escape a directory (no '/', '\\', '.', '..').
SCENARIO_PATTERN = re.compile(r"^\d{2}_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*$")

# Library capacity (後端資料規格書 §七 /scenario-library): 40 slots.
SCENARIO_LIBRARY_SIZE = 40


def is_active_scenario(scenario_id: str) -> bool:
    return scenario_id in ACTIVE_SCENARIOS


def is_wellformed_scenario(scenario_id: str) -> bool:
    """Format-only check (path-traversal safe). Does not imply the scenario exists."""
    return bool(SCENARIO_PATTERN.fullmatch(scenario_id))
