"""Static VAR compensators and their voltage-control state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from pf_tool.models.bus import BusType

if TYPE_CHECKING:
    from pf_tool.models.case import PowerFlowCase


@dataclass(slots=True)
class SVC:
    bus: int
    controlled_bus: int
    mode: str
    slope: float
    q_mvar: float
    q_min_mvar: float
    q_max_mvar: float
    reference_voltage: float
    device_voltage_scale: float = 1.0
    control_voltage_scale: float = 1.0


@dataclass(slots=True)
class SVCState:
    bus_index: int
    control_bus_index: int
    mode: str
    slope: float
    q_pu: float
    q_min_pu: float
    q_max_pu: float
    reference_voltage: float
    active: bool
    device_voltage_scale: float = 1.0
    control_voltage_scale: float = 1.0
    limit_state: int = 0
    svc_index: int = -1


def active_svc_states(case: PowerFlowCase) -> list[SVCState]:
    indices = case.bus_index
    states: list[SVCState] = []
    for svc_index, svc in enumerate(case.svcs):
        if svc.bus not in indices or svc.controlled_bus not in indices:
            continue
        bus_index = indices[svc.bus]
        states.append(
            SVCState(
                bus_index=bus_index,
                control_bus_index=indices[svc.controlled_bus],
                mode=svc.mode.upper(),
                slope=svc.slope,
                q_pu=svc.q_mvar / case.base_mva,
                q_min_pu=svc.q_min_mvar / case.base_mva,
                q_max_pu=svc.q_max_mvar / case.base_mva,
                reference_voltage=svc.reference_voltage,
                active=case.buses[bus_index].bus_type == BusType.PQ,
                device_voltage_scale=svc.device_voltage_scale,
                control_voltage_scale=svc.control_voltage_scale,
                svc_index=svc_index,
            )
        )
    return states


def store_svc_outputs(case: PowerFlowCase, states: list[SVCState]) -> None:
    """Write solved SVC outputs back to the case (and its bus generation)."""
    for state in states:
        if not state.active or state.svc_index < 0:
            continue
        svc = case.svcs[state.svc_index]
        solved_mvar = state.q_pu * case.base_mva
        case.buses[state.bus_index].q_generation_mvar += solved_mvar - svc.q_mvar
        svc.q_mvar = solved_mvar


def refresh_svc_limits(states: list[SVCState], magnitudes: np.ndarray) -> None:
    """Clamp each SVC to its susceptance limits, ``Q = B·Vterminal²``."""
    for state in states:
        if not state.active:
            state.limit_state = 0
            continue
        control_voltage = magnitudes[state.control_bus_index] * state.control_voltage_scale
        device_voltage = magnitudes[state.bus_index] * state.device_voltage_scale
        lower = state.q_min_pu * device_voltage**2
        upper = state.q_max_pu * device_voltage**2
        requested_q = state.q_pu
        if abs(state.slope) > 1e-12:
            voltage_error = state.reference_voltage - control_voltage
            if state.mode == "I":
                requested_q = voltage_error * device_voltage / state.slope
            else:
                requested_q = voltage_error / state.slope
        if requested_q < lower:
            state.q_pu = lower
            state.limit_state = -1
        elif requested_q > upper:
            state.q_pu = upper
            state.limit_state = 1
        else:
            state.limit_state = 0
