"""Unit tests for scenario validation (path-safety + active set)."""

from __future__ import annotations

from app.domain.scenarios import (
    ACTIVE_SCENARIOS,
    is_active_scenario,
    is_wellformed_scenario,
)


def test_active_set():
    assert ACTIVE_SCENARIOS == ("01_Pick_and_Place", "18_Ball_Screw", "34_Rotor_Demag")
    assert is_active_scenario("18_Ball_Screw")
    assert not is_active_scenario("05_Collision_Detect")


def test_wellformed_accepts_valid():
    assert is_wellformed_scenario("01_Pick_and_Place")
    assert is_wellformed_scenario("34_Rotor_Demag")


def test_wellformed_rejects_path_traversal_and_junk():
    bad_ids = [
        "../etc/passwd",
        "01/../02",
        "01_Pick/..",
        "..",
        ".",
        "",
        "abc",
        "1_x",
        "01_Pick.and",
    ]
    for bad in bad_ids:
        assert not is_wellformed_scenario(bad), bad
