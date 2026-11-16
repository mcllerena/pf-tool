"""Pyomo/Ipopt optimization-based AC power-flow solver.

The power flow is posed as a nonlinear program (following the o-grid
optimization ACPF): signed P/Q balance residuals are explicit variables,
bounded per bus and in aggregate by the convergence tolerance, and their
tolerance-normalized squares are minimized together with a light
regularization towards the initial state.  A feasible point of the NLP is
therefore a converged power flow.

SVCs are modelled by operating region, as in the Newton solver: a regulating
SVC satisfies its voltage-control law (``P``: reactive-power droop, ``I``:
current droop) within the tolerance, while a saturated SVC injects
``Qlim·Vterminal²``.  Region changes, generator reactive limits, switched
shunts and LCC set-points are updated between successive NLP solves.
Transformer taps and phase shifts are taken from the case (after the stored
operating point reconciliation or a Newton warm start that solves the
CTAP/CPHS controls).
"""

from __future__ import annotations

import logging
import math
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pyomo.environ as pyo
from pyomo.opt import SolverFactory, TerminationCondition
from scipy.sparse import csc_matrix
from scipy.sparse.csgraph import connected_components

from pf_tool.models.bus import BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.hvdc_lcc import update_lcc_from_dc_solution
from pf_tool.models.network_reduction import reduce_low_impedance_branches
from pf_tool.models.shunt import adjust_controlled_shunts
from pf_tool.models.svc import active_svc_states, refresh_svc_limits
from pf_tool.models.transformer import reconcile_tap_controls
from pf_tool.newton_raphson import (
    BusSolution,
    NewtonRaphsonPowerFlow,
    PowerFlowResult,
)
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.network import build_ybus, calculate_power, freeze_branch_matrix
from pf_tool.utils.utils import build_power_flow_case

_IPOPT_ACCEPTED_TERMINATIONS = {
    TerminationCondition.optimal,
    TerminationCondition.locallyOptimal,
    TerminationCondition.feasible,
}
# Weight of the squared state deviation relative to the normalized residuals.
_REGULARIZATION_WEIGHT = 1.0e-3
# Margin (pu) used when comparing an SVC output with its capability limits.
_SVC_LIMIT_MARGIN = 1.0e-6


def _voltage_bounds(case: PowerFlowCase, index: int) -> tuple[float, float]:
    # Broad numerical guard (as in the Newton solver); operating limits from
    # DGLT are a post-solution check, not a feasibility constraint.
    return 0.4, 2.0


def _assign_island_references(case: PowerFlowCase, ybus: csc_matrix) -> None:
    graph = ybus.copy()
    graph.data = np.ones(graph.nnz, dtype=np.int8)
    island_count, labels = connected_components(graph, directed=False)
    for island in range(island_count):
        indices = np.flatnonzero(labels == island)
        if any(case.buses[index].bus_type == BusType.SLACK for index in indices):
            continue
        candidates = [index for index in indices if case.buses[index].bus_type == BusType.PV]
        if not candidates:
            candidates = indices.tolist()
        reference = max(
            candidates,
            key=lambda index: case.buses[index].p_generation_mw,
        )
        case.buses[reference].bus_type = BusType.SLACK


def _network_mismatch(case: PowerFlowCase, ybus: csc_matrix, voltage: np.ndarray) -> float:
    residual = calculate_power(ybus, voltage) - case.specified_power
    active = [index for index, bus in enumerate(case.buses) if bus.bus_type != BusType.SLACK]
    reactive = [index for index, bus in enumerate(case.buses) if bus.bus_type == BusType.PQ]
    values = [np.abs(residual.real[active]), np.abs(residual.imag[reactive])]
    nonempty = [part for part in values if part.size]
    return float(max((float(np.max(part)) for part in nonempty), default=0.0))


def initial_svc_regions(case: PowerFlowCase, voltage: np.ndarray) -> dict[int, int]:
    """SVC operating regions (-1 lower limit, 0 regulating, +1 upper limit)."""
    states = active_svc_states(case)
    refresh_svc_limits(states, np.abs(voltage))
    return {state.svc_index: state.limit_state for state in states if state.active}


