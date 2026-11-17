"""Load-continuation restoration followed by fast-decoupled AC power flow."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

import numpy as np

from pf_tool.fast_decoupled import FastDecoupledPowerFlow
from pf_tool.models.case import PowerFlowCase
from pf_tool.newton_raphson import NewtonRaphsonPowerFlow, PowerFlowResult
from pf_tool.parser._dat import render_dat
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case

_NUMERIC_DBAR_FIELDS = {
    "No": int,
    "Tb": int,
    "V": float,
    "A0": float,
    "Pg": float,
    "Qg": float,
    "Qgn": float,
    "Qgm": float,
    "Pl": float,
    "Ql": float,
    "Sh": float,
    "Are": int,
}


@dataclass(slots=True)
class RobustPowerFlowResult(PowerFlowResult):
    """Fast-decoupled result and explicit load-restoration details."""

    load_factor: float = 1.0
    load_shed_active_mw: float = 0.0
    load_shed_reactive_mvar: float = 0.0
    restoration_attempts: int = 0
    restoration_iterations: int = 0
    fast_decoupled_iterations: int = 0
    full_load_converged: bool = False
    solved_case_path: Path | None = None


class RobustACPowerFlow:
    """Restore a solvable load level, then verify it with fast decoupled power flow.

    The restoration stage scales only positive active/reactive loads from the
    input DBAR records. After a failed full-load trial, it retries from the
    original voltage profile at ``initial_load_reduction``; subsequent trials
    are warm-started from the preceding voltage profile. Once a load level
    converges with sequential Newton-Raphson and fast decoupled power flow,
    the load is ramped back up in progressively smaller steps to avoid
    unnecessary shedding.

    A ``-solved.dat`` file is written only when both solvers converge. It keeps
    the source data blocks, replaces DBAR P/Q loads with the retained demand,
    and records the final bus voltage/angle as the next solve's starting point.
    """

    def __init__(
        self,
        filename: str | Path,
        *,
        solved_filename: str | Path | None = None,
        max_load_shedding_fraction: float = 0.2,
        initial_load_reduction: float = 0.1,
        load_shedding_step: float = 0.02,
        minimum_load_step: float = 0.0025,
        max_attempts: int = 40,
        tolerance: float | None = None,
        max_iterations: int | None = None,
        max_control_passes: int = 50,
        enhancement: bool = False,
        print_iterations: bool = False,
    ) -> None:
        if not isfinite(max_load_shedding_fraction) or not (
            0.0 <= max_load_shedding_fraction < 1.0
        ):
            raise ValueError("max_load_shedding_fraction must be in [0, 1)")
        if not isfinite(load_shedding_step) or not (0.0 < load_shedding_step <= 1.0):
            raise ValueError("load_shedding_step must be in (0, 1]")
        if not isfinite(initial_load_reduction) or not (0.0 < initial_load_reduction <= 1.0):
            raise ValueError("initial_load_reduction must be in (0, 1]")
        if not isfinite(minimum_load_step) or not (0.0 < minimum_load_step <= load_shedding_step):
            raise ValueError("minimum_load_step must be in (0, load_shedding_step]")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")

        self.filename = Path(filename)
        if not self.filename.is_file():
            raise FileNotFoundError(self.filename)
        self.solved_filename = (
            Path(solved_filename)
            if solved_filename is not None
            else self.filename.with_name(f"{self.filename.stem}-solved.dat")
        )
        if self.solved_filename.resolve() == self.filename.resolve():
            raise ValueError("solved_filename must not overwrite the source case")

        self.max_load_shedding_fraction = float(max_load_shedding_fraction)
        self.initial_load_reduction = float(initial_load_reduction)
        self.load_shedding_step = float(load_shedding_step)
        self.minimum_load_step = float(minimum_load_step)
        self.max_attempts = int(max_attempts)
        self.tolerance = tolerance
        self.max_iterations = max_iterations
        self.max_control_passes = int(max_control_passes)
        self.enhancement = bool(enhancement)
        self.print_iterations = bool(print_iterations)

        self._source_tables = parse_dat_file(self.filename)
        self._base_loads = {
            int(float(record["No"])): (
                float(record.get("Pl", 0.0)),
                float(record.get("Ql", 0.0)),
            )
            for record in self._source_tables.get("DBAR", [])
            if record.get("E", "L").upper() != "D" and record.get("O", "A").upper() == "A"
        }
        self._base_case = build_power_flow_case(self._source_tables)
        self.case = deepcopy(self._base_case)
        self.result: RobustPowerFlowResult | None = None
        self.restoration_solver: NewtonRaphsonPowerFlow | None = None
        self.fast_decoupled_solver: FastDecoupledPowerFlow | None = None

    def solve(self) -> RobustPowerFlowResult:
        """Find the highest tested retained load level and run fast decoupled."""
        best_case: PowerFlowCase | None = None
        best_result: PowerFlowResult | None = None
        best_factor: float | None = None
        best_fast_decoupled_iterations = 0
        last_fast_decoupled_iterations = 0
        last_case = deepcopy(self._base_case)
        last_result: PowerFlowResult | None = None
        last_factor = 1.0
        working_case = deepcopy(self._base_case)
        working_voltage: np.ndarray | None = None
        factor = 1.0
        step = self.load_shedding_step
        attempts = 0
        restoration_iterations = 0
        full_load_converged = False

        while attempts < self.max_attempts:
            if best_case is not None and best_factor is not None and factor > best_factor:
                attempt_case = deepcopy(best_case)
                seed = best_result.voltage if best_result is not None else None
            else:
                attempt_case = deepcopy(working_case)
                seed = working_voltage

            self._apply_load_factor(attempt_case, factor)
            if seed is not None:
                self._set_voltage(attempt_case, seed)
            if self.print_iterations:
                print(
                    f"\nRobust restoration attempt {attempts + 1}: "
                    f"{factor:.2%} of original DBAR load"
                )

            newton = NewtonRaphsonPowerFlow(
                self.filename,
                tolerance=self.tolerance,
                max_iterations=self.max_iterations,
                max_control_passes=self.max_control_passes,
                control_method="sequential",
                print_iterations=self.print_iterations,
            )
            newton.case = attempt_case
            newton_result = newton.solve()
            self.restoration_solver = newton
            attempts += 1
            restoration_iterations += newton_result.iterations
            attempt_case = newton.case
            self._set_voltage(attempt_case, newton_result.voltage)
            candidate_result = newton_result
            candidate_converged = newton_result.converged

            if newton_result.converged:
                fast_decoupled = FastDecoupledPowerFlow(
                    self.filename,
                    enhancement=self.enhancement,
                    warm_start=False,
                    tolerance=self.tolerance,
                    max_iterations=self.max_iterations,
                    max_control_passes=self.max_control_passes,
                    print_iterations=self.print_iterations,
                )
                fast_decoupled.case = deepcopy(attempt_case)
                fast_decoupled_result = fast_decoupled.solve()
                self.fast_decoupled_solver = fast_decoupled
                last_fast_decoupled_iterations = fast_decoupled_result.iterations
                candidate_result = fast_decoupled_result
                attempt_case = fast_decoupled.case
                self._set_voltage(attempt_case, fast_decoupled_result.voltage)
                candidate_converged = fast_decoupled_result.converged
            else:
                self.fast_decoupled_solver = None

            last_case = deepcopy(attempt_case)
            last_result = candidate_result
            last_factor = factor

            if candidate_converged:
                best_case = deepcopy(attempt_case)
                best_result = candidate_result
                best_factor = factor
                best_fast_decoupled_iterations = candidate_result.iterations
                working_case = deepcopy(best_case)
                working_voltage = best_result.voltage.copy()
                if factor >= 1.0 - 1e-12:
                    full_load_converged = True
                    break
                factor = min(1.0, best_factor + step)
                continue

            if best_factor is None and attempts == 1 and self.max_load_shedding_fraction > 0.0:
                reduction = min(
                    self.initial_load_reduction,
                    self.max_load_shedding_fraction,
                )
                factor = 1.0 - reduction
                working_case = deepcopy(self._base_case)
                working_voltage = None
                continue

            if best_factor is None:
                next_factor = max(
                    1.0 - self.max_load_shedding_fraction,
                    factor - step,
                )
                if next_factor >= factor - 1e-12:
                    break
                factor = next_factor
                working_case = deepcopy(attempt_case)
                working_voltage = candidate_result.voltage.copy()
                continue

            step *= 0.5
            if step < self.minimum_load_step:
                break
            factor = min(1.0, best_factor + step)
            working_case = deepcopy(best_case)
            working_voltage = best_result.voltage.copy() if best_result is not None else None

        if best_case is not None and best_result is not None and best_factor is not None:
            self.case = best_case
            solved_case_path = self._write_solved_case(best_factor, best_result)
            result = best_result
            load_factor = best_factor
            failure_reason = None
        else:
            self.case = last_case
            solved_case_path = None
            result = last_result or PowerFlowResult(
                converged=False,
                iterations=0,
                max_mismatch=float("inf"),
                voltage=self._base_case.initial_voltage,
                buses=[],
                failure_reason="No restoration attempt was completed",
            )
            load_factor = last_factor
            failure_reason = (
                f"No Newton plus fast-decoupled solution found within "
                f"{self.max_load_shedding_fraction:.1%} load shedding; "
                f"last trial retained {last_factor:.2%} of DBAR load: "
                f"{result.failure_reason or 'residual exceeds tolerance'}"
            )

        self.result = RobustPowerFlowResult(
            converged=bool(best_result is not None and best_factor is not None),
            iterations=result.iterations,
            max_mismatch=result.max_mismatch,
            voltage=result.voltage.copy(),
            buses=list(result.buses),
            failure_reason=failure_reason,
            load_factor=load_factor,
            load_shed_active_mw=self._shed_total("active", load_factor),
            load_shed_reactive_mvar=self._shed_total("reactive", load_factor),
            restoration_attempts=attempts,
            restoration_iterations=restoration_iterations,
            fast_decoupled_iterations=(
                best_fast_decoupled_iterations
                if best_result is not None
                else last_fast_decoupled_iterations  # noqa: E501
            ),
            full_load_converged=full_load_converged,
            solved_case_path=solved_case_path,
        )
        return self.result

    def _apply_load_factor(self, case: PowerFlowCase, factor: float) -> None:
        rectifier_p: dict[int, float] = {}
        converter_q: dict[int, float] = {}
        for link in case.hvdc_links:
            rectifier_p[link.rectifier_bus] = (
                rectifier_p.get(link.rectifier_bus, 0.0) + link.rectifier_active_mw
            )
            converter_q[link.rectifier_bus] = (
                converter_q.get(link.rectifier_bus, 0.0) + link.rectifier_reactive_mvar
            )
            converter_q[link.inverter_bus] = (
                converter_q.get(link.inverter_bus, 0.0) + link.inverter_reactive_mvar
            )

        for bus in case.buses:
            active_load, reactive_load = self._base_loads.get(bus.number, (0.0, 0.0))
            bus.p_load_mw = self._scaled_positive_load(active_load, factor) + rectifier_p.get(
                bus.number, 0.0
            )
            bus.q_load_mvar = self._scaled_positive_load(reactive_load, factor) + converter_q.get(
                bus.number, 0.0
            )

    @staticmethod
    def _scaled_positive_load(load: float, factor: float) -> float:
        return load * factor if load > 0.0 else load

    def _set_voltage(self, case: PowerFlowCase, voltage: np.ndarray) -> None:
        if voltage.shape != (len(case.buses),) or not np.isfinite(voltage).all():
            raise ValueError("A restoration voltage seed must contain one finite value per bus")
        for bus, value in zip(case.buses, voltage, strict=True):
            bus.voltage_pu = float(abs(value))
            bus.angle_radians = float(np.angle(value))

    def _shed_total(self, kind: str, factor: float) -> float:
        position = 0 if kind == "active" else 1
        return sum(max(loads[position], 0.0) for loads in self._base_loads.values()) * (
            1.0 - factor
        )

    def _write_solved_case(self, factor: float, result: PowerFlowResult) -> Path:
        tables = deepcopy(self._source_tables)
        records = tables.get("DBAR")
        if not records:
            raise ValueError("The source case has no DBAR block to write")
        solutions = {bus.number: bus for bus in result.buses}
        active_bus_numbers = set(self._base_loads)

        for record in records:
            number = int(float(record["No"]))
            if number in active_bus_numbers:
                active_load, reactive_load = self._base_loads[number]
                record["Pl"] = self._scaled_positive_load(active_load, factor)
                record["Ql"] = self._scaled_positive_load(reactive_load, factor)
            solution = solutions.get(number)
            if solution is not None:
                record["V"] = solution.voltage_pu
                record["A0"] = solution.angle_degrees
            for field, converter in _NUMERIC_DBAR_FIELDS.items():
                value = record.get(field)
                if value in (None, ""):
                    continue
                try:
                    number_value = float(value)
                except ValueError:
                    continue
                record[field] = int(number_value) if converter is int else number_value

        source_text = self._read_source_text()
        matches = list(
            re.finditer(r"^param:\s*DBAR\s*:", source_text, flags=re.IGNORECASE | re.MULTILINE)
        )
        if len(matches) != 1:
            raise ValueError("The source case must contain exactly one DBAR block to write")
        block_start = matches[0].start()
        block_end = source_text.find(";", matches[0].end())
        if block_end < 0:
            raise ValueError("The source DBAR block has no ';' terminator")
        replacement = render_dat({"DBAR": records}).rstrip()
        updated_text = (
            f"# RobustACPowerFlow result; retained {factor:.4%} of positive DBAR loads.\n"
            + source_text[:block_start]
            + replacement
            + source_text[block_end + 1 :]
        )
        self.solved_filename.parent.mkdir(parents=True, exist_ok=True)
        self.solved_filename.write_text(updated_text, encoding="utf-8")
        return self.solved_filename

    def _read_source_text(self) -> str:
        try:
            return self.filename.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return self.filename.read_text(encoding="latin-1")


__all__ = ["RobustACPowerFlow", "RobustPowerFlowResult"]
