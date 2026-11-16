"""Supplemental generator limits from the DGER data block."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Generator:
    """DGER record.  Scheduled generation itself is defined in DBAR."""

    bus: int
    minimum_active_power_mw: float
    maximum_active_power_mw: float
    available: bool = True

    @classmethod
    def from_record(cls, record: dict[str, str]) -> Generator:
        return cls(
            bus=int(float(record["No"])),
            minimum_active_power_mw=float(record.get("Pgn") or 0.0),
            maximum_active_power_mw=float(record.get("Pgm") or 0.0),
            available=record.get("O", "A").upper() == "A",
        )
