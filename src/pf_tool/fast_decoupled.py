"""Fast decoupled AC power-flow solver.

The Newton update ``J·Δx = -F`` is split into two smaller systems by
neglecting the off-diagonal Jacobian blocks ``J_PV`` and ``J_Qθ``: active power
is assumed insensitive to voltage magnitude and reactive power insensitive to
angle (small conductance, small angle differences).

* ``enhancement=False`` (decoupled Newton): every iteration evaluates the
  diagonal blocks at the current point and solves ``J_Pθ·Δθ = -ΔP`` and
  ``J_QV·Δ|V| = -ΔQ`` from the same point.
* ``enhancement=True`` (constant matrices): with a flat voltage profile,
  ``J_Pθ ≈ -[V]·B'·[V]`` and ``J_QV ≈ -[V]·B''``.  Approximating the second
  ``[V]`` by the identity and dividing by ``[V]`` gives
  ``-B'·Δθ = ΔP/|V|`` and ``-B''·Δ|V| = ΔQ/|V|``.  ``B''`` is ``Im(Ybus)``
  restricted to the PQ buses.  ``B'`` is built from series reactances only
  (``b = 1/x``, the XB scheme): shunts, line charging and series resistance,
  which mainly affect reactive power, are left out of the P-θ matrix.  The
  matrices are factored once and refactored only when the voltage-controlled
  buses or the regulating devices change.  The P and Q half-iterations
  alternate, so each Q step starts from the latest angles.

The mismatches always use the full AC power-flow equations, so a converged
fast decoupled solution is as accurate as a Newton-Raphson solution.
"""

from __future__ import annotations

import warnings
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import LinearOperator, MatrixRankWarning, gmres, splu

from pf_tool.models.bus import BusType
from pf_tool.models.svc import SVCState, active_svc_states, refresh_svc_limits
from pf_tool.newton_raphson import (
    NewtonRaphsonPowerFlow,
    PowerFlowResult,
    _conflicting_controls,
    _Layout,
    _max_abs,
)
from pf_tool.utils.network import build_ybus

# A decoupled iteration must cut the mismatch (max or 2-norm) by this factor;
# otherwise it is replaced by a coupling-corrected step.
_SUFFICIENT_DECREASE = 0.9
# Coupling correction: preconditioned GMRES settings.
_CORRECTION_TOLERANCE = 1e-3
_CORRECTION_RESTART = 30
_CORRECTION_CYCLES = 3


@dataclass(slots=True)
class _Subproblem:
    """One decoupled half: unknown/equation positions and its factored matrix."""

    rows: np.ndarray
    buses: np.ndarray  # bus of each P or Q equation in ``rows``; -1 for device equations
    factor: object
    # True for the constant B'/B'' matrices, whose bus equations are ΔP/|V|, ΔQ/|V|.
    scaled: bool = False


