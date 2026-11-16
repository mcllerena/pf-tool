"""Complete parsed AC power-flow case."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csc_matrix

from pf_tool.models.branch import Branch
from pf_tool.models.bus import Bus, BusType
from pf_tool.models.hvdc_lcc import HVDCLink
from pf_tool.models.shunt import SwitchedShunt
from pf_tool.models.svc import SVC
from pf_tool.models.transformer import TapControl


@dataclass(slots=True)
class PowerFlowCase:
    base_mva: float
    buses: list[Bus]
    branches: list[Branch]
    svcs: list[SVC] = field(default_factory=list)
    switched_shunts: list[SwitchedShunt] = field(default_factory=list)
    hvdc_links: list[HVDCLink] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    base_ybus: csc_matrix | None = None
    solved_voltage: np.ndarray | None = None
    tap_controls: list[TapControl] = field(default_factory=list)

    @property
    def bus_index(self) -> dict[int, int]:
        return {bus.number: index for index, bus in enumerate(self.buses)}

    @property
    def pq_indices(self) -> np.ndarray:
        return np.array(
            [i for i, bus in enumerate(self.buses) if bus.bus_type == BusType.PQ],
            dtype=np.int64,
        )

    @property
    def pv_indices(self) -> np.ndarray:
        return np.array(
            [i for i, bus in enumerate(self.buses) if bus.bus_type == BusType.PV],
            dtype=np.int64,
        )

    @property
    def specified_power(self) -> np.ndarray:
        return np.array(
            [
                complex(
                    bus.p_generation_mw - bus.p_load_mw, bus.q_generation_mvar - bus.q_load_mvar
                )
                / self.base_mva
                for bus in self.buses
            ],
            dtype=np.complex128,
        )

    @property
    def initial_voltage(self) -> np.ndarray:
        return np.array(
            [bus.voltage_pu * np.exp(1j * bus.angle_radians) for bus in self.buses],
            dtype=np.complex128,
        )