def build_optimization_model(
    case: PowerFlowCase,
    *,
    initial_voltage: np.ndarray | None = None,
    tolerance: float = 1.0e-4,
    svc_regions: dict[int, int] | None = None,
) -> pyo.ConcreteModel:
    """Build the residual-bounded AC power-flow NLP for ``case``.

    Per-bus P/Q residuals and regulating-SVC control residuals are bounded by
    ``tolerance``; the aggregate P and Q residuals are bounded as well.  The
    objective minimizes the tolerance-normalized squared residuals plus a
    small regularization towards the initial state.  Voltage magnitudes only
    have broad numerical bounds: operating limits are reported separately.
    """
    if tolerance <= 0.0:
        raise ValueError("tolerance must be greater than zero")
    ybus = build_ybus(case).tocsr()
    _assign_island_references(case, ybus.tocsc())
    bus_count = len(case.buses)
    bus_ids = list(range(bus_count))
    initial = (
        case.initial_voltage.copy()
        if initial_voltage is None
        else np.asarray(initial_voltage, dtype=np.complex128).copy()
    )
    if initial.shape != (bus_count,) or not np.isfinite(initial).all():
        raise ValueError("initial_voltage must contain one finite value per bus")
    if svc_regions is None:
        svc_regions = initial_svc_regions(case, initial)

    voltage_seed = np.empty(bus_count, dtype=float)
    angle_seed = np.empty(bus_count, dtype=float)
    voltage_limits: dict[int, tuple[float, float]] = {}
    for index in bus_ids:
        lower, upper = _voltage_bounds(case, index)
        voltage_limits[index] = (lower, upper)
        voltage_seed[index] = float(np.clip(abs(initial[index]), lower, upper))
        angle_seed[index] = float(np.clip(np.angle(initial[index]), -math.pi, math.pi))

    states = active_svc_states(case)
    refresh_svc_limits(states, voltage_seed)
    active_states = [state for state in states if state.active]
    svc_initial_by_bus: dict[int, float] = {}
    for state in states:
        svc_initial_by_bus[state.bus_index] = (
            svc_initial_by_bus.get(state.bus_index, 0.0)
            + case.svcs[state.svc_index].q_mvar / case.base_mva
        )

    p_buses = [i for i, bus in enumerate(case.buses) if bus.bus_type != BusType.SLACK]
    q_buses = [i for i, bus in enumerate(case.buses) if bus.bus_type == BusType.PQ]
    specified = case.specified_power
    p_spec_values = {index: float(specified[index].real) for index in bus_ids}
    q_spec_values = {
        index: float(specified[index].imag) - svc_initial_by_bus.get(index, 0.0)
        for index in bus_ids
    }
    seed_power = calculate_power(ybus.tocsc(), voltage_seed * np.exp(1j * angle_seed))
    svc_seed_by_bus: dict[int, float] = {}
    for state in active_states:
        svc_seed_by_bus[state.bus_index] = svc_seed_by_bus.get(state.bus_index, 0.0) + state.q_pu
    neighbors: dict[int, list[tuple[int, float, float]]] = {}
    for index in bus_ids:
        start, stop = ybus.indptr[index], ybus.indptr[index + 1]
        neighbors[index] = [
            (int(other), float(value.real), float(value.imag))
            for other, value in zip(ybus.indices[start:stop], ybus.data[start:stop])
        ]

    model = pyo.ConcreteModel(name="optimization_ac_power_flow")
    model.BUS = pyo.Set(initialize=bus_ids, ordered=True)
    model.PBUS = pyo.Set(initialize=p_buses, ordered=True)
    model.QBUS = pyo.Set(initialize=q_buses, ordered=True)
    model.SVC = pyo.Set(initialize=list(range(len(active_states))), ordered=True)

    def clipped(value: float) -> float:
        return float(np.clip(value, -tolerance, tolerance))

    model.vm = pyo.Var(
        model.BUS,
        initialize={index: voltage_seed[index] for index in bus_ids},
        bounds=lambda m, index: voltage_limits[index],
        within=pyo.NonNegativeReals,
    )
    model.va = pyo.Var(
        model.BUS,
        initialize={index: angle_seed[index] for index in bus_ids},
        bounds=(-math.pi, math.pi),
        within=pyo.Reals,
    )
    model.p_residual = pyo.Var(
        model.PBUS,
        initialize={
            index: clipped(seed_power[index].real - p_spec_values[index]) for index in p_buses
        },
        bounds=(-tolerance, tolerance),
        within=pyo.Reals,
    )
    model.q_residual = pyo.Var(
        model.QBUS,
        initialize={
            index: clipped(
                seed_power[index].imag - q_spec_values[index] - svc_seed_by_bus.get(index, 0.0)
            )
            for index in q_buses
        },
        bounds=(-tolerance, tolerance),
        within=pyo.Reals,
    )
    for index, bus in enumerate(case.buses):
        if bus.bus_type in (BusType.SLACK, BusType.PV):
            model.vm[index].fix(voltage_seed[index])
        if bus.bus_type == BusType.SLACK:
            model.va[index].fix(angle_seed[index])

    model.p_calculated = pyo.Expression(
        model.BUS,
        rule=lambda m, index: sum(
            m.vm[index]
            * m.vm[other]
            * (
                conductance * pyo.cos(m.va[index] - m.va[other])
                + susceptance * pyo.sin(m.va[index] - m.va[other])
            )
            for other, conductance, susceptance in neighbors[index]
        ),
    )
    model.q_calculated = pyo.Expression(
        model.BUS,
        rule=lambda m, index: sum(
            m.vm[index]
            * m.vm[other]
            * (
                conductance * pyo.sin(m.va[index] - m.va[other])
                - susceptance * pyo.cos(m.va[index] - m.va[other])
            )
            for other, conductance, susceptance in neighbors[index]
        ),
    )
    model.p_balance = pyo.Constraint(
        model.PBUS,
        rule=lambda m, index: m.p_calculated[index] - p_spec_values[index] == m.p_residual[index],
    )

    svc_data: dict[int, dict[str, Any]] = {}
    for model_index, state in enumerate(active_states):
        low_vm, high_vm = voltage_limits[state.bus_index]
        low_vm *= state.device_voltage_scale
        high_vm *= state.device_voltage_scale
        region = int(svc_regions.get(state.svc_index, state.limit_state))
        svc_data[model_index] = {
            "svc_index": state.svc_index,
            "device": state.bus_index,
            "control": state.control_bus_index,
            "mode": state.mode,
            "slope": state.slope,
            "q_min": state.q_min_pu,
            "q_max": state.q_max_pu,
            "reference": state.reference_voltage,
            "device_scale": state.device_voltage_scale,
            "control_scale": state.control_voltage_scale,
            "region": region,
            "bounds": (
                min(state.q_min_pu * low_vm**2, state.q_min_pu * high_vm**2),
                max(state.q_max_pu * low_vm**2, state.q_max_pu * high_vm**2),
            ),
            "initial": state.q_pu,
        }

    model.qsvc = pyo.Var(
        model.SVC,
        initialize={
            i: float(np.clip(data["initial"], *data["bounds"])) for i, data in svc_data.items()
        },
        bounds=lambda m, index: svc_data[index]["bounds"],
        within=pyo.Reals,
    )
    # Unbounded but tolerance-penalized: an SVC that cannot regulate keeps the
    # NLP feasible and is moved to its saturated region afterwards.
    model.svc_residual = pyo.Var(model.SVC, initialize=0.0, within=pyo.Reals)
    svc_by_bus: dict[int, list[int]] = {}
    for model_index, data in svc_data.items():
        svc_by_bus.setdefault(int(data["device"]), []).append(model_index)
    model.q_balance = pyo.Constraint(
        model.QBUS,
        rule=lambda m, index: (
            m.q_calculated[index]
            - q_spec_values[index]
            - sum(m.qsvc[k] for k in svc_by_bus.get(index, []))
            == m.q_residual[index]
        ),
    )
    model.aggregate_p = pyo.Constraint(
        expr=pyo.inequality(
            -tolerance, sum(model.p_residual[index] for index in model.PBUS), tolerance
        )
    )
    model.aggregate_q = pyo.Constraint(
        expr=pyo.inequality(
            -tolerance, sum(model.q_residual[index] for index in model.QBUS), tolerance
        )
    )

    def device_voltage(m: Any, data: dict[str, Any]) -> Any:
        return m.vm[int(data["device"])] * float(data["device_scale"])

    def svc_rule(m: Any, index: int):
        data = svc_data[index]
        if data["region"] != 0:
            m.svc_residual[index].fix(0.0)
            limit = data["q_min"] if data["region"] < 0 else data["q_max"]
            return m.qsvc[index] == limit * device_voltage(m, data) ** 2
        control_voltage = m.vm[int(data["control"])] * float(data["control_scale"])
        expression = control_voltage - float(data["reference"])
        if data["mode"] == "I":
            expression += float(data["slope"]) * m.qsvc[index] / device_voltage(m, data)
        else:
            expression += float(data["slope"]) * m.qsvc[index]
        return expression == m.svc_residual[index]

    model.svc_control = pyo.Constraint(model.SVC, rule=svc_rule)

    scale = 1.0 / tolerance
    model.objective = pyo.Objective(
        expr=sum((scale * model.p_residual[index]) ** 2 for index in model.PBUS)
        + sum((scale * model.q_residual[index]) ** 2 for index in model.QBUS)
        + sum((scale * model.svc_residual[index]) ** 2 for index in model.SVC)
        + _REGULARIZATION_WEIGHT
        * sum(
            (model.vm[index] - voltage_seed[index]) ** 2
            + (model.va[index] - angle_seed[index]) ** 2
            for index in model.BUS
        ),
        sense=pyo.minimize,
    )
    model._case = case
    model._tolerance = tolerance
    model._svc_data = svc_data
    model._ybus = ybus.tocsc()
    model._p_spec = np.array([p_spec_values[index] for index in bus_ids])
    model._q_spec = np.array([q_spec_values[index] for index in bus_ids])
    return model