class FastDecoupledPowerFlow(NewtonRaphsonPowerFlow):
    """Solve an ANAREDE ``.dat`` case with the fast decoupled method.

    **Decoupled subproblems.** The P-θ subproblem solves bus angles and
    phase-shifter angles against the active-power and phase-shifter flow
    equations.  The Q-V subproblem solves PQ voltage magnitudes, SVC outputs
    and LTC tap shifts against the reactive-power, SVC and LTC equations.
    Series compensators and transformer taps enter through ``Ybus``.
    Generator reactive limits, voltage-limited buses, switched shunts, LTC
    activation and LCC set-points are updated between solves, as in the
    sequential Newton-Raphson method.

    **Options.**

    * ``enhancement``: ``False`` re-evaluates the decoupled Jacobian blocks
      every iteration; ``True`` uses the constant ``B'``/``B''`` matrices with
      alternating P and Q half-iterations.
    * ``warm_start``: ``True`` (default) first solves the case with the
      sequential Newton-Raphson method and re-solves it with the fast
      decoupled method from that solved state (a hot start).  ``False`` starts
      from the voltages stored in the case.
    * ``max_iterations`` counts full (P and Q) iterations per control pass and
      defaults to ``ACIT``.

    **Coupling correction.** Where the decoupling assumptions fail (nearly
    resistive branches, series capacitors, negative-reactance transformer
    branches), a decoupled step may not reduce the mismatch.  An iteration
    that does not cut the largest or the 2-norm mismatch by 10 % is undone and
    replaced by a coupling-corrected step: a few GMRES iterations on the full
    Jacobian, preconditioned by the decoupled blocks.  If GMRES does not reach
    its tolerance, the full Jacobian is factored for that step instead.
    Controls whose corrected step is non-physical (two LTCs fighting over the
    same voltage, for example) are fixed at their current tap, as in the
    Newton-Raphson method.  ``coupling_corrections`` counts these steps and
    ``factorizations`` counts every sparse LU factorization.
    """

    solver_name = "Fast decoupled power flow"
    method_label = "Fast decoupled"
    control_method = "sequential"
    enhancement = False

    def __init__(
        self,
        filename: str | Path,
        *,
        enhancement: bool = False,
        warm_start: bool = True,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_control_passes: int = 50,
        print_iterations: bool = False,
    ) -> None:
        super().__init__(
            filename,
            tolerance=tolerance,
            max_iterations=max_iterations,
            max_control_passes=max_control_passes,
            control_method="sequential",
            print_iterations=print_iterations,
        )
        self.enhancement = bool(enhancement)
        self.warm_start = bool(warm_start)
        if self.enhancement:
            self.solver_name = "Fast decoupled power flow (constant B'/B'')"
        self.warm_start_iterations = 0
        self.warm_start_converged = False
        self.factorizations = 0
        self.coupling_corrections = 0

    # ------------------------------------------------------------------
    # Warm start
    # ------------------------------------------------------------------

    def solve(self) -> PowerFlowResult:
        """Run the fast decoupled power flow, from a Newton solution if ``warm_start``."""
        if self.warm_start:
            self._apply_newton_start()
        return super().solve()

    def _apply_newton_start(self) -> None:
        """Hot start from a solved Newton-Raphson power flow.

        A copy of the case is solved with the sequential Newton-Raphson method.
        Its solved state becomes this case's starting point: bus voltages and
        angles, generator PV/PQ status, transformer taps, switched-shunt steps,
        SVC outputs and LCC injections.  The fast decoupled method then
        re-solves the case from that state with its own control passes.
        """
        newton = object.__new__(NewtonRaphsonPowerFlow)
        newton.case = deepcopy(self.case)
        newton.tolerance = self.tolerance
        newton.max_iterations = self.max_iterations
        newton.max_control_passes = self.max_control_passes
        newton.tap_voltage_band = self.tap_voltage_band
        newton.control_method = "sequential"
        newton.reduction = None
        newton.result = None
        result = newton.solve()
        self.warm_start_iterations = result.iterations
        self.warm_start_converged = result.converged
        if self.print_iterations:
            outcome = "converged" if result.converged else "stopped"
            print(
                f"Warm start: Newton-Raphson {outcome} in {result.iterations} iterations; "
                "re-solving with the fast decoupled method",
                flush=True,
            )
        if not np.all(np.isfinite(result.voltage)):
            return
        # The Newton solve wrote its control states back to its copy of the
        # case; adopt that copy and its solved voltages.
        self.case = newton.case
        self.case.solved_voltage = None
        for bus, value in zip(self.case.buses, result.voltage):
            bus.angle_radians = float(np.angle(value))
            if bus.bus_type == BusType.PQ:
                bus.voltage_pu = float(abs(value))

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
        last_step: tuple[float, float, float] | None = None
        halves: tuple[_Subproblem, _Subproblem] | None = None
        structure = None

        for iteration in range(self.max_iterations + 1):
            layout = self._layout(svc_states, controls)
            refresh_svc_limits(svc_states, np.abs(voltage))
            self._svc_states = svc_states
            residual = self._residual(ybus, voltage, specified, svc_states, layout)
            mismatch = _max_abs(residual)
            norm = float(np.linalg.norm(residual))
            if self.print_iterations:
                self._print_iteration(iteration, residual, layout, last_step)
            if mismatch <= self.tolerance or iteration == self.max_iterations:
                return voltage, ybus, iteration, mismatch, None

            key = (
                tuple(int(i) for i in layout.active_svc),
                tuple(state.limit_state for state in svc_states),
                tuple(id(control) for control in layout.controls),
            )
            try:
                if not self.enhancement:
                    halves = self._jacobian_blocks(ybus, voltage, svc_states, layout)
                elif halves is None or key != structure:
                    halves = self._constant_blocks(ybus, svc_states, layout)
                    structure = key
            except (RuntimeError, ValueError) as exc:
                return voltage, ybus, iteration, mismatch, f"Factorization failed: {exc}"

            start = (voltage, svc_states, ybus, [control.shift for control in layout.controls])
            if self.enhancement:
                # P half from the latest point, then Q half from the new angles.
                for position, half in enumerate(halves):
                    if position:
                        refresh_svc_limits(svc_states, np.abs(voltage))
                        residual = self._residual(ybus, voltage, specified, svc_states, layout)
                    step = self._half_step(half, residual, voltage)
                    if self._screen_conflicts(layout, step):
                        break
                    voltage, svc_states, ybus = self._line_search(
                        ybus,
                        voltage,
                        specified,
                        svc_states,
                        layout,
                        step,
                        residual,
                        rows=half.rows,
                    )
            else:
                # Both corrections from the same point.
                step = sum(self._half_step(half, residual, voltage) for half in halves)
                if not self._screen_conflicts(layout, step):
                    voltage, svc_states, ybus = self._line_search(
                        ybus, voltage, specified, svc_states, layout, step, residual
                    )
            scale = self._step_scale

            refresh_svc_limits(svc_states, np.abs(voltage))
            after = self._residual(ybus, voltage, specified, svc_states, layout)
            improved = (
                _max_abs(after) < _SUFFICIENT_DECREASE * mismatch
                or float(np.linalg.norm(after)) < _SUFFICIENT_DECREASE * norm
            )
            unchanged = layout.controls == self._layout(svc_states, controls).controls
            if not improved:
                # Undo the iteration.  A control that just reached a limit
                # keeps its new status, and the next iteration retries with it;
                # otherwise take a coupling correction from the same point.
                voltage, svc_states, ybus, shifts = start
                for control, shift in zip(layout.controls, shifts):
                    control.apply(shift)
                refresh_svc_limits(svc_states, np.abs(voltage))
                scale = 0.0
            if not improved and unchanged:
                residual = self._residual(ybus, voltage, specified, svc_states, layout)
                step = self._corrected_step(ybus, voltage, svc_states, layout, halves, residual)
                if step is not None and not self._screen_conflicts(layout, step):
                    voltage, svc_states, ybus = self._line_search(
                        ybus, voltage, specified, svc_states, layout, step, residual
                    )
                    scale = self._step_scale
                    self.coupling_corrections += 1
            if self.print_iterations:
                previous = start[0]
                last_step = (
                    _max_abs(np.abs(voltage) - np.abs(previous)),
                    float(np.rad2deg(_max_abs(np.angle(voltage * np.conj(previous))))),
                    scale,
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

    @staticmethod
    def _screen_conflicts(layout: _Layout, step: np.ndarray) -> bool:
        """Fix controls whose step is non-physical; True when any was found."""
        suspicious = _conflicting_controls(layout, step)
        for control in suspicious:
            control.conflicting = True
        return bool(suspicious)

    def _half_step(
        self, half: _Subproblem, residual: np.ndarray, voltage: np.ndarray
    ) -> np.ndarray:
        """Correction of one subproblem, zero elsewhere."""
        right = residual[half.rows].copy()
        if half.scaled:
            # ΔP/|V| and ΔQ/|V|: divide the bus equations by their voltage.
            bus_rows = half.buses >= 0
            right[bus_rows] /= np.abs(voltage[half.buses[bus_rows]])
        step = np.zeros(residual.size)
        solved = half.factor.solve(right)
        step[half.rows] = -np.where(np.isfinite(solved), solved, 0.0)
        return step

    def _corrected_step(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
        halves: tuple[_Subproblem, _Subproblem],
        residual: np.ndarray,
    ) -> np.ndarray | None:
        """Solve ``J·Δx = -F`` by GMRES preconditioned with decoupled factors.

        The preconditioner is the pair of decoupled Jacobian blocks at the
        current point (with ``enhancement=True`` these are factored here, since
        the constant ``B'``/``B''`` are too far from ``J`` where coupling is
        strong).  When GMRES stalls, the full Jacobian is factored instead.
        """
        jacobian = self._build_jacobian(ybus, voltage, svc_states, layout).tocsr()
        size = residual.size
        if self.enhancement:
            try:
                halves = self._jacobian_blocks(ybus, voltage, svc_states, layout)
            except (RuntimeError, ValueError):
                return None

        def precondition(vector: np.ndarray) -> np.ndarray:
            return -sum(self._half_step(half, np.ravel(vector), voltage) for half in halves)

        preconditioner = LinearOperator((size, size), matvec=precondition, dtype=float)
        step, info = gmres(
            jacobian,
            -residual,
            M=preconditioner,
            rtol=_CORRECTION_TOLERANCE,
            restart=_CORRECTION_RESTART,
            maxiter=_CORRECTION_CYCLES,
        )
        if info != 0 or not np.all(np.isfinite(step)):
            # GMRES stalled: fall back to an exact solve with the full Jacobian.
            try:
                step = -_factor(jacobian.tocsc()).solve(residual)
            except (RuntimeError, ValueError):
                return None
            self.factorizations += 1
        return step if np.all(np.isfinite(step)) else None

    @staticmethod
    def _partition(layout: _Layout, size: int) -> tuple:
        """Rows and bus mapping of the P-θ and Q-V subproblems."""
        angle_count = len(layout.angle_buses)
        pq_count = len(layout.pq)
        control_start = layout.network_size + len(layout.active_svc)
        bus_of_row = np.full(size, -1, dtype=np.int64)
        bus_of_row[:angle_count] = layout.angle_buses
        bus_of_row[angle_count : angle_count + pq_count] = layout.pq
        phase = [control_start + i for i, c in enumerate(layout.controls) if c.mode == "P"]
        active = np.concatenate((np.arange(angle_count), np.asarray(phase, dtype=np.int64)))
        reactive = np.setdiff1d(np.arange(size), active)
        return (active, bus_of_row[active]), (reactive, bus_of_row[reactive])

    def _jacobian_blocks(
        self,
        ybus: csc_matrix,
        voltage: np.ndarray,
        svc_states: list[SVCState],
        layout: _Layout,
    ) -> tuple[_Subproblem, _Subproblem]:
        """``J_Pθ`` and ``J_QV`` (with their device rows) at the current point."""
        jacobian = self._build_jacobian(ybus, voltage, svc_states, layout).tocsc()
        halves = tuple(
            _Subproblem(rows, buses, _factor(jacobian[rows, :][:, rows]))
            for rows, buses in self._partition(layout, jacobian.shape[0])
        )
        self.factorizations += 2
        return halves

    def _constant_blocks(
        self, ybus: csc_matrix, svc_states: list[SVCState], layout: _Layout
    ) -> tuple[_Subproblem, _Subproblem]:
        """``-B'`` and ``-B''`` with the device rows/columns of a flat profile."""
        # Device couplings and equations are evaluated at a flat profile
        # (|V| = 1, θ = 0), the same assumption behind B' and B''.
        flat = np.ones(len(self.case.buses), dtype=np.complex128)
        flat_states = [replace(state) for state in svc_states]
        jacobian = self._build_jacobian(ybus, flat, flat_states, layout).tocsc()
        matrices = (_series_reactance_matrix(ybus), csc_matrix(ybus.imag))
        halves = []
        for (rows, buses), susceptance in zip(self._partition(layout, jacobian.shape[0]), matrices):
            block = jacobian[rows, :][:, rows].tolil()
            count = int(np.count_nonzero(buses >= 0))
            network = buses[:count]
            block[:count, :count] = -susceptance[network, :][:, network]
            halves.append(_Subproblem(rows, buses, _factor(block.tocsc()), scaled=True))
        self.factorizations += 2
        return tuple(halves)


def _series_reactance_matrix(ybus: csc_matrix) -> csc_matrix:
    """``B'`` from series reactances only (XB scheme): ``B'_jk = 1/x_jk``, ``B'_jj = -Σ 1/x_jk``.

    Each ``x_jk`` is the reactance of the series admittance between buses j and
    k as stamped in ``Ybus`` (``x = Im(-1/Y_jk)``), so taps and network
    reduction scaling are kept while resistance and shunts are dropped.
    """
    matrix = csc_matrix(ybus).tocoo()
    off = (matrix.row != matrix.col) & (np.abs(matrix.data) > 0.0)
    reactance = np.imag(-1.0 / matrix.data[off])
    inverse = np.divide(1.0, reactance, out=np.zeros_like(reactance), where=reactance != 0.0)
    size = ybus.shape[0]
    rows = np.concatenate((matrix.row[off], np.arange(size)))
    columns = np.concatenate((matrix.col[off], np.arange(size)))
    diagonal = np.zeros(size)
    np.add.at(diagonal, matrix.row[off], -inverse)
    values = np.concatenate((inverse, diagonal))
    return csc_matrix((values, (rows, columns)), shape=(size, size))


def _factor(matrix: csc_matrix):
    with warnings.catch_warnings():
        warnings.simplefilter("error", MatrixRankWarning)
        return splu(csc_matrix(matrix))


__all__ = ["FastDecoupledPowerFlow"]
