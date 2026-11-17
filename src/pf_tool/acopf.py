"""AC optimal power flow with a voltage-profile objective and Ipopt."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pyomo.environ as pyo
from pyomo.opt import TerminationCondition
from scipy.sparse import csc_matrix
from scipy.sparse.csgraph import connected_components

from pf_tool.models.bus import Bus, BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.network_reduction import (
    ZERO_IMPEDANCE_THRESHOLD_PU,
    ReducedCase,
    reduce_low_impedance_branches,
)
from pf_tool.models.transformer import branch_stamps, reconcile_tap_controls
from pf_tool.newton_raphson import BusSolution, NewtonRaphsonPowerFlow, PowerFlowResult
from pf_tool.optimization import _assign_island_references, solve_optimization_model
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.network import build_ybus, calculate_power, freeze_branch_matrix
from pf_tool.utils.utils import build_power_flow_case

_ACCEPTED_TERMINATIONS = {
    TerminationCondition.optimal,
    TerminationCondition.locallyOptimal,
    TerminationCondition.feasible,
}
_UNLIMITED_RATING_MVA = 9999.0


@dataclass(slots=True)
class ACOptimalPowerFlowResult(PowerFlowResult):
    """Power-flow result with the ACOPF objective, duals, and solver status."""

    objective_value: float = math.nan
    duals: dict[str, Any] = field(default_factory=dict)
    termination_condition: str = "unknown"
    warm_start_iterations: int = 0
    feasibility_restoration_iterations: int = 0
    progressive_tightening_stages: int = 0


@dataclass(slots=True, frozen=True)
class ACOPFViolation:
    """One observed ACOPF constraint or operating-limit violation."""

    category: str
    element: str
    value: float
    limit: float
    amount: float
    unit: str


@dataclass(slots=True)
class _GeneratorGroup:
    reduced_index: int
    members: list[int]
    scheduled_mw: float
    lower_mw: float | None
    upper_mw: float | None


def _generator_group(
    reduced_index: int,
    members: list[int],
    scheduled_mw: float,
    bounds: list[tuple[float | None, float | None]],
) -> _GeneratorGroup:
    lower = (
        sum(pair[0] for pair in bounds)
        if bounds and all(pair[0] is not None for pair in bounds)
        else None
    )
    upper = (
        sum(pair[1] for pair in bounds)
        if bounds and all(pair[1] is not None for pair in bounds)
        else None
    )
    return _GeneratorGroup(reduced_index, members, scheduled_mw, lower, upper)


def _valid_range(lower: float | None, upper: float | None) -> bool:
    return lower is not None and upper is not None and upper > lower


def _generator_bounds(
    bus: Bus, lower_name: str, upper_name: str
) -> tuple[float | None, float | None]:
    lower = getattr(bus, lower_name)
    upper = getattr(bus, upper_name)
    if lower is None or upper is None or upper <= lower:
        return None, None
    return float(lower), float(upper)


def _bounded_initial(value: float, bounds: tuple[float | None, float | None]) -> float:
    lower, upper = bounds
    if lower is not None:
        value = max(value, lower)
    if upper is not None:
        value = min(value, upper)
    return value


def _build_acopf_model(
    operating_case: PowerFlowCase,
    reference_case: PowerFlowCase,
    reduction: ReducedCase,
    initial_voltage: np.ndarray,
    *,
    objective: Literal["voltage_deviation", "loss_minimization"],
    voltage_violation_penalty: float,
    branch_violation_penalty: float,
    limit_relaxation: float,
    feasibility_restoration: bool,
    voltage_reference: Mapping[int, float] | None,
    voltage_weights: Mapping[int, float] | None,
) -> pyo.ConcreteModel:
    case = reduction.case
    original_count = len(operating_case.buses)
    bus_count = len(case.buses)
    bus_ids = list(range(bus_count))
    original_ids = list(range(original_count))
    if initial_voltage.shape != (bus_count,) or not np.isfinite(initial_voltage).all():
        raise ValueError("initial_voltage must contain one finite value per reduced bus")

    original_indices = operating_case.bus_index
    stored_voltage = reference_case.initial_voltage
    specified_power = operating_case.specified_power
    reference_values: dict[int, float] = {}
    weight_values: dict[int, float] = {}
    for index, bus in enumerate(reference_case.buses):
        value = (
            float(voltage_reference.get(bus.number, abs(stored_voltage[index])))
            if voltage_reference is not None
            else float(abs(stored_voltage[index]))
        )
        weight = float(voltage_weights.get(bus.number, 1.0)) if voltage_weights is not None else 1.0
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"Voltage reference for bus {bus.number} must be finite and positive")
        if not math.isfinite(weight) or weight < 0.0:
            raise ValueError(f"Voltage weight for bus {bus.number} must be finite and nonnegative")
        reference_values[index] = value
        weight_values[index] = weight
    if voltage_reference is not None:
        unknown = set(voltage_reference).difference(original_indices)
        if unknown:
            raise ValueError(f"Unknown bus numbers in voltage_reference: {sorted(unknown)}")
    if voltage_weights is not None:
        unknown = set(voltage_weights).difference(original_indices)
        if unknown:
            raise ValueError(f"Unknown bus numbers in voltage_weights: {sorted(unknown)}")
    if objective == "voltage_deviation" and not any(weight_values.values()):
        raise ValueError("At least one voltage weight must be greater than zero")

    svc_by_bus: dict[int, float] = {}
    for svc in operating_case.svcs:
        svc_by_bus[svc.bus] = svc_by_bus.get(svc.bus, 0.0) + svc.q_mvar

    p_groups: dict[int, _GeneratorGroup] = {}
    q_groups: dict[int, _GeneratorGroup] = {}
    p_fixed = np.zeros(bus_count, dtype=float)
    q_fixed = np.zeros(bus_count, dtype=float)
    for reduced_index, members in enumerate(reduction.members):
        scheduled_p = 0.0
        p_members = [
            member for member in members if reference_case.buses[member].bus_type == BusType.SLACK
        ]
        if case.buses[reduced_index].bus_type == BusType.SLACK and not p_members:
            p_members = [reduction.representatives[reduced_index]]
        if p_members:
            scheduled_p = sum(operating_case.buses[index].p_generation_mw for index in p_members)
            p_bounds = [
                _generator_bounds(operating_case.buses[index], "p_min_mw", "p_max_mw")
                for index in p_members
            ]
            p_groups[reduced_index] = _generator_group(
                reduced_index, p_members, scheduled_p, p_bounds
            )
        p_fixed[reduced_index] = (
            sum(specified_power[index].real for index in members)
            - scheduled_p / operating_case.base_mva
        )

        q_members = [
            member
            for member in members
            if reference_case.buses[member].bus_type in (BusType.PV, BusType.SLACK)
            or _valid_range(
                operating_case.buses[member].q_min_mvar,
                operating_case.buses[member].q_max_mvar,
            )
        ]
        if case.buses[reduced_index].bus_type in (BusType.PV, BusType.SLACK) and not q_members:
            q_members = [reduction.representatives[reduced_index]]
        scheduled_q = sum(
            operating_case.buses[index].q_generation_mvar
            - svc_by_bus.get(operating_case.buses[index].number, 0.0)
            for index in q_members
        )
        if q_members:
            q_bounds = [
                _generator_bounds(operating_case.buses[index], "q_min_mvar", "q_max_mvar")
                for index in q_members
            ]
            q_groups[reduced_index] = _generator_group(
                reduced_index, q_members, scheduled_q, q_bounds
            )
        scheduled_svc = sum(
            svc_by_bus.get(operating_case.buses[index].number, 0.0) for index in members
        )
        q_fixed[reduced_index] = (
            sum(specified_power[index].imag for index in members)
            - scheduled_q / operating_case.base_mva
            - scheduled_svc / operating_case.base_mva
        )

    ybus = build_ybus(case).tocsr()
    neighbors = {
        index: [
            (int(other), float(value.real), float(value.imag))
            for other, value in zip(
                ybus.indices[ybus.indptr[index] : ybus.indptr[index + 1]],
                ybus.data[ybus.indptr[index] : ybus.indptr[index + 1]],
            )
        ]
        for index in bus_ids
    }
    initial_power = calculate_power(ybus.tocsc(), initial_voltage)
    svc_initial_by_bus: dict[int, float] = {}
    reduced_bus_index = case.bus_index
    for svc in case.svcs:
        bus_index = reduced_bus_index[svc.bus]
        svc_initial_by_bus[bus_index] = (
            svc_initial_by_bus.get(bus_index, 0.0) + svc.q_mvar / operating_case.base_mva
        )

    model = pyo.ConcreteModel(name="ac_optimal_power_flow")
    model.BUS = pyo.Set(initialize=bus_ids, ordered=True)
    model.ORIGINAL_BUS = pyo.Set(initialize=original_ids, ordered=True)
    model.PGEN = pyo.Set(initialize=list(p_groups), ordered=True)
    model.QGEN = pyo.Set(initialize=list(q_groups), ordered=True)

    model.vm = pyo.Var(
        model.BUS,
        initialize={index: float(abs(initial_voltage[index])) for index in bus_ids},
        bounds=(0.2, 2.0),
        within=pyo.PositiveReals,
    )
    model.va = pyo.Var(
        model.BUS,
        initialize={index: float(np.angle(initial_voltage[index])) for index in bus_ids},
        bounds=(-math.pi, math.pi),
        within=pyo.Reals,
    )
    graph = ybus.copy()
    graph.data = np.ones(graph.nnz, dtype=np.int8)
    island_count, island_labels = connected_components(graph, directed=False)
    reference_angle_buses: set[int] = set()
    for island in range(island_count):
        indices = np.flatnonzero(island_labels == island)
        slack_buses = [index for index in indices if case.buses[index].bus_type == BusType.SLACK]
        if slack_buses:
            reference_angle_buses.add(slack_buses[0])
        elif len(indices):
            reference_angle_buses.add(int(indices[0]))
    if not reference_angle_buses:
        raise ValueError("At least one slack/reference bus is required")
    for index in reference_angle_buses:
        model.va[index].fix(float(np.angle(initial_voltage[index])))

    model.pgen = pyo.Var(
        model.PGEN,
        initialize={
            index: _bounded_initial(
                float(initial_power[index].real - p_fixed[index]),
                (
                    None
                    if p_groups[index].lower_mw is None
                    else p_groups[index].lower_mw / operating_case.base_mva,
                    None
                    if p_groups[index].upper_mw is None
                    else p_groups[index].upper_mw / operating_case.base_mva,
                ),
            )
            for index in p_groups
        },
        bounds=lambda _m, index: (
            None
            if p_groups[index].lower_mw is None
            else p_groups[index].lower_mw / operating_case.base_mva,
            None
            if p_groups[index].upper_mw is None
            else p_groups[index].upper_mw / operating_case.base_mva,
        ),
        within=pyo.Reals,
    )
    model.qgen = pyo.Var(
        model.QGEN,
        initialize={
            index: _bounded_initial(
                float(
                    initial_power[index].imag - q_fixed[index] - svc_initial_by_bus.get(index, 0.0)
                ),
                (
                    None
                    if q_groups[index].lower_mw is None
                    else q_groups[index].lower_mw / operating_case.base_mva,
                    None
                    if q_groups[index].upper_mw is None
                    else q_groups[index].upper_mw / operating_case.base_mva,
                ),
            )
            for index in q_groups
        },
        bounds=lambda _m, index: (
            None
            if q_groups[index].lower_mw is None
            else q_groups[index].lower_mw / operating_case.base_mva,
            None
            if q_groups[index].upper_mw is None
            else q_groups[index].upper_mw / operating_case.base_mva,
        ),
        within=pyo.Reals,
    )
    svc_data: dict[int, dict[str, float | int]] = {}
    for svc_index, svc in enumerate(case.svcs):
        if svc.bus not in reduced_bus_index:
            continue
        if svc.q_min_mvar > svc.q_max_mvar:
            raise ValueError(f"Invalid reactive limits for SVC at bus {svc.bus}")
        svc_data[svc_index] = {
            "bus_index": reduced_bus_index[svc.bus],
            "bus_number": svc.bus,
            "scale": svc.device_voltage_scale,
            "q_min": svc.q_min_mvar / operating_case.base_mva,
            "q_max": svc.q_max_mvar / operating_case.base_mva,
            "initial": svc.q_mvar / operating_case.base_mva,
        }
    model.SVC = pyo.Set(initialize=list(svc_data), ordered=True)
    model.qsvc = pyo.Var(
        model.SVC,
        initialize={index: float(data["initial"]) for index, data in svc_data.items()},
        within=pyo.Reals,
    )

    def p_calculated(m: Any, index: int) -> Any:
        return sum(
            m.vm[index]
            * m.vm[other]
            * (
                conductance * pyo.cos(m.va[index] - m.va[other])
                + susceptance * pyo.sin(m.va[index] - m.va[other])
            )
            for other, conductance, susceptance in neighbors[index]
        )

    def q_calculated(m: Any, index: int) -> Any:
        return sum(
            m.vm[index]
            * m.vm[other]
            * (
                conductance * pyo.sin(m.va[index] - m.va[other])
                - susceptance * pyo.cos(m.va[index] - m.va[other])
            )
            for other, conductance, susceptance in neighbors[index]
        )

    pgen_by_bus = {index: [index] for index in p_groups}
    qgen_by_bus = {index: [index] for index in q_groups}
    svc_by_reduced_bus: dict[int, list[int]] = {}
    for svc_index, data in svc_data.items():
        svc_by_reduced_bus.setdefault(int(data["bus_index"]), []).append(svc_index)
    model.p_calculated = pyo.Expression(model.BUS, rule=p_calculated)
    model.q_calculated = pyo.Expression(model.BUS, rule=q_calculated)
    model.p_balance = pyo.Constraint(
        model.BUS,
        rule=lambda m, index: (
            m.p_calculated[index]
            == p_fixed[index] + sum(m.pgen[k] for k in pgen_by_bus.get(index, []))
        ),
    )
    model.q_balance = pyo.Constraint(
        model.BUS,
        rule=lambda m, index: (
            m.q_calculated[index]
            == q_fixed[index]
            + sum(m.qgen[k] for k in qgen_by_bus.get(index, []))
            + sum(m.qsvc[k] for k in svc_by_reduced_bus.get(index, []))
        ),
    )
    model.voltage_lower = pyo.Constraint(
        model.ORIGINAL_BUS,
        rule=lambda m, index: (
            abs(reduction.voltage_scales[index]) * m.vm[int(reduction.original_to_reduced[index])]
            >= reference_case.buses[index].voltage_min_pu
        ),
    )
    model.voltage_upper = pyo.Constraint(
        model.ORIGINAL_BUS,
        rule=lambda m, index: (
            abs(reduction.voltage_scales[index]) * m.vm[int(reduction.original_to_reduced[index])]
            <= reference_case.buses[index].voltage_max_pu
        ),
    )
    model.svc_lower = pyo.Constraint(
        model.SVC,
        rule=lambda m, index: (
            m.qsvc[index]
            >= float(svc_data[index]["q_min"])
            * (m.vm[int(svc_data[index]["bus_index"])] * float(svc_data[index]["scale"])) ** 2
        ),
    )
    model.svc_upper = pyo.Constraint(
        model.SVC,
        rule=lambda m, index: (
            m.qsvc[index]
            <= float(svc_data[index]["q_max"])
            * (m.vm[int(svc_data[index]["bus_index"])] * float(svc_data[index]["scale"])) ** 2
        ),
    )

    soft_limit_penalty = _add_soft_operating_limits(
        model,
        operating_case,
        reference_case,
        reduction,
        initial_voltage,
        voltage_violation_penalty,
        branch_violation_penalty,
        limit_relaxation,
    )
    if feasibility_restoration:
        objective_expression = 0.0
    elif objective == "voltage_deviation":
        objective_expression = sum(
            weight_values[index]
            * (
                abs(reduction.voltage_scales[index])
                * model.vm[int(reduction.original_to_reduced[index])]
                - reference_values[index]
            )
            ** 2
            for index in original_ids
        )
    elif objective == "loss_minimization":
        objective_expression = sum(model.p_calculated[index] for index in model.BUS)
    else:
        raise ValueError(f"Unsupported ACOPF objective: {objective}")
    model.objective = pyo.Objective(
        expr=objective_expression + soft_limit_penalty,
        sense=pyo.minimize,
    )
    model.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    model.ipopt_zL_out = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    model.ipopt_zU_out = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    model._case = case
    model._operating_case = operating_case
    model._reference_case = reference_case
    model._reduction = reduction
    model._ybus = ybus.tocsc()
    model._p_fixed = p_fixed
    model._q_fixed = q_fixed
    model._p_groups = p_groups
    model._q_groups = q_groups
    model._svc_data = svc_data
    model._objective_type = objective
    model._feasibility_restoration = feasibility_restoration
    model._limit_relaxation = limit_relaxation
    model._base_mva = operating_case.base_mva
    return model


def _add_soft_operating_limits(
    model: pyo.ConcreteModel,
    operating_case: PowerFlowCase,
    reference_case: PowerFlowCase,
    reduction: ReducedCase,
    initial_voltage: np.ndarray,
    voltage_penalty: float,
    branch_penalty: float,
    limit_relaxation: float,
) -> Any:
    model.voltage_lower.deactivate()
    model.voltage_upper.deactivate()
    model.voltage_lower_slack = pyo.Var(
        model.ORIGINAL_BUS,
        within=pyo.NonNegativeReals,
        initialize={
            index: max(
                reference_case.buses[index].voltage_min_pu
                - abs(reduction.voltage_scales[index])
                * abs(initial_voltage[int(reduction.original_to_reduced[index])]),
                0.0,
            )
            for index in model.ORIGINAL_BUS
        },
    )
    model.voltage_upper_slack = pyo.Var(
        model.ORIGINAL_BUS,
        within=pyo.NonNegativeReals,
        initialize={
            index: max(
                abs(reduction.voltage_scales[index])
                * abs(initial_voltage[int(reduction.original_to_reduced[index])])
                - reference_case.buses[index].voltage_max_pu,
                0.0,
            )
            for index in model.ORIGINAL_BUS
        },
    )
    model.voltage_lower_soft = pyo.Constraint(
        model.ORIGINAL_BUS,
        rule=lambda m, index: (
            abs(reduction.voltage_scales[index]) * m.vm[int(reduction.original_to_reduced[index])]
            >= (
                reference_case.buses[index].voltage_min_pu
                - limit_relaxation
                - m.voltage_lower_slack[index]
            )
        ),
    )
    model.voltage_upper_soft = pyo.Constraint(
        model.ORIGINAL_BUS,
        rule=lambda m, index: (
            abs(reduction.voltage_scales[index]) * m.vm[int(reduction.original_to_reduced[index])]
            <= (
                reference_case.buses[index].voltage_max_pu
                + limit_relaxation
                + m.voltage_upper_slack[index]
            )
        ),
    )

    original_bus_index = operating_case.bus_index
    branch_data: dict[int, dict[str, Any]] = {}
    branch_slack_initial: dict[int, float] = {}
    for position, branch in enumerate(operating_case.branches):
        if not 0.0 < branch.rating_mva < _UNLIMITED_RATING_MVA:
            continue
        impedance = complex(branch.resistance_pu, branch.reactance_pu)
        if abs(impedance) <= ZERO_IMPEDANCE_THRESHOLD_PU:
            continue
        from_original = original_bus_index[branch.from_bus]
        to_original = original_bus_index[branch.to_bus]
        from_reduced = int(reduction.original_to_reduced[from_original])
        to_reduced = int(reduction.original_to_reduced[to_original])
        from_scale = complex(reduction.voltage_scales[from_original])
        to_scale = complex(reduction.voltage_scales[to_original])
        yff, yft, ytf, ytt = branch_stamps(
            1.0 / impedance,
            branch.charging_pu,
            branch.tap_ratio,
            branch.phase_shift_radians,
        )
        data = {
            "from_bus": from_reduced,
            "to_bus": to_reduced,
            "from_scale": abs(from_scale),
            "to_scale": abs(to_scale),
            "phase_delta": float(np.angle(from_scale) - np.angle(to_scale)),
            "yff_g": float(yff.real),
            "yff_b": float(yff.imag),
            "yft_g": float(yft.real),
            "yft_b": float(yft.imag),
            "ytf_g": float(ytf.real),
            "ytf_b": float(ytf.imag),
            "ytt_g": float(ytt.real),
            "ytt_b": float(ytt.imag),
            "rating_pu": branch.rating_mva / operating_case.base_mva * (1.0 + limit_relaxation),
            "label": f"{branch.from_bus}-{branch.to_bus}:{branch.circuit} (branch {position + 1})",
        }
        branch_data[position] = data
        from_voltage = from_scale * initial_voltage[from_reduced]
        to_voltage = to_scale * initial_voltage[to_reduced]
        from_power = from_voltage * np.conj(yff * from_voltage + yft * to_voltage)
        to_power = to_voltage * np.conj(ytf * from_voltage + ytt * to_voltage)
        branch_slack_initial[position] = max(
            max(abs(from_power), abs(to_power)) - float(data["rating_pu"]),
            0.0,
        )

    model.BRANCH = pyo.Set(initialize=list(branch_data), ordered=True)
    model.branch_slack = pyo.Var(
        model.BRANCH,
        within=pyo.NonNegativeReals,
        initialize=branch_slack_initial,
    )
    model._branch_data = branch_data
    model._branch_labels = {index: str(data["label"]) for index, data in branch_data.items()}

    def terminal_powers(m: Any, index: int) -> tuple[Any, Any, Any, Any]:
        data = branch_data[index]
        from_voltage = m.vm[int(data["from_bus"])] * float(data["from_scale"])
        to_voltage = m.vm[int(data["to_bus"])] * float(data["to_scale"])
        delta = m.va[int(data["from_bus"])] - m.va[int(data["to_bus"])] + float(data["phase_delta"])
        cross = from_voltage * to_voltage
        p_from = from_voltage**2 * float(data["yff_g"]) + cross * (
            float(data["yft_g"]) * pyo.cos(delta) + float(data["yft_b"]) * pyo.sin(delta)
        )
        q_from = -(from_voltage**2) * float(data["yff_b"]) + cross * (
            float(data["yft_g"]) * pyo.sin(delta) - float(data["yft_b"]) * pyo.cos(delta)
        )
        p_to = to_voltage**2 * float(data["ytt_g"]) + cross * (
            float(data["ytf_g"]) * pyo.cos(delta) - float(data["ytf_b"]) * pyo.sin(delta)
        )
        q_to = -(to_voltage**2) * float(data["ytt_b"]) - cross * (
            float(data["ytf_g"]) * pyo.sin(delta) + float(data["ytf_b"]) * pyo.cos(delta)
        )
        return p_from, q_from, p_to, q_to

    model.branch_p_from = pyo.Expression(
        model.BRANCH, rule=lambda m, index: terminal_powers(m, index)[0]
    )
    model.branch_q_from = pyo.Expression(
        model.BRANCH, rule=lambda m, index: terminal_powers(m, index)[1]
    )
    model.branch_p_to = pyo.Expression(
        model.BRANCH, rule=lambda m, index: terminal_powers(m, index)[2]
    )
    model.branch_q_to = pyo.Expression(
        model.BRANCH, rule=lambda m, index: terminal_powers(m, index)[3]
    )
    model.branch_flow_from_limit = pyo.Constraint(
        model.BRANCH,
        rule=lambda m, index: (
            m.branch_p_from[index] ** 2 + m.branch_q_from[index] ** 2
            <= (float(branch_data[index]["rating_pu"]) + m.branch_slack[index]) ** 2
        ),
    )
    model.branch_flow_to_limit = pyo.Constraint(
        model.BRANCH,
        rule=lambda m, index: (
            m.branch_p_to[index] ** 2 + m.branch_q_to[index] ** 2
            <= (float(branch_data[index]["rating_pu"]) + m.branch_slack[index]) ** 2
        ),
    )
    return (
        voltage_penalty * sum(model.voltage_lower_slack[index] for index in model.ORIGINAL_BUS)
        + (voltage_penalty * sum(model.voltage_upper_slack[index] for index in model.ORIGINAL_BUS))
        + branch_penalty * sum(model.branch_slack[index] for index in model.BRANCH)
    )


def _model_voltage(model: pyo.ConcreteModel) -> np.ndarray:
    return np.array(
        [
            float(pyo.value(model.vm[index])) * np.exp(1j * float(pyo.value(model.va[index])))
            for index in model.BUS
        ],
        dtype=np.complex128,
    )


def _copy_model_variable_values(source: pyo.ConcreteModel, destination: pyo.ConcreteModel) -> None:
    for name in (
        "vm",
        "va",
        "pgen",
        "qgen",
        "qsvc",
        "voltage_lower_slack",
        "voltage_upper_slack",
        "branch_slack",
    ):
        source_variable = getattr(source, name)
        destination_variable = getattr(destination, name)
        for index in destination_variable:
            value = pyo.value(source_variable[index], exception=False)
            if value is not None:
                destination_variable[index].set_value(value)


def _model_mismatch(model: pyo.ConcreteModel) -> tuple[float, float]:
    voltage = _model_voltage(model)
    calculated = calculate_power(model._ybus, voltage)
    p_specified = model._p_fixed.copy()
    q_specified = model._q_fixed.copy()
    for index in model._p_groups:
        p_specified[index] += float(pyo.value(model.pgen[index]))
    for index in model._q_groups:
        q_specified[index] += float(pyo.value(model.qgen[index]))
    for index, data in model._svc_data.items():
        q_specified[int(data["bus_index"])] += float(pyo.value(model.qsvc[index]))
    return (
        float(np.max(np.abs(calculated.real - p_specified))),
        float(np.max(np.abs(calculated.imag - q_specified))),
    )


def _duals(model: pyo.ConcreteModel, reduction: ReducedCase) -> dict[str, Any]:
    def constraint_values(component: Any, labels: Mapping[int, str]) -> dict[str, float]:
        values = {}
        for index in component:
            dual = model.dual.get(component[index])
            if dual is not None:
                values[labels[index]] = float(dual)
        return values

    def bound_values(
        variable: Any, suffix_name: str, labels: Mapping[int, str]
    ) -> dict[str, float]:
        suffix = getattr(model, suffix_name)
        values = {}
        for index in variable:
            dual = suffix.get(variable[index])
            if dual is not None:
                values[labels[index]] = float(dual)
        return values

    bus_labels = {index: str(model._case.buses[index].number) for index in model.BUS}
    original_labels = {
        index: str(model._reference_case.buses[index].number) for index in model.ORIGINAL_BUS
    }
    svc_labels = {}
    for index in model.SVC:
        source_index = reduction.svc_sources[index] if reduction.svc_sources else index
        svc_labels[index] = f"{int(model._svc_data[index]['bus_number'])}:svc{source_index + 1}"
    pg_labels = {index: str(model._case.buses[index].number) for index in model.PGEN}
    qg_labels = {index: str(model._case.buses[index].number) for index in model.QGEN}
    vm_labels = {index: str(model._case.buses[index].number) for index in model.BUS}
    return {
        "active_power_balance": constraint_values(model.p_balance, bus_labels),
        "reactive_power_balance": constraint_values(model.q_balance, bus_labels),
        "voltage_lower": constraint_values(model.voltage_lower_soft, original_labels),
        "voltage_upper": constraint_values(model.voltage_upper_soft, original_labels),
        "svc_lower": constraint_values(model.svc_lower, svc_labels),
        "svc_upper": constraint_values(model.svc_upper, svc_labels),
        "branch_flow_from": constraint_values(model.branch_flow_from_limit, model._branch_labels),
        "branch_flow_to": constraint_values(model.branch_flow_to_limit, model._branch_labels),
        "variable_bounds": {
            "lower": {
                "voltage_magnitude": bound_values(model.vm, "ipopt_zL_out", vm_labels),
                "slack_active_generation": bound_values(model.pgen, "ipopt_zL_out", pg_labels),
                "generator_reactive_generation": bound_values(
                    model.qgen, "ipopt_zL_out", qg_labels
                ),
                "voltage_lower_slack": bound_values(
                    model.voltage_lower_slack, "ipopt_zL_out", original_labels
                ),
                "voltage_upper_slack": bound_values(
                    model.voltage_upper_slack, "ipopt_zL_out", original_labels
                ),
                "branch_slack": bound_values(
                    model.branch_slack, "ipopt_zL_out", model._branch_labels
                ),
            },
            "upper": {
                "voltage_magnitude": bound_values(model.vm, "ipopt_zU_out", vm_labels),
                "slack_active_generation": bound_values(model.pgen, "ipopt_zU_out", pg_labels),
                "generator_reactive_generation": bound_values(
                    model.qgen, "ipopt_zU_out", qg_labels
                ),
                "voltage_lower_slack": bound_values(
                    model.voltage_lower_slack, "ipopt_zU_out", original_labels
                ),
                "voltage_upper_slack": bound_values(
                    model.voltage_upper_slack, "ipopt_zU_out", original_labels
                ),
                "branch_slack": bound_values(
                    model.branch_slack, "ipopt_zU_out", model._branch_labels
                ),
            },
        },
    }


def _bus_solutions(case: PowerFlowCase, voltage: np.ndarray, ybus: csc_matrix) -> list[BusSolution]:
    calculated = calculate_power(ybus, voltage)
    return [
        BusSolution(
            number=bus.number,
            name=bus.name,
            voltage_pu=float(abs(voltage[index])),
            angle_degrees=float(np.rad2deg(np.angle(voltage[index]))),
            active_injection_mw=float(calculated[index].real * case.base_mva),
            reactive_injection_mvar=float(calculated[index].imag * case.base_mva),
        )
        for index, bus in enumerate(case.buses)
    ]


class ACOptimalPowerFlow:
    """Minimize AC voltage deviation (or real losses) with Pyomo and Ipopt.

    Non-slack active generation remains at the case schedule. Slack generation
    balances losses and uses parsed active-power limits when available.
    """

    def __init__(
        self,
        filename: str | Path,
        *,
        objective: Literal["voltage_deviation", "loss_minimization"] = "voltage_deviation",
        voltage_violation_penalty: float = 1000.0,
        branch_violation_penalty: float = 1000.0,
        voltage_reference: Mapping[int, float] | None = None,
        voltage_weights: Mapping[int, float] | None = None,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_cpu_time: float = 900.0,
        warm_start: bool = True,
        warm_start_linear_solver: Literal["scipy", "clu"] = "scipy",
        feasibility_restoration: bool = False,
        progressive_tightening: bool = False,
        print_iterations: bool = False,
        ipopt_executable: str | Path | None = None,
    ) -> None:
        if objective not in ("voltage_deviation", "loss_minimization"):
            raise ValueError("objective must be 'voltage_deviation' or 'loss_minimization'")
        if not math.isfinite(voltage_violation_penalty) or voltage_violation_penalty <= 0.0:
            raise ValueError("voltage_violation_penalty must be finite and greater than zero")
        if not math.isfinite(branch_violation_penalty) or branch_violation_penalty <= 0.0:
            raise ValueError("branch_violation_penalty must be finite and greater than zero")
        if warm_start_linear_solver not in ("scipy", "clu"):
            raise ValueError("warm_start_linear_solver must be 'scipy' or 'clu'")
        self.filename = Path(filename)
        tables = parse_dat_file(self.filename)
        self.case = build_power_flow_case(tables)
        constants = {
            row.get("Mn", "").upper(): float(row.get("Val", 0.0)) for row in tables.get("DCTE", [])
        }
        if tolerance is None:
            tolerance = constants.get("TEPA", 0.01) / self.case.base_mva
        if max_iterations is None:
            max_iterations = max(200, 2 * int(constants.get("ACIT", 100)))
        if tolerance <= 0.0:
            raise ValueError("tolerance must be greater than zero")
        if max_iterations <= 0:
            raise ValueError("max_iterations must be greater than zero")
        if max_cpu_time <= 0.0:
            raise ValueError("max_cpu_time must be greater than zero")
        self.objective = objective
        self.voltage_violation_penalty = float(voltage_violation_penalty)
        self.branch_violation_penalty = float(branch_violation_penalty)
        self.voltage_reference = None if voltage_reference is None else dict(voltage_reference)
        self.voltage_weights = None if voltage_weights is None else dict(voltage_weights)
        self.tolerance = float(tolerance)
        self.max_iterations = int(max_iterations)
        self.max_cpu_time = float(max_cpu_time)
        self.warm_start = bool(warm_start)
        self.warm_start_linear_solver = warm_start_linear_solver
        self.feasibility_restoration = bool(feasibility_restoration)
        self.progressive_tightening = bool(progressive_tightening)
        self.print_iterations = bool(print_iterations)
        self.ipopt_executable = ipopt_executable
        self.warm_start_iterations = 0
        self.reduction: ReducedCase | None = None
        self.result: ACOptimalPowerFlowResult | None = None
        self._model: pyo.ConcreteModel | None = None
        self._reference_case: PowerFlowCase | None = None

    def _prepare_case(
        self, reference_case: PowerFlowCase
    ) -> tuple[PowerFlowCase, ReducedCase, np.ndarray]:
        operating_case = deepcopy(reference_case)
        if self.warm_start:
            warm_solver = NewtonRaphsonPowerFlow(
                filename=self.filename,
                tolerance=self.tolerance,
                max_iterations=min(100, self.max_iterations),
                control_method="sequential",
                linear_solver=self.warm_start_linear_solver,
            )
            warm_solver.case = operating_case
            warm_result = warm_solver.solve()
            self.warm_start_iterations = warm_result.iterations
            if warm_result.converged and warm_solver.reduction is not None:
                reduction = warm_solver.reduction
                voltage = warm_result.voltage[reduction.representatives].copy()
                for index, bus in enumerate(operating_case.buses):
                    bus.voltage_pu = float(abs(warm_result.voltage[index]))
                    bus.angle_radians = float(np.angle(warm_result.voltage[index]))
                return operating_case, reduction, voltage

        operating_case = deepcopy(reference_case)
        reduction = reduce_low_impedance_branches(operating_case)
        reduced_case = reduction.case
        freeze_branch_matrix(reduced_case)
        voltage = reduced_case.initial_voltage
        if reconcile_tap_controls(reduced_case, voltage):
            voltage = reduced_case.initial_voltage
        _assign_island_references(reduced_case, build_ybus(reduced_case))
        return operating_case, reduction, voltage

    def solve(self) -> ACOptimalPowerFlowResult:
        reference_case = deepcopy(self.case)
        operating_case, reduction, initial_voltage = self._prepare_case(reference_case)
        relaxation_schedule = (0.05, 0.03, 0.0) if self.progressive_tightening else (0.0,)
        solve_schedule = []
        if self.feasibility_restoration:
            solve_schedule.append((relaxation_schedule[0], True))
        solve_schedule.extend((relaxation, False) for relaxation in relaxation_schedule)

        model: pyo.ConcreteModel | None = None
        previous_model: pyo.ConcreteModel | None = None
        total_iterations = 0
        restoration_iterations = 0
        tightening_stages = 0
        termination = "unknown"
        max_p = max_q = math.inf
        accepted = False
        failed_stage = "optimization"
        for limit_relaxation, restoring in solve_schedule:
            model = _build_acopf_model(
                operating_case,
                reference_case,
                reduction,
                initial_voltage,
                objective=self.objective,
                voltage_violation_penalty=self.voltage_violation_penalty,
                branch_violation_penalty=self.branch_violation_penalty,
                limit_relaxation=limit_relaxation,
                feasibility_restoration=restoring,
                voltage_reference=self.voltage_reference,
                voltage_weights=self.voltage_weights,
            )
            if previous_model is not None:
                _copy_model_variable_values(previous_model, model)
            self._model = model
            self._reference_case = reference_case
            solver_results, stage_iterations = solve_optimization_model(
                model,
                max_iterations=self.max_iterations,
                max_cpu_time=self.max_cpu_time,
                print_iterations=self.print_iterations,
                ipopt_executable=self.ipopt_executable,
            )
            stage_iterations = stage_iterations or 0
            total_iterations += stage_iterations
            if restoring:
                restoration_iterations = stage_iterations
            elif self.progressive_tightening:
                tightening_stages += 1
            failed_stage = (
                "feasibility restoration"
                if restoring
                else f"optimization at {limit_relaxation:.0%} limit relaxation"
            )
            termination = getattr(
                getattr(solver_results, "solver", None), "termination_condition", "unknown"
            )
            voltage = _model_voltage(model)
            max_p, max_q = _model_mismatch(model)
            accepted = termination in _ACCEPTED_TERMINATIONS
            if not accepted or max(max_p, max_q) > self.tolerance:
                break
            previous_model = model
            initial_voltage = voltage

        if model is None:
            raise RuntimeError("ACOPF solve schedule did not contain any optimization stages")
        voltage = _model_voltage(model)
        converged = accepted and max(max_p, max_q) <= self.tolerance
        failure_reason = None
        if not accepted:
            failure_reason = f"Ipopt terminated with {termination} during {failed_stage}"
        elif not converged:
            failure_reason = (
                f"Power-balance residuals exceed tolerance (P={max_p:.6g}, Q={max_q:.6g})"
            )

        reduced_case = reduction.case
        for index in model.SVC:
            reduced_case.svcs[index].q_mvar = (
                float(pyo.value(model.qsvc[index])) * operating_case.base_mva
            )
        for index in model.BUS:
            bus = reduced_case.buses[index]
            bus.voltage_pu = float(pyo.value(model.vm[index]))
            bus.angle_radians = float(pyo.value(model.va[index]))
            if index in model.PGEN:
                bus.bus_type = BusType.SLACK
            elif index in model.QGEN:
                bus.bus_type = BusType.PV
        expanded_voltage = reduction.expand_voltage(voltage)
        reduction.write_back(operating_case)
        for index, bus in enumerate(operating_case.buses):
            bus.voltage_pu = float(abs(expanded_voltage[index]))
            bus.angle_radians = float(np.angle(expanded_voltage[index]))
        ybus = build_ybus(operating_case)
        operating_case.solved_voltage = expanded_voltage.copy()
        self.case = operating_case
        self.reduction = reduction
        self.result = ACOptimalPowerFlowResult(
            converged=converged,
            iterations=total_iterations,
            max_mismatch=max(max_p, max_q),
            voltage=expanded_voltage,
            buses=_bus_solutions(operating_case, expanded_voltage, ybus),
            failure_reason=failure_reason,
            objective_value=float(pyo.value(model.objective)),
            duals=_duals(model, reduction),
            termination_condition=str(termination),
            warm_start_iterations=self.warm_start_iterations,
            feasibility_restoration_iterations=restoration_iterations,
            progressive_tightening_stages=tightening_stages,
        )
        return self.result

    def get_result_duals(self, result: ACOptimalPowerFlowResult | None = None) -> dict[str, Any]:
        """Return labeled Ipopt constraint and variable-bound multipliers."""
        selected = self.result if result is None else result
        if selected is None:
            raise ValueError("Call solve() before requesting ACOPF duals")
        if not isinstance(selected, ACOptimalPowerFlowResult):
            raise TypeError("result must be an ACOptimalPowerFlowResult")
        return deepcopy(selected.duals)

    def get_violations(
        self, result: ACOptimalPowerFlowResult | None = None
    ) -> list[ACOPFViolation]:
        """Return material power-balance, equipment-limit, and branch overloads.

        Power and voltage violations use the solver tolerance. Branch ratings
        are diagnostic only and are obtained from the same collapsed-link flow
        calculation used by ``ExportSolution``.
        """
        selected = self.result if result is None else result
        if selected is None or self._model is None or self._reference_case is None:
            raise ValueError("Call solve() before requesting ACOPF violations")
        if selected is not self.result:
            raise ValueError("result must be the result returned by this solver")

        model = self._model
        reduction = self.reduction
        if reduction is None:
            raise ValueError("The solved network reduction is unavailable")
        violations: list[ACOPFViolation] = []
        voltage = _model_voltage(model)
        calculated = calculate_power(model._ybus, voltage)
        p_specified = model._p_fixed.copy()
        q_specified = model._q_fixed.copy()
        for index in model._p_groups:
            p_specified[index] += float(pyo.value(model.pgen[index]))
        for index in model._q_groups:
            q_specified[index] += float(pyo.value(model.qgen[index]))
        for index, data in model._svc_data.items():
            q_specified[int(data["bus_index"])] += float(pyo.value(model.qsvc[index]))

        for index, bus in enumerate(reduction.case.buses):
            p_residual = float(calculated[index].real - p_specified[index])
            if abs(p_residual) > self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "active_power_balance",
                        str(bus.number),
                        p_residual,
                        self.tolerance,
                        abs(p_residual) - self.tolerance,
                        "pu",
                    )
                )
            q_residual = float(calculated[index].imag - q_specified[index])
            if abs(q_residual) > self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "reactive_power_balance",
                        str(bus.number),
                        q_residual,
                        self.tolerance,
                        abs(q_residual) - self.tolerance,
                        "pu",
                    )
                )

        for index, original_bus in enumerate(self._reference_case.buses):
            magnitude = float(abs(selected.voltage[index]))
            if magnitude < original_bus.voltage_min_pu - self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "voltage_lower_limit",
                        str(original_bus.number),
                        magnitude,
                        original_bus.voltage_min_pu,
                        original_bus.voltage_min_pu - magnitude,
                        "pu",
                    )
                )
            if magnitude > original_bus.voltage_max_pu + self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "voltage_upper_limit",
                        str(original_bus.number),
                        magnitude,
                        original_bus.voltage_max_pu,
                        magnitude - original_bus.voltage_max_pu,
                        "pu",
                    )
                )

        generation_tolerance = self.tolerance * self.case.base_mva
        for index, group in model._p_groups.items():
            value = float(pyo.value(model.pgen[index])) * self.case.base_mva
            label = str(reduction.case.buses[index].number)
            for category, limit, amount in (
                (
                    "slack_active_generation_lower_limit",
                    group.lower_mw,
                    (group.lower_mw or 0) - value,
                ),
                (
                    "slack_active_generation_upper_limit",
                    group.upper_mw,
                    value - (group.upper_mw or 0),
                ),
            ):
                if limit is not None and amount > generation_tolerance:
                    violations.append(ACOPFViolation(category, label, value, limit, amount, "MW"))
        for index, group in model._q_groups.items():
            value = float(pyo.value(model.qgen[index])) * self.case.base_mva
            label = str(reduction.case.buses[index].number)
            for category, limit, amount in (
                ("generator_reactive_lower_limit", group.lower_mw, (group.lower_mw or 0) - value),
                ("generator_reactive_upper_limit", group.upper_mw, value - (group.upper_mw or 0)),
            ):
                if limit is not None and amount > generation_tolerance:
                    violations.append(ACOPFViolation(category, label, value, limit, amount, "Mvar"))
        for index, data in model._svc_data.items():
            q_value = float(pyo.value(model.qsvc[index]))
            terminal_voltage = float(pyo.value(model.vm[int(data["bus_index"])])) * float(
                data["scale"]
            )
            lower = float(data["q_min"]) * terminal_voltage**2
            upper = float(data["q_max"]) * terminal_voltage**2
            if q_value < lower - self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "svc_lower_limit",
                        f"{int(data['bus_number'])}:svc{index + 1}",
                        q_value * self.case.base_mva,
                        lower * self.case.base_mva,
                        (lower - q_value) * self.case.base_mva,
                        "Mvar",
                    )
                )
            if q_value > upper + self.tolerance:
                violations.append(
                    ACOPFViolation(
                        "svc_upper_limit",
                        f"{int(data['bus_number'])}:svc{index + 1}",
                        q_value * self.case.base_mva,
                        upper * self.case.base_mva,
                        (q_value - upper) * self.case.base_mva,
                        "Mvar",
                    )
                )

        from pf_tool.exporter import _SolutionTables

        flows = _SolutionTables(self).flows
        for position, (branch, flow) in enumerate(
            zip(self.case.branches, flows, strict=True), start=1
        ):
            if not 0.0 < branch.rating_mva < _UNLIMITED_RATING_MVA:
                continue
            apparent_power_mva = max(abs(flow.from_power), abs(flow.to_power)) * self.case.base_mva
            excess = apparent_power_mva - branch.rating_mva
            if excess > generation_tolerance:
                violations.append(
                    ACOPFViolation(
                        "branch_rating",
                        f"{branch.from_bus}-{branch.to_bus}:{branch.circuit} (branch {position})",
                        apparent_power_mva,
                        branch.rating_mva,
                        excess,
                        "MVA",
                    )
                )
        return violations


__all__ = ["ACOPFViolation", "ACOptimalPowerFlow", "ACOptimalPowerFlowResult"]
