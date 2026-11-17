"""Sparse polar-coordinate Newton-Raphson AC power-flow solver."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy.sparse import bmat, coo_matrix, csc_matrix, diags, hstack, vstack
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, splu, spsolve

from pf_tool.models.bus import BusType
from pf_tool.models.hvdc_lcc import HVDCLink, converter_operating_point, update_lcc_from_dc_solution
from pf_tool.models.network_reduction import reduce_low_impedance_branches
from pf_tool.models.shunt import SwitchedShunt, adjust_controlled_shunts, snap_to_bank_steps
from pf_tool.models.svc import (
    SVCState,
    active_svc_states,
    refresh_svc_limits,
    store_svc_outputs,
)
from pf_tool.models.transformer import TapControl, branch_sensitivity, reconcile_tap_controls
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.network import build_ybus, calculate_power, freeze_branch_matrix
from pf_tool.utils.utils import build_power_flow_case

# Largest tap-ratio / phase-angle (rad) move allowed in one Newton iteration.
_MAX_TAP_STEP = 0.05
_MAX_PHASE_STEP = 0.1
# Consecutive iterations a control may push against its limit before it is
# fixed at that limit and its regulation equation is dropped.
_BOUND_HITS_TO_LIMIT = 3
# A single Newton step beyond these sizes marks a control as conflicting.
_CONFLICT_TAP_STEP = 0.2
_CONFLICT_PHASE_STEP = 1.0

_CONTROL_METHODS = ("simultaneous", "sequential")
# Simultaneous method: slope of the LTC / switched-shunt dead-band laws.  Inside
# the band the device holds its start setting; outside it moves until the
# controlled voltage sits on the band edge, offset by gain × device move
# (≤ about 1e-4 pu for taps, a few 1e-4 pu for shunts).  The slope also keeps
# devices that regulate the same bus from making the Jacobian singular.
_TAP_DEADBAND_GAIN = 1e-3
_SHUNT_DEADBAND_GAIN = 1e-4
# Step used to differentiate the LCC converter reactive power numerically.
_LCC_VOLTAGE_STEP = 1e-6

# Columns of the ``print_iterations`` table.
_COLUMNS = (
    ("Pass", 4),
    ("Iter", 4),
    ("Mismatch (pu)", 13),
    ("Max dP (MW)", 12),
    ("P bus", 6),
    ("Max dQ (Mvar)", 13),
    ("Q bus", 6),
    ("Ctrl (pu)", 10),
    ("Max dV (pu)", 11),
    ("Max dAng (deg)", 14),
    ("Step", 6),
)
_WIDTHS = tuple(width for _, width in _COLUMNS)
_ITERATION_HEADER = "  ".join(title.rjust(width) for title, width in _COLUMNS)
_ITERATION_RULE = "-" * len(_ITERATION_HEADER)


@dataclass(slots=True)
class BusSolution:
    number: int
    name: str
    voltage_pu: float
    angle_degrees: float
    active_injection_mw: float
    reactive_injection_mvar: float


@dataclass(slots=True)
class PowerFlowResult:
    converged: bool
    iterations: int
    max_mismatch: float
    voltage: np.ndarray
    buses: list[BusSolution]
    failure_reason: str | None = None


@dataclass(slots=True)
class _Layout:
    """Ordering of Newton unknowns/equations for one iteration."""

    angle_buses: np.ndarray
    pq: np.ndarray
    angle_position: dict[int, int]
    pq_position: dict[int, int]
    active_svc: np.ndarray
    controls: list[TapControl]

    @property
    def network_size(self) -> int:
        return len(self.angle_buses) + len(self.pq)


@dataclass(slots=True)
class _Augmented:
    """Control unknowns the simultaneous method adds after the SVC/tap unknowns.

    The unknown vector is ``[generator Q, VLIM Q, shunt B]`` (pu).  Generator
    and VLIM unknowns are extra reactive injections at their bus; a shunt
    unknown is extra susceptance at 1 pu, injecting ``B·(scale·|V|)²``.
    """

    gen_buses: np.ndarray
    gen_setpoint: np.ndarray
    gen_lower: np.ndarray
    gen_upper: np.ndarray
    vlim_buses: np.ndarray
    vlim_lower: np.ndarray
    vlim_upper: np.ndarray
    shunts: list[SwitchedShunt]
    shunt_buses: np.ndarray
    shunt_scales: np.ndarray
    shunt_control_buses: np.ndarray
    shunt_control_scales: np.ndarray
    shunt_band_low: np.ndarray
    shunt_band_high: np.ndarray
    shunt_lower: np.ndarray
    shunt_upper: np.ndarray
    links: list[tuple[HVDCLink, int, int]]

    @property
    def gen_slice(self) -> slice:
        return slice(0, len(self.gen_buses))

    @property
    def vlim_slice(self) -> slice:
        start = len(self.gen_buses)
        return slice(start, start + len(self.vlim_buses))

    @property
    def shunt_slice(self) -> slice:
        start = len(self.gen_buses) + len(self.vlim_buses)
        return slice(start, start + len(self.shunts))

    @property
    def size(self) -> int:
        return len(self.gen_buses) + len(self.vlim_buses) + len(self.shunts)

    def clip(self, values: np.ndarray) -> np.ndarray:
        clipped = values.copy()
        clipped[self.gen_slice] = np.clip(clipped[self.gen_slice], self.gen_lower, self.gen_upper)
        clipped[self.shunt_slice] = np.clip(
            clipped[self.shunt_slice], self.shunt_lower, self.shunt_upper
        )
        return clipped


class NewtonRaphsonPowerFlow:
    """Solve an ANAREDE ``.dat`` case using sparse Newton-Raphson iterations.

    ``filename`` is an AMPL-style ANAREDE block export, such as the cases in
    the repository's ``tests/data/`` folder.  With ``print_iterations=True``
    the solve prints one table row per Newton iteration (mismatches, voltage
    and angle changes, and line-search step length) plus the outer control
    updates between passes.

    Besides bus voltages, the Newton state always includes SVC reactive
    outputs, LTC tap shifts (DOPC ``CTAP``) and phase-shifter angles (DOPC
    ``CPHS``).  ``control_method`` selects how the remaining controls are
    solved:

    * ``"simultaneous"`` (default): generator reactive limits (``QLIM``),
      voltage-limited load buses (``VLIM``), switched shunts (``STEP``), the
      LTC dead band and the LCC converter reactive power are equations of the
      same Newton system as the network.  Limits are complementarity
      conditions solved by semismooth Newton, so a generator can leave and
      re-enter its reactive limit within the iterations.  Switched shunts are
      solved as continuous susceptances, then rounded to whole bank steps and
      the case is solved once more.
    * ``"sequential"``: those controls are updated between full Newton solves
      (one shunt unit per pass, PV to PQ switching, LCC recomputed from the
      AC voltages) until no control changes.

    Set ``lu_decomposition=True`` to select an explicit sparse LU
    factorization for the Newton linear systems. The default retains the
    existing SciPy sparse direct-solve path.

    ``sparsity=True`` selects a minimum-degree, fill-reducing ordering for the
    sparse factorization. The default ``False`` retains SuperLU's COLAMD
    ordering. This option is independent of ``lu_decomposition``; sparse
    matrix storage is always used.

    Set ``linear_solver="clu"`` to use sparse CLU factorizer.
    CLU is optional and is loaded only when selected; its symbolic analysis is
    reused when successive Jacobians have the same sparse structure.
    """

    max_control_passes = 50
    tap_voltage_band = 0.01
    # Class-level defaults for solvers created with ``object.__new__`` (tests,
    # the optimization warm start): silent, with the default solver options.
    filename: Path | None = None
    print_iterations = False
    control_method = "simultaneous"
    lu_decomposition = False
    sparsity = False
    linear_solver: Literal["scipy", "clu"] = "scipy"
    _clu_factorizer: Any | None = None
    solver_name = "Newton-Raphson power flow"
    method_label = "Newton-Raphson"

    def __init__(
        self,
        filename: str | Path,
        *,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_control_passes: int = 50,
        control_method: str = "simultaneous",
        print_iterations: bool = False,
        lu_decomposition: bool = False,
        sparsity: bool = False,
        linear_solver: Literal["scipy", "clu"] = "scipy",
    ) -> None:
        if control_method not in _CONTROL_METHODS:
            raise ValueError(f"control_method must be one of {', '.join(_CONTROL_METHODS)}")
        if linear_solver not in ("scipy", "clu"):
            raise ValueError("linear_solver must be 'scipy' or 'clu'")
        if linear_solver == "clu" and (lu_decomposition or sparsity):
            raise ValueError("lu_decomposition and sparsity are SciPy-only options")
        self.control_method = control_method
        self.lu_decomposition = lu_decomposition
        self.sparsity = sparsity
        self.linear_solver = linear_solver
        self._clu_factorizer = None
        self.filename = Path(filename)
        self.print_iterations = bool(print_iterations)
        tables = parse_dat_file(self.filename)
        self.case = build_power_flow_case(tables)
        constants = {
            record.get("Mn", "").upper(): float(record.get("Val", 0.0))
            for record in tables.get("DCTE", [])
        }
        if tolerance is None:
            tolerance = constants.get("TEPA", 0.01) / self.case.base_mva
        if max_iterations is None:
            max_iterations = int(constants.get("ACIT", 100))
        self.tap_voltage_band = float(constants.get("TLTC", 0.01))
        if tolerance <= 0:
            raise ValueError("tolerance must be greater than zero")
        if max_iterations < 0:
            raise ValueError("max_iterations cannot be negative")
        if max_control_passes < 1:
            raise ValueError("max_control_passes must be at least one")
        self.tolerance = float(tolerance)
        self.max_iterations = int(max_iterations)
        self.max_control_passes = int(max_control_passes)
        self.reduction = None
        self.result: PowerFlowResult | None = None

    def solve(self) -> PowerFlowResult:
        """Run the AC power flow and retain the result on ``self.result``."""
        original_case = self.case
        reduction = reduce_low_impedance_branches(original_case)
        self.case = reduction.case
        self._pass_number = 0
        if self.print_iterations:
            self._print_header(len(original_case.buses))
        try:
            voltage, _, total_iterations, mismatch, failure_reason = self._solve_reduced_case()
        finally:
            self.case = original_case

        expanded_voltage = reduction.expand_voltage(voltage)
        reduction.write_back(original_case)
        self.reduction = reduction
        original_ybus = build_ybus(original_case)
        calculated = calculate_power(original_ybus, expanded_voltage)
        original_case.solved_voltage = expanded_voltage.copy()
        buses = [
            BusSolution(
                number=bus.number,
                name=bus.name,
                voltage_pu=float(abs(expanded_voltage[index])),
                angle_degrees=float(np.rad2deg(np.angle(expanded_voltage[index]))),
                active_injection_mw=float(calculated[index].real * original_case.base_mva),
                reactive_injection_mvar=float(calculated[index].imag * original_case.base_mva),
            )
            for index, bus in enumerate(original_case.buses)
        ]
        self.result = PowerFlowResult(
            converged=failure_reason is None and mismatch <= self.tolerance,
            iterations=total_iterations,
            max_mismatch=float(mismatch),
            voltage=expanded_voltage,
            buses=buses,
            failure_reason=failure_reason,
        )
        if self.print_iterations:
            self._print_summary(self.result)
        return self.result

    def _solve_reduced_case(
        self,
    ) -> tuple[np.ndarray, csc_matrix, int, float, str | None]:
        freeze_branch_matrix(self.case)
        ybus = build_ybus(self.case)
        self._assign_island_references(ybus)
        voltage = self.case.initial_voltage
        if self.case.options.get("NEWT", "L") != "L":
            return voltage, ybus, 0, float("inf"), "DOPC.NEWTON is disabled"
        if reconcile_tap_controls(self.case, voltage):
            ybus = build_ybus(self.case)
        voltage = self._warm_start_angles(ybus, voltage)
        if self.control_method == "simultaneous":
            return self._solve_simultaneous(voltage)
        failure_reason: str | None = None
        total_iterations = 0
        mismatch = float("inf")

        # Discrete controls (generator Q limits, voltage-limited buses, shunt
        # banks, LCC set-points) change the equations; re-solve after each pass.
        for pass_number in range(1, self.max_control_passes + 1):
            self._pass_number = pass_number
            voltage, ybus, iterations, mismatch, reason = self._solve_network(voltage)
            total_iterations += iterations
            if reason is not None or mismatch > self.tolerance:
                failure_reason = reason or f"{self.method_label} did not converge"
                break
            store_svc_outputs(self.case, self._svc_states)
            q_limits_changed = (
                self._enforce_generator_q_limits(ybus, voltage)
                if self.case.options.get("QLIM", "L") == "L"
                else False
            )
            voltage_limits_changed = (
                self._enforce_voltage_limits(voltage)
                if self.case.options.get("VLIM", "L") == "L"
                else False
            )
            shunts_changed = (
                adjust_controlled_shunts(self.case, voltage)
                if self.case.options.get("STEP", "L") == "L"
                else False
            )
            lcc_changed = update_lcc_from_dc_solution(self.case, voltage)
            taps_changed = (
                self._activate_tap_controls(voltage)
                if self.case.options.get("CTAP", "L") == "L"
                else False
            )
            if shunts_changed:
                ybus = build_ybus(self.case)
            if self.print_iterations:
                self._print_control_update(
                    pass_number,
                    {
                        "generator Q limits": q_limits_changed,
                        "voltage limits": voltage_limits_changed,
                        "switched shunts": shunts_changed,
                        "LCC set-points": lcc_changed,
                        "LTC regulation": taps_changed,
                    },
                )
            if (
                not q_limits_changed
                and not voltage_limits_changed
                and not shunts_changed
                and not lcc_changed
                and not taps_changed
            ):
                break
        else:
            failure_reason = "Generator, voltage, shunt, LTC, or LCC controls did not stabilize"

        return voltage, ybus, total_iterations, mismatch, failure_reason

    def _regulating_controls(self, svc_states: list[SVCState]) -> list[TapControl]:
        """Tap/phase controls whose regulation equation is solvable now."""
        svc_controlled = {state.control_bus_index for state in svc_states if state.active}
        indices = self.case.bus_index
        controls = []
        for control in self.case.tap_controls:
            target = indices.get(control.controlled_bus)
            control.enabled = target is not None and (
                control.mode == "P"
                or (self.case.buses[target].bus_type == BusType.PQ and target not in svc_controlled)
            )
            if (
                control.enabled
                and control.regulating
                and not control.limited
                and not control.conflicting
            ):
                controls.append(control)
        return controls

    def _activate_tap_controls(self, voltage: np.ndarray) -> bool:
        """Start regulating LTCs whose controlled voltage left its band."""
        indices = self.case.bus_index
        changed = False
        for control in self.case.tap_controls:
            if control.mode != "V" or control.regulating or control.limited or control.conflicting:
                continue
            target = indices.get(control.controlled_bus)
            if target is None or self.case.buses[target].bus_type != BusType.PQ:
                continue
            magnitude = abs(voltage[target]) * control.controlled_scale
            if magnitude > control.setpoint + self.tap_voltage_band:
                control.target = control.setpoint + self.tap_voltage_band
            elif magnitude < control.setpoint - self.tap_voltage_band:
                control.target = control.setpoint - self.tap_voltage_band
            else:
                continue
            control.regulating = True
            changed = True
        return changed

    def _layout(self, svc_states: list[SVCState], controls: list[TapControl]) -> _Layout:
        # The simultaneous method also solves the voltage of generator buses.
        pq = (
            self._magnitude_buses
            if getattr(self, "_magnitude_buses", None) is not None
            else self.case.pq_indices
        )
        angle_buses = np.concatenate((self.case.pv_indices, self.case.pq_indices))
        return _Layout(
            angle_buses=angle_buses,
            pq=pq,
            angle_position={int(bus): i for i, bus in enumerate(angle_buses)},
            pq_position={int(bus): i for i, bus in enumerate(pq)},
            active_svc=np.array(
                [i for i, state in enumerate(svc_states) if state.active], dtype=np.int64
            ),
            controls=[
                control for control in controls if not control.limited and not control.conflicting
            ],
        )

    def _solve_linear_system(self, jacobian: csc_matrix, residual: np.ndarray) -> np.ndarray:
        if self.linear_solver == "clu":
            return _clu_sparse_solve(jacobian, residual, self)
        return _sparse_solve(jacobian, residual, self.lu_decomposition, self.sparsity)

    def _solve_network(
        self, initial_voltage: np.ndarray
    ) -> tuple[np.ndarray, csc_matrix, int, float, str | None]:
        voltage = initial_voltage.copy()
        specified = self.case.specified_power
        svc_states = active_svc_states(self.case)
        for state in svc_states:
            specified.imag[state.bus_index] -= state.q_pu
        controls = self._regulating_controls(svc_states)
        self._indices = self.case.bus_index
        ybus = build_ybus(self.case)
        mismatch = float("inf")
        # Voltage change and step length of the update that led to the current row.
        last_step: tuple[float, float, float] | None = None

        for iteration in range(self.max_iterations + 1):
            layout = self._layout(svc_states, controls)
            refresh_svc_limits(svc_states, np.abs(voltage))
            self._svc_states = svc_states
            residual = self._residual(ybus, voltage, specified, svc_states, layout)
            mismatch = _max_abs(residual)
            if self.print_iterations:
                self._print_iteration(iteration, residual, layout, last_step)
            if mismatch <= self.tolerance or iteration == self.max_iterations:
                return voltage, ybus, iteration, mismatch, None

            jacobian = self._build_jacobian(ybus, voltage, svc_states, layout)
            try:
                step = self._solve_linear_system(jacobian, residual)
                # Drop controls whose regulation equation is (nearly) singular:
                # a tap/phase step far beyond any physical range shows that the
                # control has no independent effect on its target.
                for _ in range(5):
                    suspicious = _conflicting_controls(layout, step)
                    if not suspicious:
                        break
                    for control in suspicious:
                        control.conflicting = True
                    layout = self._layout(svc_states, controls)
                    residual = self._residual(ybus, voltage, specified, svc_states, layout)
                    jacobian = self._build_jacobian(ybus, voltage, svc_states, layout)
                    step = self._solve_linear_system(jacobian, residual)
            except (MatrixRankWarning, FloatingPointError, RuntimeError, ValueError) as exc:
                return voltage, ybus, iteration, mismatch, f"Sparse Newton solve failed: {exc}"
            if not np.all(np.isfinite(step)):
                return (
                    voltage,
                    ybus,
                    iteration,
                    mismatch,
                    "Sparse Newton solve returned a non-finite step",
                )

            previous_voltage = voltage
            voltage, svc_states, ybus = self._line_search(
                ybus, voltage, specified, svc_states, layout, step, residual
            )
            if self.print_iterations:
                last_step = (
                    _max_abs(np.abs(voltage) - np.abs(previous_voltage)),
                    float(np.rad2deg(_max_abs(np.angle(voltage * np.conj(previous_voltage))))),
                    self._step_scale,
                )
            magnitude = np.abs(voltage)
            if np.any(magnitude < 0.4) or np.any(magnitude > 2.0):
                return (
                    voltage,
                    ybus,
                    iteration + 1,
                    mismatch,
                    "Voltage magnitude left [0.4, 2.0] pu",
                )

        return voltage, ybus, self.max_iterations, mismatch, None

    def _residual(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        specified: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
    ) -> np.ndarray:
        calculated = calculate_power(ybus, voltage)
        svc_q = np.zeros(len(self.case.buses), dtype=float)
        for state in svc_states:
            if state.active:
                svc_q[state.bus_index] += state.q_pu
        return np.concatenate(
            (
                (calculated.real - specified.real)[layout.angle_buses],
                (calculated.imag - specified.imag - svc_q)[layout.pq],
                np.array(
                    [self._svc_residual(svc_states[i], voltage) for i in layout.active_svc],
                    dtype=float,
                ),
                np.array(
                    [self._control_residual(control, voltage) for control in layout.controls],
                    dtype=float,
                ),
            )
        )

    def _control_residual(self, control: TapControl, voltage: np.ndarray) -> float:
        indices = self._indices
        if control.mode == "V":
            target = indices[control.controlled_bus]
            law = self._tap_law(control)
            if law is not None:
                return sorted(self._tap_law_pieces(control, law, voltage))[1]
            return abs(voltage[target]) * control.controlled_scale - control.target
        branch = control.branches[0]
        sensitivity = branch_sensitivity(
            branch, voltage[indices[branch.from_bus]], voltage[indices[branch.to_bus]]
        )
        power = sensitivity.from_power if control.measure_from else sensitivity.to_power
        return float(power.real) - control.target

    @staticmethod
    def _svc_residual(state: SVCState, voltage: np.ndarray) -> float:
        control_voltage = abs(voltage[state.control_bus_index]) * state.control_voltage_scale
        device_voltage = max(abs(voltage[state.bus_index]) * state.device_voltage_scale, 1e-12)
        if state.limit_state < 0:
            return state.q_pu - state.q_min_pu * device_voltage**2
        if state.limit_state > 0:
            return state.q_pu - state.q_max_pu * device_voltage**2
        if state.mode == "I":
            return (
                control_voltage
                - state.reference_voltage
                + state.q_pu * state.slope / device_voltage
            )
        return control_voltage - state.reference_voltage + state.q_pu * state.slope

    def _build_jacobian(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
    ) -> csc_matrix:
        base = _network_jacobian(ybus, voltage, layout.angle_buses, layout.pq)
        extra_count = len(layout.active_svc) + len(layout.controls)
        if extra_count == 0:
            return base

        magnitude = np.abs(voltage)
        angle_count = len(layout.angle_buses)
        network_size = layout.network_size
        coupling = _Triplets()  # d(network equations) / d(extra unknowns)
        sensitivity = _Triplets()  # d(extra equations) / d(theta, V)
        extra = _Triplets()  # d(extra equations) / d(extra unknowns)

        for column, state_index in enumerate(layout.active_svc):
            state = svc_states[int(state_index)]
            q_row = layout.pq_position.get(state.bus_index)
            if q_row is not None:
                coupling.add(angle_count + q_row, column, -1.0)
            device_column = layout.pq_position.get(state.bus_index)
            device_voltage = magnitude[state.bus_index] * state.device_voltage_scale
            if state.limit_state:
                bound = state.q_min_pu if state.limit_state < 0 else state.q_max_pu
                if device_column is not None:
                    sensitivity.add(
                        column,
                        angle_count + device_column,
                        -2.0 * bound * device_voltage * state.device_voltage_scale,
                    )
                extra.add(column, column, 1.0)
                continue
            control_column = layout.pq_position.get(state.control_bus_index)
            if control_column is not None:
                sensitivity.add(column, angle_count + control_column, state.control_voltage_scale)
            if state.mode == "I":
                if device_column is not None:
                    sensitivity.add(
                        column,
                        angle_count + device_column,
                        -state.q_pu
                        * state.slope
                        / max(device_voltage**2, 1e-12)
                        * state.device_voltage_scale,
                    )
                extra.add(column, column, state.slope / max(device_voltage, 1e-12))
            else:
                extra.add(column, column, state.slope)

        indices = self._indices
        svc_count = len(layout.active_svc)
        for offset, control in enumerate(layout.controls):
            column = svc_count + offset
            for branch in control.branches:
                start = indices[branch.from_bus]
                end = indices[branch.to_bus]
                derivative = branch_sensitivity(branch, voltage[start], voltage[end])
                if control.mode == "V":
                    terms = ((start, derivative.from_tap), (end, derivative.to_tap))
                else:
                    terms = ((start, derivative.from_phase), (end, derivative.to_phase))
                for bus, value in terms:
                    angle_row = layout.angle_position.get(bus)
                    if angle_row is not None:
                        coupling.add(angle_row, column, value.real)
                    q_row = layout.pq_position.get(bus)
                    if q_row is not None:
                        coupling.add(angle_count + q_row, column, value.imag)
            if control.mode == "V":
                target = indices[control.controlled_bus]
                law = self._tap_law(control)
                if law is None:
                    sensitivity.add(
                        column,
                        angle_count + layout.pq_position[target],
                        control.controlled_scale,
                    )
                    continue
                pieces = self._tap_law_pieces(control, law, voltage)
                if _median_index(pieces) < 2:
                    extra.add(column, column, 1.0)  # held at a tap limit
                    continue
                extra.add(column, column, _TAP_DEADBAND_GAIN)
                sign, _, low, high = law
                if not low <= magnitude[target] * control.controlled_scale <= high:
                    sensitivity.add(
                        column,
                        angle_count + layout.pq_position[target],
                        sign * control.controlled_scale,
                    )
                continue
            branch = control.branches[0]
            start = indices[branch.from_bus]
            end = indices[branch.to_bus]
            derivative = branch_sensitivity(branch, voltage[start], voltage[end])
            if control.measure_from:
                theta, magnitude_terms = derivative.from_theta, derivative.from_magnitude
                phase_term = derivative.from_phase
            else:
                theta, magnitude_terms = derivative.to_theta, derivative.to_magnitude
                phase_term = derivative.to_phase
            for bus, theta_value, magnitude_value in (
                (start, theta[0], magnitude_terms[0]),
                (end, theta[1], magnitude_terms[1]),
            ):
                angle_column = layout.angle_position.get(bus)
                if angle_column is not None:
                    sensitivity.add(column, angle_column, theta_value.real)
                magnitude_column = layout.pq_position.get(bus)
                if magnitude_column is not None:
                    sensitivity.add(column, angle_count + magnitude_column, magnitude_value.real)
            extra.add(column, column, phase_term.real)

        return vstack(
            (
                hstack((base, coupling.matrix(network_size, extra_count)), format="csc"),
                hstack(
                    (
                        sensitivity.matrix(extra_count, network_size),
                        extra.matrix(extra_count, extra_count),
                    ),
                    format="csc",
                ),
            ),
            format="csc",
        )

    def _line_search(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        specified: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
        step: np.ndarray,
        residual: np.ndarray,
        augmented: tuple[_Augmented, np.ndarray] | None = None,
        rows: np.ndarray | None = None,
        monotone: bool = True,
    ) -> tuple[np.ndarray, list[SVCState], csc_matrix]:
        """Damped Newton update.

        With ``augmented`` (simultaneous method) the accepted control unknowns
        are left in ``self._accepted_values``.  With ``rows`` (decoupled
        half-steps) a trial is judged only on those equations.  With
        ``monotone=False`` the first trial inside the voltage bounds and control
        step limits is accepted.
        """
        angle_count = len(layout.angle_buses)
        magnitude_count = len(layout.pq)
        svc_count = len(layout.active_svc)
        control_offset = angle_count + magnitude_count + svc_count
        control_end = control_offset + len(layout.controls)
        control_step = step[control_offset:control_end]
        # Limit large control moves by shortening the whole Newton step, which
        # keeps the search direction (clipping single components would not).
        scale = 1.0
        for offset, control in enumerate(layout.controls):
            limit = _MAX_TAP_STEP if control.mode == "V" else _MAX_PHASE_STEP
            if abs(control_step[offset]) > limit:
                scale = min(scale, limit / abs(control_step[offset]))
        start_shifts = [control.shift for control in layout.controls]
        judged = residual if rows is None else residual[rows]
        current_max = _max_abs(judged)
        current_norm = float(np.linalg.norm(judged))
        angle = np.angle(voltage)
        magnitude = np.abs(voltage)
        for _ in range(16):
            trial_magnitude = magnitude.copy()
            trial_magnitude[layout.pq] += scale * step[angle_count : angle_count + magnitude_count]
            if np.any(trial_magnitude[layout.pq] < 0.4) or np.any(trial_magnitude[layout.pq] > 2.0):
                scale *= 0.5
                continue
            trial_angle = angle.copy()
            trial_angle[layout.angle_buses] += scale * step[:angle_count]
            trial_states = [replace(state) for state in svc_states]
            for column, state_index in enumerate(layout.active_svc):
                trial_states[int(state_index)].q_pu += (
                    scale * step[angle_count + magnitude_count + column]
                )
            refresh_svc_limits(trial_states, trial_magnitude)
            for offset, control in enumerate(layout.controls):
                shift = start_shifts[offset] + scale * control_step[offset]
                if self._tap_law(control) is None:
                    shift = np.clip(shift, control.shift_min, control.shift_max)
                control.apply(shift)
            trial_ybus = build_ybus(self.case) if layout.controls else ybus
            trial_voltage = trial_magnitude * np.exp(1j * trial_angle)
            if augmented is None:
                trial_residual = self._residual(
                    trial_ybus, trial_voltage, specified, trial_states, layout
                )
            else:
                aug, values = augmented
                # No clipping: the median equations pull unknowns back to their
                # bounds, while clipping would bend the Newton direction.
                trial_values = values + scale * step[control_end:]
                trial_residual = self._augmented_residual(
                    trial_ybus, trial_voltage, specified, trial_states, layout, aug, trial_values
                )
            if rows is not None:
                trial_residual = trial_residual[rows]
            if (
                not monotone
                or _max_abs(trial_residual) < current_max
                or float(np.linalg.norm(trial_residual)) < current_norm
                or scale <= 1 / 32768
            ):
                self._track_control_limits(layout.controls, start_shifts, control_step)
                self._step_scale = scale
                if augmented is not None:
                    self._accepted_values = trial_values
                return trial_voltage, trial_states, trial_ybus
            scale *= 0.5
        for offset, control in enumerate(layout.controls):
            control.apply(start_shifts[offset])
        self._step_scale = 0.0
        if augmented is not None:
            self._accepted_values = augmented[1]
        return voltage, svc_states, ybus

    # ------------------------------------------------------------------
    # Simultaneous control method
    # ------------------------------------------------------------------

    def _solve_simultaneous(
        self, voltage: np.ndarray
    ) -> tuple[np.ndarray, csc_matrix, int, float, str | None]:
        """Solve the network and every control as one Newton system.

        Pass 1 solves switched shunts as continuous susceptances.  If any
        shunt moved, the shunts are rounded to whole bank units and pass 2
        re-solves with them fixed; every other control stays in the system.
        """
        ybus = build_ybus(self.case)
        augmented = self._build_augmented(include_shunts=self.case.options.get("STEP", "L") == "L")
        values = augmented.clip(np.zeros(augmented.size))
        self._magnitude_buses = np.union1d(self.case.pq_indices, augmented.gen_buses).astype(
            np.int64
        )
        total_iterations = 0
        self._tap_laws = None  # set once by the first pass, kept for the second
        try:
            for pass_number in (1, 2):
                self._pass_number = pass_number
                voltage, ybus, iterations, mismatch, reason, values = self._solve_network_augmented(
                    voltage, augmented, values
                )
                total_iterations += iterations
                if reason is not None or mismatch > self.tolerance:
                    return (
                        voltage,
                        ybus,
                        total_iterations,
                        mismatch,
                        reason or f"{self.method_label} did not converge",
                    )
                shunt_values = values[augmented.shunt_slice]
                if pass_number == 2 or not np.any(np.abs(shunt_values) > 1e-6):
                    if self.print_iterations:
                        print(f"  -- pass {pass_number} converged; controls are stable", flush=True)
                    break
                moved = self._round_shunts(augmented, values)
                if self.print_iterations:
                    print(
                        "  -- pass 1 converged; rounding switched shunts to whole bank units "
                        f"({moved} changed) and re-solving",
                        flush=True,
                    )
                augmented, values = self._without_shunts(augmented, values)
                ybus = build_ybus(self.case)
            self._store_augmented(augmented, values, voltage)
            store_svc_outputs(self.case, self._svc_states)
        finally:
            self._magnitude_buses = None
            self._tap_laws = None
        return voltage, ybus, total_iterations, mismatch, None

    def _build_augmented(self, *, include_shunts: bool) -> _Augmented:
        case = self.case
        base = case.base_mva
        indices = case.bus_index
        gen_buses, gen_setpoint, gen_lower, gen_upper = [], [], [], []
        if case.options.get("QLIM", "L") == "L":
            for index, bus in enumerate(case.buses):
                if bus.bus_type != BusType.PV or bus.q_min_mvar is None or bus.q_max_mvar is None:
                    continue
                if bus.q_max_mvar <= bus.q_min_mvar + 1e-9:
                    continue
                gen_buses.append(index)
                gen_setpoint.append(bus.voltage_pu)
                gen_lower.append((bus.q_min_mvar - bus.q_generation_mvar) / base)
                gen_upper.append((bus.q_max_mvar - bus.q_generation_mvar) / base)
        vlim_buses, vlim_lower, vlim_upper = [], [], []
        if case.options.get("VLIM", "L") == "L":
            for index, bus in enumerate(case.buses):
                if bus.bus_type == BusType.PQ and bus.voltage_limit_control:
                    vlim_buses.append(index)
                    vlim_lower.append(bus.voltage_min_pu)
                    vlim_upper.append(bus.voltage_max_pu)
        shunts = []
        if include_shunts:
            shunts = [
                shunt
                for shunt in case.switched_shunts
                if not shunt.fixed
                and shunt.stages
                and shunt.minimum_voltage_pu < shunt.maximum_voltage_pu
                and shunt.bus in indices
                and shunt.controlled_bus in indices
            ]
        links = [
            (link, indices[link.rectifier_bus], indices[link.inverter_bus])
            for link in case.hvdc_links
            if link.rectifier_bus in indices and link.inverter_bus in indices
        ]

        def array(values: list[float], kind: type = float) -> np.ndarray:
            return np.asarray(values, dtype=np.int64 if kind is int else float)

        return _Augmented(
            gen_buses=array(gen_buses, int),
            gen_setpoint=array(gen_setpoint),
            gen_lower=array(gen_lower),
            gen_upper=array(gen_upper),
            vlim_buses=array(vlim_buses, int),
            vlim_lower=array(vlim_lower),
            vlim_upper=array(vlim_upper),
            shunts=shunts,
            shunt_buses=array([indices[s.bus] for s in shunts], int),
            shunt_scales=array([s.voltage_scale for s in shunts]),
            shunt_control_buses=array([indices[s.controlled_bus] for s in shunts], int),
            shunt_control_scales=array([s.control_voltage_scale for s in shunts]),
            shunt_band_low=array([s.minimum_voltage_pu for s in shunts]),
            shunt_band_high=array([s.maximum_voltage_pu for s in shunts]),
            shunt_lower=array([(s.minimum_q_mvar - s.current_q_mvar) / base for s in shunts]),
            shunt_upper=array([(s.maximum_q_mvar - s.current_q_mvar) / base for s in shunts]),
            links=links,
        )

    def _simultaneous_controls(self, svc_states: list[SVCState]) -> list[TapControl]:
        """Tap/phase controls of the simultaneous system.

        Every LTC whose controlled voltage is solved for takes part (its dead
        band decides whether it moves); phase shifters regulate as usual.
        """
        svc_controlled = {state.control_bus_index for state in svc_states if state.active}
        solved = set(self._magnitude_buses.tolist())
        indices = self.case.bus_index
        controls = []
        for control in self.case.tap_controls:
            target = indices.get(control.controlled_bus)
            if control.mode == "P":
                control.enabled = target is not None
                usable = control.regulating and not control.limited and not control.conflicting
            else:
                control.enabled = (
                    target is not None and target in solved and target not in svc_controlled
                )
                control.limited = control.conflicting = False
                usable = True
            if control.enabled and usable:
                controls.append(control)
        return controls

    def _tap_law(self, control: TapControl) -> tuple[float, float, float, float] | None:
        laws = getattr(self, "_tap_laws", None)
        return laws.get(id(control)) if laws else None

    def _tap_law_pieces(
        self,
        control: TapControl,
        law: tuple[float, float, float, float],
        voltage: np.ndarray,
    ) -> tuple[float, float, float]:
        """Candidates of ``mid(s - s_min, s - s_max, sign·dz(V) + k·(s - s0)) = 0``.

        ``dz`` is the distance of the controlled voltage outside its dead band
        and ``sign`` the sign of dV/ds, so the law always opposes the error.
        """
        sign, start, low, high = law
        controlled = abs(voltage[self._indices[control.controlled_bus]]) * control.controlled_scale
        outside = controlled - high if controlled > high else min(controlled - low, 0.0)
        return (
            control.shift - control.shift_min,
            control.shift - control.shift_max,
            sign * outside + _TAP_DEADBAND_GAIN * (control.shift - start),
        )

    def _tap_orientation(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        svc_states: list[SVCState],
        controls: list[TapControl],
    ) -> dict[int, tuple[float, float, float, float]]:
        """Dead-band law of each LTC: sign of dV/ds from a network sensitivity."""
        ltcs = [control for control in controls if control.mode == "V"]
        if not ltcs:
            return {}
        layout = self._layout(svc_states, [])
        angle_count = len(layout.angle_buses)
        size = layout.network_size
        indices = self._indices
        signs = np.ones(len(ltcs))
        try:
            factor = splu(
                _network_jacobian(ybus, voltage, layout.angle_buses, layout.pq),
                permc_spec=_sparse_ordering(self.sparsity),
            )
        except RuntimeError:
            factor = None
        for first in range(0, len(ltcs) if factor is not None else 0, 64):
            chunk = ltcs[first : first + 64]
            couplings = np.zeros((size, len(chunk)))
            for column, control in enumerate(chunk):
                for branch in control.branches:
                    start = indices[branch.from_bus]
                    end = indices[branch.to_bus]
                    derivative = branch_sensitivity(branch, voltage[start], voltage[end])
                    for bus, value in ((start, derivative.from_tap), (end, derivative.to_tap)):
                        row = layout.angle_position.get(bus)
                        if row is not None:
                            couplings[row, column] += value.real
                        row = layout.pq_position.get(bus)
                        if row is not None:
                            couplings[angle_count + row, column] += value.imag
            response = factor.solve(-couplings)
            for column, control in enumerate(chunk):
                target = layout.pq_position[indices[control.controlled_bus]]
                if response[angle_count + target, column] < -1e-9:
                    signs[first + column] = -1.0
        band = self.tap_voltage_band
        return {
            id(control): (sign, control.shift, control.setpoint - band, control.setpoint + band)
            for sign, control in zip(signs, ltcs)
        }

    def _augmented_pieces(
        self, augmented: _Augmented, values: np.ndarray, magnitude: np.ndarray
    ) -> np.ndarray:
        """Three candidates per control; each equation is their median."""
        gen = values[augmented.gen_slice]
        vlim = values[augmented.vlim_slice]
        shunt = values[augmented.shunt_slice]
        gen_voltage = magnitude[augmented.gen_buses]
        vlim_voltage = magnitude[augmented.vlim_buses]
        controlled = magnitude[augmented.shunt_control_buses] * augmented.shunt_control_scales
        return np.hstack(
            (
                np.vstack(
                    (
                        gen - augmented.gen_lower,
                        gen - augmented.gen_upper,
                        gen_voltage - augmented.gen_setpoint,
                    )
                ),
                # Voltage bounded, extra reactive injection complementary to it.
                np.vstack(
                    (vlim_voltage - augmented.vlim_lower, vlim_voltage - augmented.vlim_upper, vlim)
                ),
                np.vstack(
                    (
                        shunt - augmented.shunt_lower,
                        shunt - augmented.shunt_upper,
                        _dead_zone(controlled, augmented.shunt_band_low, augmented.shunt_band_high)
                        + _SHUNT_DEADBAND_GAIN * shunt,
                    )
                ),
            )
        )

    def _lcc_reactive_load(self, augmented: _Augmented, magnitude: np.ndarray) -> np.ndarray:
        """Change of the LCC converter reactive load (pu) at the present voltages."""
        load = np.zeros(len(self.case.buses))
        for link, rectifier, inverter in augmented.links:
            q_rectifier, _, _ = converter_operating_point(
                link, True, magnitude[rectifier] * link.rectifier_voltage_scale
            )
            q_inverter, _, _ = converter_operating_point(
                link, False, magnitude[inverter] * link.inverter_voltage_scale
            )
            load[rectifier] += q_rectifier - link.rectifier_reactive_mvar
            load[inverter] += q_inverter - link.inverter_reactive_mvar
        return load / self.case.base_mva

    def _augmented_residual(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        specified: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
        augmented: _Augmented,
        values: np.ndarray,
    ) -> np.ndarray:
        magnitude = np.abs(voltage)
        extra_q = np.zeros(len(self.case.buses))
        np.add.at(extra_q, augmented.gen_buses, values[augmented.gen_slice])
        np.add.at(extra_q, augmented.vlim_buses, values[augmented.vlim_slice])
        np.add.at(
            extra_q,
            augmented.shunt_buses,
            values[augmented.shunt_slice]
            * (magnitude[augmented.shunt_buses] * augmented.shunt_scales) ** 2,
        )
        extra_q -= self._lcc_reactive_load(augmented, magnitude)
        network = self._residual(ybus, voltage, specified + 1j * extra_q, svc_states, layout)
        pieces = self._augmented_pieces(augmented, values, magnitude)
        return np.concatenate((network, np.median(pieces, axis=0)))

    def _augmented_jacobian(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
        augmented: _Augmented,
        values: np.ndarray,
    ) -> csc_matrix:
        core = self._build_jacobian(ybus, voltage, svc_states, layout)
        size = core.shape[0]
        count = augmented.size
        angle_count = len(layout.angle_buses)
        magnitude = np.abs(voltage)
        base = self.case.base_mva

        def magnitude_column(bus: int) -> int | None:
            position = layout.pq_position.get(int(bus))
            return None if position is None else angle_count + position

        correction = _Triplets()  # extra d(network)/d(theta, V)
        coupling = _Triplets()  # d(network)/d(control unknowns)
        rows = _Triplets()  # d(control equations)/d(theta, V)
        diagonal = _Triplets()  # d(control equations)/d(control unknowns)
        pieces = self._augmented_pieces(augmented, values, magnitude)
        chosen = np.argsort(pieces, axis=0, kind="stable")[1]

        for k, bus in enumerate(augmented.gen_buses):
            column = magnitude_column(bus)
            coupling.add(column, k, -1.0)
            if chosen[k] < 2:
                diagonal.add(k, k, 1.0)
            else:
                rows.add(k, column, 1.0)
        offset = augmented.vlim_slice.start
        for j, bus in enumerate(augmented.vlim_buses):
            k = offset + j
            column = magnitude_column(bus)
            coupling.add(column, k, -1.0)
            if chosen[k] < 2:
                rows.add(k, column, 1.0)
            else:
                diagonal.add(k, k, 1.0)
        offset = augmented.shunt_slice.start
        for j, bus in enumerate(augmented.shunt_buses):
            k = offset + j
            column = magnitude_column(bus)
            scale = augmented.shunt_scales[j]
            terminal = magnitude[bus] * scale
            if column is not None:
                coupling.add(column, k, -(terminal**2))
                correction.add(column, column, -2.0 * values[k] * scale * terminal)
            if chosen[k] < 2:
                diagonal.add(k, k, 1.0)
                continue
            diagonal.add(k, k, _SHUNT_DEADBAND_GAIN)
            control_bus = augmented.shunt_control_buses[j]
            control_scale = augmented.shunt_control_scales[j]
            controlled = magnitude[control_bus] * control_scale
            control_column = magnitude_column(control_bus)
            outside = not (
                augmented.shunt_band_low[j] <= controlled <= augmented.shunt_band_high[j]
            )
            if outside and control_column is not None:
                rows.add(k, control_column, control_scale)
        for link, rectifier, inverter in augmented.links:
            for bus, is_rectifier, scale in (
                (rectifier, True, link.rectifier_voltage_scale),
                (inverter, False, link.inverter_voltage_scale),
            ):
                column = magnitude_column(bus)
                if column is None:
                    continue
                terminal = magnitude[bus] * scale
                upper, _, _ = converter_operating_point(
                    link, is_rectifier, terminal + _LCC_VOLTAGE_STEP
                )
                lower, _, _ = converter_operating_point(
                    link, is_rectifier, terminal - _LCC_VOLTAGE_STEP
                )
                slope = (upper - lower) / (2.0 * _LCC_VOLTAGE_STEP)
                correction.add(column, column, slope * scale / base)

        return bmat(
            [
                [core + correction.matrix(size, size), coupling.matrix(size, count)],
                [rows.matrix(count, size), diagonal.matrix(count, count)],
            ],
            format="csc",
        )

    def _solve_network_augmented(
        self, initial_voltage: np.ndarray, augmented: _Augmented, values: np.ndarray
    ) -> tuple[np.ndarray, csc_matrix, int, float, str | None, np.ndarray]:
        voltage = initial_voltage.copy()
        specified = self.case.specified_power
        svc_states = active_svc_states(self.case)
        for state in svc_states:
            specified.imag[state.bus_index] -= state.q_pu
        self._indices = self.case.bus_index
        controls = self._simultaneous_controls(svc_states)
        ybus = build_ybus(self.case)
        if self._tap_laws is None:
            self._tap_laws = self._tap_orientation(ybus, voltage, svc_states, controls)
        mismatch = float("inf")
        last_step: tuple[float, float, float] | None = None

        def evaluate(layout: _Layout) -> tuple[np.ndarray, csc_matrix]:
            residual = self._augmented_residual(
                ybus, voltage, specified, svc_states, layout, augmented, values
            )
            return residual, self._augmented_jacobian(
                ybus, voltage, svc_states, layout, augmented, values
            )

        for iteration in range(self.max_iterations + 1):
            layout = self._layout(svc_states, controls)
            refresh_svc_limits(svc_states, np.abs(voltage))
            self._svc_states = svc_states
            residual = self._augmented_residual(
                ybus, voltage, specified, svc_states, layout, augmented, values
            )
            mismatch = _max_abs(residual)
            if self.print_iterations:
                self._print_iteration(iteration, residual, layout, last_step)
            if mismatch <= self.tolerance or iteration == self.max_iterations:
                return voltage, ybus, iteration, mismatch, None, values

            try:
                residual, jacobian = evaluate(layout)
                step = self._solve_linear_system(jacobian, residual)
                # Phase shifters keep their singularity screening; LTC laws
                # are regularized by their dead-band slope instead.
                for _ in range(5):
                    suspicious = [
                        control
                        for control in _conflicting_controls(layout, step)
                        if control.mode == "P"
                    ]
                    if not suspicious:
                        break
                    for control in suspicious:
                        control.conflicting = True
                    controls = [control for control in controls if not control.conflicting]
                    layout = self._layout(svc_states, controls)
                    residual, jacobian = evaluate(layout)
                    step = self._solve_linear_system(jacobian, residual)
            except (MatrixRankWarning, FloatingPointError, RuntimeError, ValueError) as exc:
                return (
                    voltage,
                    ybus,
                    iteration,
                    mismatch,
                    f"Sparse Newton solve failed: {exc}",
                    values,
                )
            if not np.all(np.isfinite(step)):
                return (
                    voltage,
                    ybus,
                    iteration,
                    mismatch,
                    "Sparse Newton solve returned a non-finite step",
                    values,
                )

            previous_voltage = voltage
            voltage, svc_states, ybus = self._line_search(
                ybus,
                voltage,
                specified,
                svc_states,
                layout,
                step,
                residual,
                augmented=(augmented, values),
            )
            values = self._accepted_values
            if self.print_iterations:
                last_step = (
                    _max_abs(np.abs(voltage) - np.abs(previous_voltage)),
                    float(np.rad2deg(_max_abs(np.angle(voltage * np.conj(previous_voltage))))),
                    self._step_scale,
                )
            magnitude = np.abs(voltage)
            if np.any(magnitude < 0.4) or np.any(magnitude > 2.0):
                return (
                    voltage,
                    ybus,
                    iteration + 1,
                    mismatch,
                    "Voltage magnitude left [0.4, 2.0] pu",
                    values,
                )
        return voltage, ybus, self.max_iterations, mismatch, None, values

    def _round_shunts(self, augmented: _Augmented, values: np.ndarray) -> int:
        """Round the continuous shunt solution to whole bank units."""
        base = self.case.base_mva
        moved = 0
        for j, shunt in enumerate(augmented.shunts):
            requested = shunt.current_q_mvar + values[augmented.shunt_slice.start + j] * base
            change = snap_to_bank_steps(shunt, requested)
            if abs(change) > 1e-9:
                bus = self.case.buses[augmented.shunt_buses[j]]
                bus.shunt_susceptance_pu += change * shunt.voltage_scale**2 / base
                moved += 1
        return moved

    @staticmethod
    def _without_shunts(augmented: _Augmented, values: np.ndarray) -> tuple[_Augmented, np.ndarray]:
        empty_int = np.empty(0, dtype=np.int64)
        empty = np.empty(0)
        reduced = replace(
            augmented,
            shunts=[],
            shunt_buses=empty_int,
            shunt_scales=empty,
            shunt_control_buses=empty_int,
            shunt_control_scales=empty,
            shunt_band_low=empty,
            shunt_band_high=empty,
            shunt_lower=empty,
            shunt_upper=empty,
        )
        return reduced, values[: augmented.shunt_slice.start].copy()

    def _store_augmented(
        self, augmented: _Augmented, values: np.ndarray, voltage: np.ndarray
    ) -> None:
        """Write the solved control states back to the case."""
        case = self.case
        base = case.base_mva
        magnitude = np.abs(voltage)
        chosen = np.argsort(
            self._augmented_pieces(augmented, values, magnitude), axis=0, kind="stable"
        )[1]
        at_q_limit = 0
        for k, index in enumerate(augmented.gen_buses):
            bus = case.buses[index]
            bus.q_generation_mvar += values[k] * base
            if chosen[k] < 2:
                bus.bus_type = BusType.PQ
                at_q_limit += 1
        at_voltage_limit = 0
        offset = augmented.vlim_slice.start
        for j, index in enumerate(augmented.vlim_buses):
            k = offset + j
            bus = case.buses[index]
            bus.q_generation_mvar += values[k] * base
            if chosen[k] < 2:
                bus.bus_type = BusType.PV
                bus.voltage_pu = float(magnitude[index])
                at_voltage_limit += 1
        regulating = limited = 0
        for control in case.tap_controls:
            law = self._tap_law(control)
            if law is None:
                continue
            pieces = self._tap_law_pieces(control, law, voltage)
            control.limited = _median_index(pieces) < 2
            _, _, low, high = law
            controlled = magnitude[self._indices[control.controlled_bus]] * control.controlled_scale
            control.regulating = not control.limited and not low <= controlled <= high
            control.target = min(max(controlled, low), high)
            regulating += control.regulating
            limited += control.limited
        for link, rectifier, inverter in augmented.links:
            q_rectifier, tap_rectifier, mu_rectifier = converter_operating_point(
                link, True, magnitude[rectifier] * link.rectifier_voltage_scale
            )
            q_inverter, tap_inverter, mu_inverter = converter_operating_point(
                link, False, magnitude[inverter] * link.inverter_voltage_scale
            )
            case.buses[rectifier].q_load_mvar += q_rectifier - link.rectifier_reactive_mvar
            case.buses[inverter].q_load_mvar += q_inverter - link.inverter_reactive_mvar
            link.rectifier_reactive_mvar = q_rectifier
            link.inverter_reactive_mvar = q_inverter
            link.tap_rectifier, link.mu_rectifier_deg = tap_rectifier, mu_rectifier
            link.tap_inverter, link.mu_inverter_deg = tap_inverter, mu_inverter
        if self.print_iterations:
            print(
                f"  -- controls: {at_q_limit} generators at a reactive limit, "
                f"{regulating} LTC groups regulating, {limited} at a tap limit, "
                f"{at_voltage_limit} VLIM buses at a voltage limit",
                flush=True,
            )

    def _print_header(self, original_bus_count: int) -> None:
        name = self.filename.name if self.filename is not None else "in-memory case"
        base = self.case.base_mva
        print(f"{self.solver_name}: {name}")
        print(
            f"  Buses: {original_bus_count} ({len(self.case.buses)} after low-impedance reduction)"
        )
        print(
            f"  Tolerance: {self.tolerance:.3e} pu ({self.tolerance * base:.4g} MW/Mvar), "
            f"up to {self.max_iterations} iterations per control pass"
        )
        print(
            "  Mismatch: largest P, Q, SVC, or tap-control residual; converged when "
            "it is below the tolerance."
        )
        print(
            "  Max dV / Max dAng: largest voltage-magnitude / angle change of the step "
            "that produced the row. Step: line-search length (1 = full Newton step)."
        )
        print()
        print(_ITERATION_HEADER)
        print(_ITERATION_RULE, flush=True)

    def _print_iteration(
        self,
        iteration: int,
        residual: np.ndarray,
        layout: _Layout,
        last_step: tuple[float, float, float] | None,
    ) -> None:
        angle_count = len(layout.angle_buses)
        pq_count = len(layout.pq)
        base = self.case.base_mva
        p_value, p_bus = self._largest(residual[:angle_count], layout.angle_buses)
        q_value, q_bus = self._largest(residual[angle_count : angle_count + pq_count], layout.pq)
        controls = residual[angle_count + pq_count :]
        # Round before formatting so tiny negatives print as 0.000, not -0.000.
        p_mw = round(p_value * base, 3) + 0.0
        q_mvar = round(q_value * base, 3) + 0.0
        cells = [
            f"{self._pass_number:>{_WIDTHS[0]}d}",
            f"{iteration:>{_WIDTHS[1]}d}",
            f"{_max_abs(residual):>{_WIDTHS[2]}.3e}",
            f"{p_mw:>{_WIDTHS[3]}.3f}",
            f"{p_bus:>{_WIDTHS[4]}}",
            f"{q_mvar:>{_WIDTHS[5]}.3f}",
            f"{q_bus:>{_WIDTHS[6]}}",
            f"{_max_abs(controls):>{_WIDTHS[7]}.3e}" if controls.size else "-".rjust(_WIDTHS[7]),
        ]
        if last_step is None:
            cells += ["-".rjust(width) for width in _WIDTHS[8:]]
        else:
            delta_v, delta_angle, scale = last_step
            cells += [
                f"{delta_v:>{_WIDTHS[8]}.5f}",
                f"{delta_angle:>{_WIDTHS[9]}.4f}",
                f"{scale:>{_WIDTHS[10]}.4f}",
            ]
        print("  ".join(cells), flush=True)

    def _largest(self, values: np.ndarray, buses: np.ndarray) -> tuple[float, str]:
        """Signed largest-magnitude entry and the number of its bus."""
        if values.size == 0:
            return 0.0, "-"
        position = int(np.argmax(np.abs(values)))
        return float(values[position]), str(self.case.buses[int(buses[position])].number)

    @staticmethod
    def _print_control_update(pass_number: int, changes: dict[str, bool]) -> None:
        changed = [name for name, flag in changes.items() if flag]
        if changed:
            print(
                f"  -- pass {pass_number} converged; updating {', '.join(changed)} and re-solving",
                flush=True,
            )
        else:
            print(f"  -- pass {pass_number} converged; controls are stable", flush=True)

    def _print_summary(self, result: PowerFlowResult) -> None:
        print(_ITERATION_RULE)
        base = self.case.base_mva
        passes = f"{self._pass_number} control pass{'es' if self._pass_number != 1 else ''}"
        if result.converged:
            print(
                f"Converged in {result.iterations} iterations over {passes}; "
                f"max mismatch {result.max_mismatch:.3e} pu "
                f"({result.max_mismatch * base:.3e} MW/Mvar)",
                flush=True,
            )
        else:
            print(
                f"Did not converge after {result.iterations} iterations over {passes}: "
                f"{result.failure_reason}; max mismatch {result.max_mismatch:.3e} pu",
                flush=True,
            )

    def _track_control_limits(
        self, controls: list[TapControl], start_shifts: list[float], step: np.ndarray
    ) -> None:
        """Fix a control at its limit after it keeps pushing against it."""
        for offset, control in enumerate(controls):
            if self._tap_law(control) is not None:
                continue  # its complementarity law already handles the limits
            if step[offset] == 0.0:
                continue  # a decoupled half-step that does not move this control
            wanted = start_shifts[offset] + step[offset]
            at_bound = (
                control.shift <= control.shift_min + 1e-9 and wanted < control.shift_min
            ) or (control.shift >= control.shift_max - 1e-9 and wanted > control.shift_max)
            control.bound_hits = control.bound_hits + 1 if at_bound else 0
            if control.bound_hits >= _BOUND_HITS_TO_LIMIT:
                control.limited = True

    def _enforce_generator_q_limits(self, ybus: csc_matrix, voltage: np.ndarray) -> bool:
        calculated = calculate_power(ybus, voltage)
        svc_by_bus: dict[int, float] = {}
        for svc in self.case.svcs:
            bus_index = self.case.bus_index.get(svc.bus)
            if bus_index is not None and self.case.buses[bus_index].bus_type == BusType.PQ:
                svc_by_bus[svc.bus] = svc_by_bus.get(svc.bus, 0.0) + svc.q_mvar
        changed = False
        for index, bus in enumerate(self.case.buses):
            if bus.bus_type != BusType.PV or bus.q_min_mvar is None or bus.q_max_mvar is None:
                continue
            required = (
                calculated[index].imag * self.case.base_mva
                + bus.q_load_mvar
                - svc_by_bus.get(bus.number, 0.0)
            )
            if required > bus.q_max_mvar + 0.1:
                bus.q_generation_mvar = bus.q_max_mvar + svc_by_bus.get(bus.number, 0.0)
                bus.bus_type = BusType.PQ
                changed = True
            elif required < bus.q_min_mvar - 0.1:
                bus.q_generation_mvar = bus.q_min_mvar + svc_by_bus.get(bus.number, 0.0)
                bus.bus_type = BusType.PQ
                changed = True
        return changed

    def _enforce_voltage_limits(self, voltage: np.ndarray) -> bool:
        if self.case.options.get("VLIM", "L") != "L":
            return False
        changed = False
        for index, bus in enumerate(self.case.buses):
            if bus.bus_type != BusType.PQ or not bus.voltage_limit_control:
                continue
            magnitude = abs(voltage[index])
            target = None
            if magnitude > bus.voltage_max_pu + 1e-3:
                target = bus.voltage_max_pu
            elif magnitude < bus.voltage_min_pu - 1e-3:
                target = bus.voltage_min_pu
            if target is not None:
                bus.bus_type = BusType.PV
                bus.voltage_pu = target
                voltage[index] = target * np.exp(1j * np.angle(voltage[index]))
                changed = True
        return changed

    def _warm_start_angles(self, ybus: csc_matrix, voltage: np.ndarray) -> np.ndarray:
        angle_buses = np.concatenate((self.case.pv_indices, self.case.pq_indices))
        if not angle_buses.size:
            return voltage
        specified = self.case.specified_power
        candidates = [voltage.copy(), np.abs(voltage).astype(np.complex128)]
        best_voltage = voltage.copy()
        best_score = float("inf")
        empty = np.empty(0, dtype=np.int64)
        for candidate in candidates:
            for _ in range(24):
                mismatch = (calculate_power(ybus, candidate).real - specified.real)[angle_buses]
                norm = _max_abs(mismatch)
                if norm < 0.1:
                    break
                jacobian = _network_jacobian(ybus, candidate, angle_buses, empty)
                try:
                    step = self._solve_linear_system(jacobian, mismatch)
                except (MatrixRankWarning, FloatingPointError, RuntimeError, ValueError):
                    break
                if not np.all(np.isfinite(step)):
                    break
                angle = np.angle(candidate)
                current_norm = float(np.linalg.norm(mismatch))
                scale = 1.0
                accepted = False
                for _ in range(10):
                    trial_angle = angle.copy()
                    trial_angle[angle_buses] += scale * step
                    trial = np.abs(candidate) * np.exp(1j * trial_angle)
                    trial_mismatch = (calculate_power(ybus, trial).real - specified.real)[
                        angle_buses
                    ]
                    if float(np.linalg.norm(trial_mismatch)) < current_norm:
                        candidate = trial
                        accepted = True
                        break
                    scale *= 0.5
                if not accepted:
                    break
            calculated = calculate_power(ybus, candidate)
            score = _max_abs((calculated.real - specified.real)[angle_buses]) + _max_abs(
                (calculated.imag - specified.imag)[self.case.pq_indices]
            )
            if score < best_score:
                best_score = score
                best_voltage = candidate
        return best_voltage

    def _assign_island_references(self, ybus: csc_matrix) -> None:
        graph = ybus.copy()
        graph.data = np.ones(graph.nnz, dtype=np.int8)
        island_count, labels = connected_components(graph, directed=False)
        for island in range(island_count):
            indices = np.flatnonzero(labels == island)
            if any(self.case.buses[i].bus_type == BusType.SLACK for i in indices):
                continue
            candidates = [i for i in indices if self.case.buses[i].bus_type == BusType.PV]
            if not candidates:
                candidates = indices.tolist()
            reference = max(candidates, key=lambda i: self.case.buses[i].p_generation_mw)
            self.case.buses[reference].bus_type = BusType.SLACK


class _Triplets:
    def __init__(self) -> None:
        self.rows: list[int] = []
        self.columns: list[int] = []
        self.values: list[float] = []

    def add(self, row: int, column: int, value: float) -> None:
        self.rows.append(row)
        self.columns.append(column)
        self.values.append(float(value))

    def matrix(self, rows: int, columns: int) -> csc_matrix:
        return coo_matrix((self.values, (self.rows, self.columns)), shape=(rows, columns)).tocsc()


def _network_jacobian(
    ybus: csc_matrix, voltage: np.ndarray, angle_buses: np.ndarray, pq: np.ndarray
) -> csc_matrix:
    magnitude = np.abs(voltage)
    normalized_voltage = voltage / np.maximum(magnitude, 1e-12)
    current = ybus @ voltage
    diagonal_voltage = diags(voltage, format="csc")
    diagonal_current = diags(current, format="csc")
    diagonal_normalized = diags(normalized_voltage, format="csc")
    d_power_d_magnitude = (
        diagonal_voltage @ (ybus @ diagonal_normalized).conjugate()
        + diagonal_current.conjugate() @ diagonal_normalized
    )
    d_power_d_angle = (
        1j * diagonal_voltage @ (diagonal_current - ybus @ diagonal_voltage).conjugate()
    )
    j11 = d_power_d_angle[angle_buses, :][:, angle_buses].real
    j12 = d_power_d_magnitude[angle_buses, :][:, pq].real
    j21 = d_power_d_angle[pq, :][:, angle_buses].imag
    j22 = d_power_d_magnitude[pq, :][:, pq].imag
    return vstack(
        (hstack((j11, j12), format="csc"), hstack((j21, j22), format="csc")),
        format="csc",
    )


def _sparse_solve(
    jacobian: csc_matrix,
    residual: np.ndarray,
    lu_decomposition: bool = False,
    sparsity: bool = False,
) -> np.ndarray:
    """Solve ``J x = -residual`` with the selected sparse direct solver."""
    permc_spec = _sparse_ordering(sparsity)
    if lu_decomposition:
        factor = splu(jacobian.tocsc(), permc_spec=permc_spec)
        return np.asarray(factor.solve(-residual), dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        return np.asarray(
            spsolve(jacobian, -residual, permc_spec=permc_spec, use_umfpack=False),
            dtype=float,
        )


def _clu_sparse_solve(
    jacobian: csc_matrix, residual: np.ndarray, solver: NewtonRaphsonPowerFlow
) -> np.ndarray:
    try:
        from VeraGridEngine.Utils.Sparse import clu
    except ImportError as exc:
        raise ImportError(
            "linear_solver='clu' requires the optional VeraGridEngine package"
        ) from exc

    matrix = jacobian.tocsc(copy=True)
    matrix.sum_duplicates()
    matrix.sort_indices()
    factorizer = solver._clu_factorizer
    if factorizer is None:
        factorizer = clu.Factorizer(matrix, clu.Options())
        solver._clu_factorizer = factorizer

    status = factorizer.factorize(matrix)
    if status is clu.Status.INVALID_INPUT:
        factorizer = clu.Factorizer(matrix, clu.Options())
        solver._clu_factorizer = factorizer
        status = factorizer.factorize()
    if status is not clu.Status.SUCCESS:
        solver._clu_factorizer = None
        raise RuntimeError(f"CLU factorization failed: {clu.status_name(status)}")

    right_hand_side = -np.asarray(residual, dtype=np.float64)
    step = np.empty_like(right_hand_side)
    status = factorizer.solve(right_hand_side, step)
    if status is not clu.Status.SUCCESS:
        solver._clu_factorizer = None
        raise RuntimeError(f"CLU triangular solve failed: {clu.status_name(status)}")
    return step


def _sparse_ordering(sparsity: bool) -> str:
    """Return the PDF-inspired minimum-degree or the default COLAMD ordering."""
    return "MMD_AT_PLUS_A" if sparsity else "COLAMD"


def _conflicting_controls(layout: _Layout, step: np.ndarray) -> list[TapControl]:
    if not layout.controls or not np.all(np.isfinite(step)):
        return []
    offset = layout.network_size + len(layout.active_svc)
    found = []
    for position, control in enumerate(layout.controls):
        limit = _CONFLICT_TAP_STEP if control.mode == "V" else _CONFLICT_PHASE_STEP
        if abs(step[offset + position]) > limit:
            found.append(control)
    return found


def _max_abs(values: np.ndarray) -> float:
    return float(np.max(np.abs(values))) if values.size else 0.0


def _dead_zone(value: np.ndarray | float, low: np.ndarray | float, high: np.ndarray | float):
    """Distance of ``value`` outside ``[low, high]`` (negative below, zero inside)."""
    return np.where(value > high, value - high, np.where(value < low, value - low, 0.0))


def _median_index(pieces: tuple[float, float, float]) -> int:
    """Index of the median of three values (ties resolved like a stable sort)."""
    return sorted(range(3), key=pieces.__getitem__)[1]


__all__ = ["BusSolution", "NewtonRaphsonPowerFlow", "PowerFlowResult"]
