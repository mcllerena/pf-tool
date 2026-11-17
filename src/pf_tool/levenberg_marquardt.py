"""Sparse Levenberg-Marquardt AC power-flow solver."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.sparse import csc_matrix, diags

from pf_tool.models.svc import active_svc_states, refresh_svc_limits
from pf_tool.newton_raphson import (
    NewtonRaphsonPowerFlow,
    _conflicting_controls,
    _max_abs,
    _sparse_solve,
)
from pf_tool.utils.network import build_ybus

_MAX_DAMPING = 1e16


def _levenberg_marquardt_step(
    jacobian: csc_matrix, residual: np.ndarray, damping: float
) -> np.ndarray:
    normal = (jacobian.T @ jacobian).tocsc()
    diagonal = np.asarray(normal.diagonal(), dtype=float)
    maximum_diagonal = float(np.max(diagonal, initial=0.0))
    if not math.isfinite(maximum_diagonal) or maximum_diagonal <= 0.0:
        raise ValueError("Levenberg-Marquardt Jacobian has no finite nonzero columns")
    diagonal = np.maximum(diagonal, maximum_diagonal * 1e-12)
    gradient = np.asarray(jacobian.T @ residual, dtype=float).ravel()
    damped_normal = (normal + diags(damping * diagonal, format="csc")).tocsc()
    step = _sparse_solve(damped_normal, gradient)
    if not np.all(np.isfinite(step)):
        raise FloatingPointError("Levenberg-Marquardt step is non-finite")
    return step


class LevenbergMarquardtPowerFlow(NewtonRaphsonPowerFlow):
    """Solve AC power flow with sparse, diagonally damped least-squares steps.

    The inherited sequential controller handles SVC, generator, shunt, LTC,
    and LCC updates. The LM inner solve starts from the case voltages and uses
    SciPy sparse direct solves of the damped normal equations.
    """

    solver_name = "Levenberg-Marquardt power flow"
    method_label = "Levenberg-Marquardt"

    def __init__(
        self,
        filename: str | Path,
        *,
        initial_damping: float = 1e-3,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_control_passes: int = 50,
        print_iterations: bool = False,
    ) -> None:
        if not math.isfinite(initial_damping) or initial_damping <= 0.0:
            raise ValueError("initial_damping must be finite and greater than zero")
        super().__init__(
            filename,
            tolerance=tolerance,
            max_iterations=max_iterations,
            max_control_passes=max_control_passes,
            control_method="sequential",
            print_iterations=print_iterations,
        )
        self.initial_damping = float(initial_damping)

    def _warm_start_angles(self, ybus: csc_matrix, voltage: np.ndarray) -> np.ndarray:
        return voltage

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
        damping = self.initial_damping
        growth = 2.0
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

            try:
                jacobian = self._build_jacobian(ybus, voltage, svc_states, layout)
                step = _levenberg_marquardt_step(jacobian, residual, damping)
                for _ in range(5):
                    suspicious = _conflicting_controls(layout, step)
                    if not suspicious:
                        break
                    for control in suspicious:
                        control.conflicting = True
                    layout = self._layout(svc_states, controls)
                    residual = self._residual(ybus, voltage, specified, svc_states, layout)
                    jacobian = self._build_jacobian(ybus, voltage, svc_states, layout)
                    step = _levenberg_marquardt_step(jacobian, residual, damping)
            except (FloatingPointError, RuntimeError, ValueError) as exc:
                return (
                    voltage,
                    ybus,
                    iteration,
                    mismatch,
                    f"Levenberg-Marquardt sparse solve failed: {exc}",
                )
            if not np.all(np.isfinite(step)):
                return (
                    voltage,
                    ybus,
                    iteration,
                    mismatch,
                    "Levenberg-Marquardt step is non-finite",
                )

            previous_voltage = voltage
            voltage, svc_states, ybus = self._line_search(
                ybus, voltage, specified, svc_states, layout, step, residual
            )
            step_scale = self._step_scale
            trial_layout = self._layout(svc_states, controls)
            trial_residual = self._residual(ybus, voltage, specified, svc_states, trial_layout)
            scaled_step = step_scale * step
            predicted_residual = residual + jacobian @ scaled_step
            predicted_reduction = 0.5 * (
                float(residual @ residual) - float(predicted_residual @ predicted_residual)
            )
            actual_reduction = 0.5 * (
                float(residual @ residual) - float(trial_residual @ trial_residual)
            )
            gain_ratio = (
                actual_reduction / predicted_reduction if predicted_reduction > 0.0 else -math.inf
            )
            if gain_ratio > 0.0 and math.isfinite(gain_ratio):
                damping *= max(1.0 / 3.0, 1.0 - (2.0 * gain_ratio - 1.0) ** 3)
                growth = 2.0
            else:
                damping = min(damping * growth, _MAX_DAMPING)
                growth = min(growth * 2.0, _MAX_DAMPING)

            if self.print_iterations:
                last_step = (
                    _max_abs(np.abs(voltage) - np.abs(previous_voltage)),
                    float(np.rad2deg(_max_abs(np.angle(voltage * np.conj(previous_voltage))))),
                    step_scale,
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


__all__ = ["LevenbergMarquardtPowerFlow"]