def solution_metrics(model: pyo.ConcreteModel) -> dict[str, float | bool]:
    """Recompute P/Q mismatches and SVC control errors from the solved state.

    The residuals are evaluated directly from ``Ybus`` and the solved
    voltages (not read from the NLP residual variables), so the convergence
    test does not depend on the solver's constraint-violation tolerance.
    """
    voltage = _model_voltage(model)
    power = calculate_power(model._ybus, voltage)
    svc_injection = np.zeros(len(voltage))
    for index in model.SVC:
        svc_injection[int(model._svc_data[index]["device"])] += float(pyo.value(model.qsvc[index]))
    p_buses = np.fromiter(model.PBUS, dtype=np.int64)
    q_buses = np.fromiter(model.QBUS, dtype=np.int64)
    p_residuals = (power.real - model._p_spec)[p_buses]
    q_residuals = (power.imag - model._q_spec - svc_injection)[q_buses]
    max_p = float(np.max(np.abs(p_residuals))) if p_residuals.size else 0.0
    max_q = float(np.max(np.abs(q_residuals))) if q_residuals.size else 0.0
    max_svc = 0.0
    magnitude = np.abs(voltage)
    for index in model.SVC:
        data = model._svc_data[index]
        q = float(pyo.value(model.qsvc[index]))
        device = magnitude[int(data["device"])] * data["device_scale"]
        if data["region"] != 0:
            limit = data["q_min"] if data["region"] < 0 else data["q_max"]
            error = q - limit * device**2
        else:
            control = magnitude[int(data["control"])] * data["control_scale"]
            droop = q / device if data["mode"] == "I" else q
            error = control - data["reference"] + data["slope"] * droop
        max_svc = max(max_svc, abs(error))
    aggregate_p = abs(float(np.sum(p_residuals)))
    aggregate_q = abs(float(np.sum(q_residuals)))
    tolerance = float(model._tolerance)
    return {
        "max_p": max_p,
        "max_q": max_q,
        "max_svc": max_svc,
        "aggregate_p": aggregate_p,
        "aggregate_q": aggregate_q,
        "converged": (
            max_p <= tolerance
            and max_q <= tolerance
            and max_svc <= tolerance
            and aggregate_p <= tolerance
            and aggregate_q <= tolerance
        ),
    }


