"""AC bus data and bus-type definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BusType(StrEnum):
    PQ = "PQ"
    PV = "PV"
    SLACK = "SLACK"


@dataclass(slots=True)
class Bus:
    number: int
    name: str
    bus_type: BusType
    voltage_pu: float
    angle_radians: float
    p_generation_mw: float
    q_generation_mvar: float
    p_load_mw: float
    q_load_mvar: float
    shunt_susceptance_pu: float
    voltage_min_pu: float = 0.9
    voltage_max_pu: float = 1.1
    q_min_mvar: float | None = None
    q_max_mvar: float | None = None
    voltage_group: str = ""
    p_min_mw: float | None = None
    p_max_mw: float | None = None
    # ANAREDE type-3 buses: PQ buses whose voltage is held inside its limits
    # when DOPC VLIM is active.
    voltage_limit_control: bool = False
    area: int = 0
    base_kv: float = 0.0
    dbar_type: int = 0
