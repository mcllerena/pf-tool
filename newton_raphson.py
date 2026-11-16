"""Sparse polar-coordinate Newton-Raphson AC power-flow solver."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix, csc_matrix, diags, hstack, vstack
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from models.bus import BusType
from models.hvdc_lcc import update_lcc_from_dc_solution
from models.network_reduction import reduce_low_impedance_branches
from models.shunt import adjust_controlled_shunts
from models.svc import (
    SVCState,
    active_svc_states,
    refresh_svc_limits,
    store_svc_outputs,
)
from models.transformer import TapControl, branch_sensitivity, reconcile_tap_controls
from utils.dat_parser import parse_dat_file
from utils.network import build_ybus, calculate_power, freeze_branch_matrix
from utils.utils import build_power_flow_case

# Largest tap-ratio / phase-angle (rad) move allowed in one Newton iteration.
_MAX_TAP_STEP = 0.05
_MAX_PHASE_STEP = 0.1
# Consecutive iterations a control may push against its limit before it is
# fixed at that limit and its regulation equation is dropped.
_BOUND_HITS_TO_LIMIT = 3
# A single Newton step beyond these sizes marks a control as conflicting.
_CONFLICT_TAP_STEP = 0.2
_CONFLICT_PHASE_STEP = 1.0


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


class NewtonRaphsonPowerFlow:
    """Solve an ANAREDE ``.dat`` case using sparse Newton-Raphson iterations.

    The input is the AMPL-style block export used by ``tests/data/*.dat``.  The
    constructor accepts an alternate case path for other exports in the same
    format; by default it solves ``tests/data/CASO01-FLOW.dat``.

    Besides bus voltages, the Newton state includes SVC reactive outputs, LTC
    tap shifts (DOPC ``CTAP``) and phase-shifter angles (DOPC ``CPHS``).
    Generator reactive limits, voltage-limited load buses, switched shunts and
    LCC converter set-points are handled in an outer control loop.
    """

    max_control_passes = 50
    tap_voltage_band = 0.01

    def __init__(
        self,
        data_file: str | Path | None = None,
        *,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_control_passes: int = 50,
    ) -> None:
        self.data_file = (
            Path(data_file)
            if data_file is not None
            else Path(__file__).resolve().parent / "data" / "CASO01-FLOW.dat"
        )
        tables = parse_dat_file(self.data_file)
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
        failure_reason: str | None = None
        total_iterations = 0
        mismatch = float("inf")

        # Discrete controls (generator Q limits, voltage-limited buses, shunt
        # banks, LCC set-points) change the equations; re-solve after each pass.
        for _ in range(self.max_control_passes):
            voltage, ybus, iterations, mismatch, reason = self._solve_network(voltage)
            total_iterations += iterations
            if reason is not None or mismatch > self.tolerance:
                failure_reason = reason or "Newton-Raphson did not converge"
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
        pq = self.case.pq_indices
        angle_buses = np.concatenate((self.case.pv_indices, pq))
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

        for iteration in range(self.max_iterations + 1):
            layout = self._layout(svc_states, controls)
            refresh_svc_limits(svc_states, np.abs(voltage))
            self._svc_states = svc_states
            residual = self._residual(ybus, voltage, specified, svc_states, layout)
            mismatch = _max_abs(residual)
            if mismatch <= self.tolerance or iteration == self.max_iterations:
                return voltage, ybus, iteration, mismatch, None

            jacobian = self._build_jacobian(ybus, voltage, svc_states, layout)
            try:
                step = _sparse_solve(jacobian, residual)
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
                    step = _sparse_solve(jacobian, residual)
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

            voltage, svc_states, ybus = self._line_search(
                ybus, voltage, specified, svc_states, layout, step, residual
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
                sensitivity.add(
                    column,
                    angle_count + layout.pq_position[target],
                    control.controlled_scale,
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
    ) -> tuple[np.ndarray, list[SVCState], csc_matrix]:
        angle_count = len(layout.angle_buses)
        magnitude_count = len(layout.pq)
        svc_count = len(layout.active_svc)
        control_offset = angle_count + magnitude_count + svc_count
        control_step = step[control_offset:]
        # Limit large control moves by shortening the whole Newton step, which
        # keeps the search direction (clipping single components would not).
        scale = 1.0
        for offset, control in enumerate(layout.controls):
            limit = _MAX_TAP_STEP if control.mode == "V" else _MAX_PHASE_STEP
            if abs(control_step[offset]) > limit:
                scale = min(scale, limit / abs(control_step[offset]))
        start_shifts = [control.shift for control in layout.controls]
        current_max = _max_abs(residual)
        current_norm = float(np.linalg.norm(residual))
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
                control.apply(
                    np.clip(
                        start_shifts[offset] + scale * control_step[offset],
                        control.shift_min,
                        control.shift_max,
                    )
                )
            trial_ybus = build_ybus(self.case) if layout.controls else ybus
            trial_voltage = trial_magnitude * np.exp(1j * trial_angle)
            trial_residual = self._residual(
                trial_ybus, trial_voltage, specified, trial_states, layout
            )
            if (
                _max_abs(trial_residual) < current_max
                or float(np.linalg.norm(trial_residual)) < current_norm
                or scale <= 1 / 32768
            ):
                self._track_control_limits(layout.controls, start_shifts, control_step)
                return trial_voltage, trial_states, trial_ybus
            scale *= 0.5
        for offset, control in enumerate(layout.controls):
            control.apply(start_shifts[offset])
        return voltage, svc_states, ybus

    @staticmethod
    def _track_control_limits(
        controls: list[TapControl], start_shifts: list[float], step: np.ndarray
    ) -> None:
        """Fix a control at its limit after it keeps pushing against it."""
        for offset, control in enumerate(controls):
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
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", MatrixRankWarning)
                        step = np.asarray(spsolve(jacobian, -mismatch), dtype=float)
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


def _sparse_solve(jacobian: csc_matrix, residual: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        return np.asarray(spsolve(jacobian, -residual), dtype=float)


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


__all__ = ["BusSolution", "NewtonRaphsonPowerFlow", "PowerFlowResult"]