def update_svc_regions(model: pyo.ConcreteModel, regions: dict[int, int]) -> bool:
    """Switch SVCs between regulating and saturated regions after a solve.

    A regulating SVC whose output exceeds ``Qlim·Vt²`` saturates at that
    limit; a saturated SVC whose control law asks for an output inside its
    capability returns to regulation.
    """
    changed = False
    for index in model.SVC:
        data = model._svc_data[index]
        device = float(pyo.value(model.vm[int(data["device"])])) * data["device_scale"]
        control = float(pyo.value(model.vm[int(data["control"])])) * data["control_scale"]
        lower = data["q_min"] * device**2
        upper = data["q_max"] * device**2
        region = data["region"]
        if abs(data["slope"]) <= 1.0e-12:
            requested = upper if control < data["reference"] else lower
        else:
            requested = (data["reference"] - control) / data["slope"]
            if data["mode"] == "I":
                requested *= device
        if region == 0:
            q = float(pyo.value(model.qsvc[index]))
            # Saturate when the output left its capability, or when it sits on
            # the limit while the control law asks for more.
            if q < lower - _SVC_LIMIT_MARGIN or (
                q <= lower + _SVC_LIMIT_MARGIN and requested < lower - _SVC_LIMIT_MARGIN
            ):
                new_region = -1
            elif q > upper + _SVC_LIMIT_MARGIN or (
                q >= upper - _SVC_LIMIT_MARGIN and requested > upper + _SVC_LIMIT_MARGIN
            ):
                new_region = 1
            else:
                new_region = 0
        else:
            # A saturated SVC stays on its limit while the control law still
            # asks for more; otherwise it first returns to regulation (never
            # jumping straight to the opposite limit).
            still_saturated = (
                requested < lower - _SVC_LIMIT_MARGIN
                if region < 0
                else requested > upper + _SVC_LIMIT_MARGIN
            )
            new_region = region if still_saturated else 0
        if new_region != region:
            regions[int(data["svc_index"])] = new_region
            changed = True
    return changed


