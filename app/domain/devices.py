"""Device registry (batch 4).

The platform currently targets a single device, ``AXIS-04`` (design-backend §2
examples). Rather than let an arbitrary ``device`` query value flow through, the
snapshot/trends endpoints resolve it here; unknown → 404. The registry is a
static mapping for now; DECISIONS D4.3 records how it will be sourced later.
"""

from __future__ import annotations

from dataclasses import dataclass


class DeviceNotFound(Exception):
    """Unknown device id (→ HTTP 404)."""


@dataclass(frozen=True)
class Device:
    id: str
    cell: str
    line: str
    scenario_id: str
    scenario_name: str


_DEVICES: dict[str, Device] = {
    "AXIS-04": Device(
        id="AXIS-04",
        cell="Hsinchu-CellA",
        line="Line02",
        scenario_id="01_Pick_and_Place",
        scenario_name="Pick & Place",
    ),
}

DEFAULT_DEVICE = "AXIS-04"


def get_device(device_id: str) -> Device:
    device = _DEVICES.get(device_id)
    if device is None:
        raise DeviceNotFound(f"unknown device: {device_id!r}")
    return device