def _parse_ipopt_iterations(log_text: str) -> int | None:
    match = re.search(r"Number of Iterations\.+:?\s+(\d+)", log_text)
    return int(match.group(1)) if match is not None else None


def solve_optimization_model(
    model: pyo.ConcreteModel,
    *,
    max_iterations: int = 200,
    max_cpu_time: float = 900.0,
    print_iterations: bool = False,
    ipopt_executable: str | Path | None = None,
) -> tuple[Any, int | None]:
    """Solve ``model`` with Ipopt, raising a clear error if it is unavailable.

    ``ipopt_executable`` is the path to the Ipopt binary; ``None`` searches ``PATH``.
    """
    if ipopt_executable is None:
        solver = SolverFactory("ipopt")
    else:
        solver = SolverFactory("ipopt", executable=str(ipopt_executable))
    if solver is None or not solver.available(exception_flag=False):
        location = f"at {ipopt_executable}" if ipopt_executable is not None else "on PATH"
        raise RuntimeError(
            f"Ipopt is not available {location}. Install Ipopt and add it to PATH, "
            "or pass ipopt_executable='path/to/ipopt'."
        )
    solver.options["max_iter"] = int(max_iterations)
    solver.options["max_cpu_time"] = float(max_cpu_time)
    solver.options["tol"] = 1.0e-8
    solver.options["acceptable_tol"] = 1.0e-6
    # Power-balance equations must hold far below the convergence tolerance
    # (Ipopt's default absolute constraint tolerance is 1e-4).
    solver.options["constr_viol_tol"] = 1.0e-9
    solver.options["acceptable_constr_viol_tol"] = 1.0e-8
    solver.options["mu_strategy"] = "adaptive"
    if print_iterations:
        solver.options["print_level"] = 5

    log_path: Path | None = None
    pyomo_logger = logging.getLogger("pyomo.core")
    previous_level = pyomo_logger.level
    pyomo_logger.setLevel(logging.ERROR)
    try:
        with tempfile.NamedTemporaryFile(
            prefix="power-flow-ipopt-",
            suffix=".log",
            delete=False,
        ) as log_file:
            log_path = Path(log_file.name)
        results = solver.solve(
            model,
            tee=print_iterations,
            logfile=str(log_path),
            load_solutions=False,
        )
        if len(results.solution) > 0:
            model.solutions.load_from(results)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        return results, _parse_ipopt_iterations(log_text)
    finally:
        pyomo_logger.setLevel(previous_level)
        if log_path is not None:
            log_path.unlink(missing_ok=True)


def _enforce_generator_q_limits(
    case: PowerFlowCase,
    ybus: csc_matrix,
    voltage: np.ndarray,
) -> bool:
    calculated = calculate_power(ybus, voltage)
    svc_by_bus: dict[int, float] = {}
    for svc in case.svcs:
        svc_by_bus[svc.bus] = svc_by_bus.get(svc.bus, 0.0) + svc.q_mvar
    changed = False
    for index, bus in enumerate(case.buses):
        if (
            bus.bus_type != BusType.PV
            or bus.q_min_mvar is None
            or bus.q_max_mvar is None
            or bus.q_max_mvar <= bus.q_min_mvar
        ):
            continue
        required_generation = (
            calculated[index].imag * case.base_mva
            + bus.q_load_mvar
            - svc_by_bus.get(bus.number, 0.0)
        )
        if required_generation < bus.q_min_mvar - 0.1:
            limit = bus.q_min_mvar
        elif required_generation > bus.q_max_mvar + 0.1:
            limit = bus.q_max_mvar
        else:
            continue
        bus.q_generation_mvar = limit + svc_by_bus.get(bus.number, 0.0)
        bus.bus_type = BusType.PQ
        changed = True
    return changed


def _store_svc_outputs(model: pyo.ConcreteModel, case: PowerFlowCase) -> None:
    for index in model.SVC:
        data = model._svc_data[index]
        svc = case.svcs[int(data["svc_index"])]
        solved = float(pyo.value(model.qsvc[index])) * case.base_mva
        case.buses[int(data["device"])].q_generation_mvar += solved - svc.q_mvar
        svc.q_mvar = solved


def _model_voltage(model: pyo.ConcreteModel) -> np.ndarray:
    return np.array(
        [
            float(pyo.value(model.vm[index])) * np.exp(1j * float(pyo.value(model.va[index])))
            for index in model.BUS
        ],
        dtype=np.complex128,
    )


def _make_bus_solutions(
    case: PowerFlowCase,
    voltage: np.ndarray,
    ybus: csc_matrix,
) -> list[BusSolution]:
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


class OptimizationPowerFlow:
    """Solve a ``.dat`` case as a Pyomo nonlinear program with Ipopt.

    With ``warm_start=True`` (default) a Newton-Raphson solve provides the
    initial point and the control state (bus types after reactive limits,
    shunt steps, LTC taps, phase shifts, SVC outputs, LCC set-points).  With
    ``warm_start=False`` the NLP starts from the voltages stored in the case,
    after reconciling transformer settings with them.

    Ipopt is looked up on ``PATH`` unless ``ipopt_executable`` gives the path to
    the binary.
    """

    def __init__(
        self,
        data_file: str | Path,
        *,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_cpu_time: float = 900.0,
        max_control_passes: int = 20,
        warm_start: bool = True,
        print_iterations: bool = False,
        ipopt_executable: str | Path | None = None,
    ) -> None:
        self.data_file = Path(data_file)
        tables = parse_dat_file(self.data_file)
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
        if max_control_passes < 0:
            raise ValueError("max_control_passes cannot be negative")
        self.tolerance = float(tolerance)
        self.max_iterations = int(max_iterations)
        self.max_cpu_time = float(max_cpu_time)
        self.max_control_passes = int(max_control_passes)
        self.warm_start = bool(warm_start)
        self.print_iterations = bool(print_iterations)
        self.ipopt_executable = ipopt_executable
        self.tap_voltage_band = float(constants.get("TLTC", 0.01))
        self.warm_start_iterations = 0
        self.warm_start_converged = False
        self.nlp_solves = 0
        self.reduction = None
        self.result: PowerFlowResult | None = None

    def _newton_warm_start(
        self, work_case: PowerFlowCase
    ) -> tuple[PowerFlowCase, np.ndarray, csc_matrix] | None:
        warm_case = deepcopy(work_case)
        warm_solver = object.__new__(NewtonRaphsonPowerFlow)
        warm_solver.case = warm_case
        warm_solver.tolerance = self.tolerance
        warm_solver.max_iterations = min(100, self.max_iterations)
        warm_solver.tap_voltage_band = self.tap_voltage_band
        warm_solver.result = None
        voltage, ybus, iterations, mismatch, reason = warm_solver._solve_reduced_case()
        self.warm_start_iterations = iterations
        self.warm_start_converged = reason is None and mismatch <= self.tolerance
        if not np.isfinite(voltage).all():
            return None
        return warm_case, voltage, ybus

    def solve(self) -> PowerFlowResult:
        original_case = self.case
        reduction = reduce_low_impedance_branches(original_case)
        work_case = reduction.case
        freeze_branch_matrix(work_case)
        ybus = build_ybus(work_case)
        _assign_island_references(work_case, ybus)
        seed = work_case.initial_voltage
        if reconcile_tap_controls(work_case, seed):
            ybus = build_ybus(work_case)
        self.warm_start_iterations = 0
        self.warm_start_converged = False
        self.nlp_solves = 0

        if self.warm_start and work_case.options.get("NEWT", "L") == "L":
            warm = self._newton_warm_start(work_case)
            if warm is not None and (
                self.warm_start_converged
                or _network_mismatch(warm[0], warm[2], warm[1])
                < _network_mismatch(work_case, ybus, seed)
            ):
                work_case, seed, ybus = warm
                reduction.case = work_case

        regions = initial_svc_regions(work_case, seed)
        model: pyo.ConcreteModel | None = None
        ipopt_iterations = 0
        final_termination: Any = None
        pending_changes = False
        voltage = seed

        for control_pass in range(self.max_control_passes + 1):
            model = build_optimization_model(
                work_case,
                initial_voltage=seed,
                tolerance=self.tolerance,
                svc_regions=regions,
            )
            solver_results, iterations = solve_optimization_model(
                model,
                max_iterations=self.max_iterations,
                max_cpu_time=self.max_cpu_time,
                print_iterations=self.print_iterations,
                ipopt_executable=self.ipopt_executable,
            )
            self.nlp_solves += 1
            ipopt_iterations += iterations or 0
            final_termination = getattr(
                getattr(solver_results, "solver", None), "termination_condition", None
            )
            voltage = _model_voltage(model)
            if not np.isfinite(voltage).all():
                break
            ybus = build_ybus(work_case)
            svc_changed = update_svc_regions(model, regions)
            _store_svc_outputs(model, work_case)
            q_limits_changed = (
                _enforce_generator_q_limits(work_case, ybus, voltage)
                if work_case.options.get("QLIM", "L") == "L"
                else False
            )
            shunts_changed = (
                adjust_controlled_shunts(work_case, voltage)
                if work_case.options.get("STEP", "L") == "L"
                else False
            )
            lcc_changed = update_lcc_from_dc_solution(work_case, voltage)
            pending_changes = svc_changed or q_limits_changed or shunts_changed or lcc_changed
            if not pending_changes or control_pass == self.max_control_passes:
                break
            seed = voltage

        if model is None:
            raise RuntimeError("The optimization solver did not produce a model result")

        metrics = solution_metrics(model)
        accepted_termination = final_termination in _IPOPT_ACCEPTED_TERMINATIONS
        converged = accepted_termination and bool(metrics["converged"]) and not pending_changes
        failure_reason = None
        if not accepted_termination:
            failure_reason = f"Ipopt terminated with {final_termination}"
        elif not metrics["converged"]:
            failure_reason = (
                "Power-flow residuals exceed tolerance "
                f"(P={metrics['max_p']:.6g}, Q={metrics['max_q']:.6g}, "
                f"SVC={metrics['max_svc']:.6g}, "
                f"aggregate P={metrics['aggregate_p']:.6g}, "
                f"aggregate Q={metrics['aggregate_q']:.6g})"
            )
        elif pending_changes:
            failure_reason = (
                f"Control updates did not settle within {self.max_control_passes} passes"
            )

        expanded_voltage = reduction.expand_voltage(voltage)
        reduction.case = work_case
        reduction.write_back(original_case)
        self.reduction = reduction
        original_ybus = build_ybus(original_case)
        original_case.solved_voltage = expanded_voltage.copy()
        self.result = PowerFlowResult(
            converged=converged,
            iterations=ipopt_iterations,
            max_mismatch=float(
                max(float(metrics["max_p"]), float(metrics["max_q"]), float(metrics["max_svc"]))
            ),
            voltage=expanded_voltage,
            buses=_make_bus_solutions(original_case, expanded_voltage, original_ybus),
            failure_reason=failure_reason,
        )
        return self.result


__all__ = [
    "OptimizationPowerFlow",
    "build_optimization_model",
    "initial_svc_regions",
    "solution_metrics",
    "solve_optimization_model",
    "update_svc_regions",
]
