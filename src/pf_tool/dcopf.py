"""PTDF-based DC optimal power flow with least-redispatch objective."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import coo_matrix, csr_matrix, eye, hstack, vstack

from pf_tool.dc_power_flow import DCBranchFlow
from pf_tool.ptdf import PTDFMatrix, build_ptdf
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case

_UNLIMITED_RATING_MVA = 9999.0
_FEASIBILITY_TOLERANCE_MW = 1e-5


@dataclass(slots=True)
class DCOptimalPowerFlowResult:
    """DC-OPF schedule, objective, line flows, and solver status."""

    converged: bool
    objective_value_mw: float | None
    generation_mw: dict[int, float] | None
    redispatch_mw: dict[int, float] | None
    branch_flows: list[DCBranchFlow] | None
    max_balance_error_mw: float | None
    max_branch_violation_mw: float | None
    iterations: int
    reference_buses: tuple[int, ...]
    termination_condition: str
    failure_reason: str | None = None
    balance_slack_mw: dict[int, float] | None = None
    load_shedding_mw: dict[int, float] | None = None
    generation_curtailment_mw: dict[int, float] | None = None


@dataclass(slots=True)
class _DispatchGenerator:
    bus_number: int
    bus_index: int
    scheduled_mw: float
    minimum_mw: float
    maximum_mw: float


@dataclass(slots=True)
class _FlexibleBusPower:
    bus_number: int
    bus_index: int
    maximum_mw: float


class DCOptimalPowerFlow:
    """Solve a PTDF-based DC-OPF for an ANAREDE ``.dat`` case.

    The cases do not provide generator production costs. The default objective
    is therefore minimum absolute redispatch, ``min sum(abs(Pg - Pg0))``:
    satisfy system balance and branch limits while moving the existing
    dispatch as little as possible. This is a feasibility/redispatch objective,
    not an economic cost-minimizing objective.

    Generators with valid DGER active-power bounds are dispatchable. Scheduled
    positive generation at slack/PV buses without bounds can only be curtailed
    to zero. If the PTDF dispatch LP is infeasible, a sparse angle-formulation
    fallback minimizes, in order, reference adjustment, load shedding, fixed
    generation curtailment, and generator redispatch. Each island's reference
    bus may absorb a bounded active-power mismatch; the default limit is 1%
    of the case base MVA. Other injections, including LCC terminal powers,
    remain fixed. Branch ratings between zero and 9999 MVA are treated as DC
    active-power limits in MW.
    """

    def __init__(
        self,
        filename: str | Path,
        *,
        reference_bus: int | None = None,
        balance_slack_limit_mw: float | None = None,
        max_load_shedding_fraction: float = 0.2,
        print_iterations: bool = False,
    ) -> None:
        self.filename = Path(filename)
        tables = parse_dat_file(self.filename)
        self.case = build_power_flow_case(tables)
        if reference_bus is not None and reference_bus not in self.case.bus_index:
            raise ValueError(f"Reference bus {reference_bus} is not in service")
        if balance_slack_limit_mw is None:
            balance_slack_limit_mw = 0.01 * self.case.base_mva
        if not np.isfinite(balance_slack_limit_mw) or balance_slack_limit_mw < 0.0:
            raise ValueError("balance_slack_limit_mw must be finite and non-negative")
        if (
            not np.isfinite(max_load_shedding_fraction)
            or not 0.0 <= max_load_shedding_fraction <= 1.0
        ):
            raise ValueError("max_load_shedding_fraction must be between 0 and 1")
        self.reference_bus = reference_bus
        self.balance_slack_limit_mw = float(balance_slack_limit_mw)
        self.max_load_shedding_fraction = float(max_load_shedding_fraction)
        self.print_iterations = bool(print_iterations)
        self.result: DCOptimalPowerFlowResult | None = None

    def solve(self) -> DCOptimalPowerFlowResult:
        """Minimize total redispatch subject to balance and PTDF line limits."""
        case = self.case
        generators = self._dispatch_generators()
        curtailable_generation = self._curtailable_generation(generators)
        shedable_load = self._shedable_load()
        injection_buses = [generator.bus_number for generator in generators]
        ptdf = build_ptdf(case, injection_buses, reference_bus=self.reference_bus)
        base_net_injection_mw = case.specified_power.real * case.base_mva
        dispatch_count = len(generators)
        island_labels = ptdf.island_labels
        island_count = len(ptdf.reference_buses)

        balance_matrix = np.zeros((island_count, dispatch_count), dtype=np.float64)
        for generator_index, generator in enumerate(generators):
            balance_matrix[island_labels[generator.bus_index], generator_index] = 1.0
        balance_rhs = np.zeros(island_count, dtype=np.float64)
        for island in range(island_count):
            members = np.flatnonzero(island_labels == island)
            balance_rhs[island] = -float(np.sum(base_net_injection_mw[members]))
            island_generators = [
                generator
                for generator in generators
                if island_labels[generator.bus_index] == island
            ]
            minimum_redispatch = sum(
                generator.minimum_mw - generator.scheduled_mw for generator in island_generators
            )
            maximum_redispatch = sum(
                generator.maximum_mw - generator.scheduled_mw for generator in island_generators
            )
            island_curtailment = sum(
                item.maximum_mw
                for item in curtailable_generation
                if island_labels[item.bus_index] == island
            )
            island_shedding = sum(
                item.maximum_mw for item in shedable_load if island_labels[item.bus_index] == island
            )
            minimum_redispatch -= island_curtailment
            maximum_redispatch += island_shedding
            required_redispatch = balance_rhs[island]
            if (
                required_redispatch
                < minimum_redispatch - self.balance_slack_limit_mw - _FEASIBILITY_TOLERANCE_MW
                or required_redispatch
                > maximum_redispatch + self.balance_slack_limit_mw + _FEASIBILITY_TOLERANCE_MW
            ):
                reference = ptdf.reference_buses[island]
                reason = (
                    f"Island with reference bus {reference} requires "
                    f"{required_redispatch:+.6g} MW of redispatch, outside the "
                    f"generator range [{minimum_redispatch:+.6g}, "
                    f"{maximum_redispatch:+.6g}] MW including allowed load "
                    f"shedding/curtailment plus the allowed "
                    f"reference-bus adjustment of +/-{self.balance_slack_limit_mw:.6g} MW"
                )
                self.result = DCOptimalPowerFlowResult(
                    converged=False,
                    objective_value_mw=None,
                    generation_mw=None,
                    redispatch_mw=None,
                    branch_flows=None,
                    max_balance_error_mw=None,
                    max_branch_violation_mw=None,
                    iterations=0,
                    reference_buses=ptdf.reference_buses,
                    termination_condition=reason,
                    failure_reason=reason,
                )
                self._print_result()
                return self.result

        constrained_branches = [
            index
            for index, branch in enumerate(case.branches)
            if 0.0 < branch.rating_mva < _UNLIMITED_RATING_MVA
        ]
        line_sensitivities = csr_matrix(ptdf.matrix[constrained_branches, :])
        line_limits = np.asarray(
            [case.branches[index].rating_mva for index in constrained_branches],
            dtype=np.float64,
        )
        base_line_flows = ptdf.base_flows_mw[constrained_branches]

        if dispatch_count == 0:
            fixed_result = self._solve_without_dispatch(
                ptdf,
                base_net_injection_mw,
                line_limits,
                base_line_flows,
                island_labels,
            )
            if fixed_result.converged:
                return fixed_result
            return self._solve_with_load_shedding(
                ptdf,
                base_net_injection_mw,
                generators,
                curtailable_generation,
                shedable_load,
                fixed_result.failure_reason or "Fixed dispatch is infeasible",
            )

        fixed_overload_reason = self._infeasibility_reason(
            "",
            constrained_branches,
            line_limits,
            base_line_flows,
            line_sensitivities,
        )
        if fixed_overload_reason.startswith("DC-OPF is infeasible"):
            return self._solve_with_load_shedding(
                ptdf,
                base_net_injection_mw,
                generators,
                curtailable_generation,
                shedable_load,
                fixed_overload_reason,
            )

        identity = eye(dispatch_count, format="csr", dtype=np.float64)
        slack_identity = eye(island_count, format="csr", dtype=np.float64)
        zero_line_generators = csr_matrix((len(constrained_branches), dispatch_count))
        zero_line_islands = csr_matrix((len(constrained_branches), island_count))
        zero_generator_islands = csr_matrix((dispatch_count, island_count))
        zero_island_generators = csr_matrix((island_count, dispatch_count))
        zero_islands = csr_matrix((island_count, island_count))
        flow_upper = vstack(
            (
                hstack(
                    (
                        line_sensitivities,
                        zero_line_generators,
                        zero_line_islands,
                        zero_line_islands,
                    ),
                    format="csr",
                ),
                hstack(
                    (
                        -line_sensitivities,
                        zero_line_generators,
                        zero_line_islands,
                        zero_line_islands,
                    ),
                    format="csr",
                ),
            ),
            format="csr",
        )
        absolute_upper = vstack(
            (
                hstack(
                    (identity, -identity, zero_generator_islands, zero_generator_islands),
                    format="csr",
                ),
                hstack(
                    (-identity, -identity, zero_generator_islands, zero_generator_islands),
                    format="csr",
                ),
            ),
            format="csr",
        )
        slack_absolute_upper = vstack(
            (
                hstack(
                    (
                        zero_island_generators,
                        zero_island_generators,
                        slack_identity,
                        -slack_identity,
                    ),
                    format="csr",
                ),
                hstack(
                    (
                        zero_island_generators,
                        zero_island_generators,
                        -slack_identity,
                        -slack_identity,
                    ),
                    format="csr",
                ),
            ),
            format="csr",
        )
        inequality_matrix = vstack((flow_upper, absolute_upper, slack_absolute_upper), format="csr")
        inequality_rhs = np.concatenate(
            (
                line_limits - base_line_flows,
                line_limits + base_line_flows,
                np.zeros(2 * dispatch_count, dtype=np.float64),
                np.zeros(2 * island_count, dtype=np.float64),
            )
        )

        balance_sparse = csr_matrix(balance_matrix)
        equality_matrix = hstack(
            (
                balance_sparse,
                csr_matrix((island_count, dispatch_count)),
                slack_identity,
                zero_islands,
            ),
            format="csr",
        )
        equality_rhs = balance_rhs
        variable_bounds = (
            [
                (
                    generator.minimum_mw - generator.scheduled_mw,
                    generator.maximum_mw - generator.scheduled_mw,
                )
                for generator in generators
            ]
            + [(0.0, None)] * dispatch_count
            + [(-self.balance_slack_limit_mw, self.balance_slack_limit_mw)] * island_count
            + [(0.0, None)] * island_count
        )
        slack_objective = np.concatenate(
            (
                np.zeros(2 * dispatch_count + island_count, dtype=np.float64),
                np.ones(island_count, dtype=np.float64),
            )
        )
        objective = np.concatenate(
            (
                np.zeros(dispatch_count, dtype=np.float64),
                np.ones(dispatch_count, dtype=np.float64),
                np.zeros(2 * island_count, dtype=np.float64),
            )
        )
        slack_optimization = linprog(
            slack_objective,
            A_ub=inequality_matrix,
            b_ub=inequality_rhs,
            A_eq=equality_matrix,
            b_eq=equality_rhs,
            bounds=variable_bounds,
            method="highs",
        )
        if not slack_optimization.success or slack_optimization.x is None:
            reason = self._infeasibility_reason(
                slack_optimization.message,
                constrained_branches,
                line_limits,
                base_line_flows,
                line_sensitivities,
            )
            return self._solve_with_load_shedding(
                ptdf,
                base_net_injection_mw,
                generators,
                curtailable_generation,
                shedable_load,
                reason,
            )

        minimum_slack = float(np.sum(slack_optimization.x[2 * dispatch_count + island_count :]))
        slack_objective_row = csr_matrix(
            (
                np.ones(island_count, dtype=np.float64),
                (
                    np.zeros(island_count, dtype=np.int64),
                    np.arange(
                        2 * dispatch_count + island_count,
                        2 * dispatch_count + 2 * island_count,
                    ),
                ),
            ),
            shape=(1, 2 * dispatch_count + 2 * island_count),
        )
        redispatch_inequality = vstack((inequality_matrix, slack_objective_row), format="csr")
        redispatch_rhs = np.concatenate((inequality_rhs, [minimum_slack]))
        optimization = linprog(
            objective,
            A_ub=redispatch_inequality,
            b_ub=redispatch_rhs,
            A_eq=equality_matrix,
            b_eq=equality_rhs,
            bounds=variable_bounds,
            method="highs",
        )
        if not optimization.success or optimization.x is None:
            reason = self._infeasibility_reason(
                optimization.message,
                constrained_branches,
                line_limits,
                base_line_flows,
                line_sensitivities,
            )
            return self._solve_with_load_shedding(
                ptdf,
                base_net_injection_mw,
                generators,
                curtailable_generation,
                shedable_load,
                reason,
            )

        delta_generation = np.asarray(optimization.x[:dispatch_count], dtype=np.float64)
        balance_slack_values = np.asarray(
            optimization.x[2 * dispatch_count : 2 * dispatch_count + island_count],
            dtype=np.float64,
        )
        balance_slack_mw = {
            reference: float(value)
            for reference, value in zip(ptdf.reference_buses, balance_slack_values, strict=True)
        }
        generation_mw = {bus.number: float(bus.p_generation_mw) for bus in case.buses}
        redispatch_mw: dict[int, float] = {}
        for generator, delta in zip(generators, delta_generation, strict=True):
            redispatch_mw[generator.bus_number] = float(delta)
            generation_mw[generator.bus_number] += float(delta)

        branch_flow_values = ptdf.base_flows_mw + ptdf.matrix @ delta_generation
        branch_flows = [
            DCBranchFlow(
                from_bus=branch.from_bus,
                to_bus=branch.to_bus,
                circuit=branch.circuit,
                active_power_mw=float(flow),
            )
            for branch, flow in zip(case.branches, branch_flow_values, strict=True)
        ]
        balance_error = self._balance_error(
            base_net_injection_mw,
            delta_generation,
            generators,
            island_labels,
            balance_slack_mw,
        )
        branch_violation = self._maximum_branch_violation(branch_flow_values)
        feasible = (
            balance_error <= _FEASIBILITY_TOLERANCE_MW
            and branch_violation <= _FEASIBILITY_TOLERANCE_MW
        )
        failure_reason = (
            None
            if feasible
            else "The LP solution exceeds the post-solve balance or branch-limit tolerance"
        )
        self.result = DCOptimalPowerFlowResult(
            converged=feasible,
            objective_value_mw=float(np.sum(np.abs(delta_generation))),
            generation_mw=generation_mw,
            redispatch_mw=redispatch_mw,
            branch_flows=branch_flows,
            max_balance_error_mw=balance_error,
            max_branch_violation_mw=branch_violation,
            iterations=(
                int(getattr(slack_optimization, "nit", 0) or 0)
                + int(getattr(optimization, "nit", 0) or 0)
            ),
            reference_buses=ptdf.reference_buses,
            termination_condition=str(optimization.message),
            failure_reason=failure_reason,
            balance_slack_mw=balance_slack_mw,
        )
        self._print_result()
        return self.result

    def get_curtailed_generators(self) -> pd.DataFrame:
        """Return a bus-level dataframe of generation reductions in the solution."""
        result = self._require_feasible_result()
        generation_after = result.generation_mw
        if generation_after is None:
            raise RuntimeError("The feasible DC-OPF result has no generation schedule")

        columns = (
            "bus_number",
            "bus_name",
            "generation_before_mw",
            "generation_after_mw",
            "curtailed_mw",
        )
        rows = []
        for bus in self.case.buses:
            before = float(bus.p_generation_mw)
            after = float(generation_after[bus.number])
            curtailed = before - after
            if curtailed > _FEASIBILITY_TOLERANCE_MW:
                rows.append(
                    {
                        "bus_number": bus.number,
                        "bus_name": bus.name,
                        "generation_before_mw": before,
                        "generation_after_mw": after,
                        "curtailed_mw": curtailed,
                    }
                )
        return pd.DataFrame(rows, columns=columns)

    def get_shed_loads(self) -> pd.DataFrame:
        """Return a bus-level dataframe of load reductions in the solution."""
        result = self._require_feasible_result()
        shed_by_bus = result.load_shedding_mw or {}
        columns = (
            "bus_number",
            "bus_name",
            "load_before_mw",
            "load_after_mw",
            "shed_mw",
        )
        rows = []
        for bus in self.case.buses:
            shed = shed_by_bus.get(bus.number, 0.0)
            if shed > _FEASIBILITY_TOLERANCE_MW:
                before = float(bus.p_load_mw)
                rows.append(
                    {
                        "bus_number": bus.number,
                        "bus_name": bus.name,
                        "load_before_mw": before,
                        "load_after_mw": before - shed,
                        "shed_mw": shed,
                    }
                )
        return pd.DataFrame(rows, columns=columns)

    def _require_feasible_result(self) -> DCOptimalPowerFlowResult:
        if self.result is None:
            raise RuntimeError("Call solve() before requesting adjustment reports")
        if not self.result.converged:
            raise RuntimeError("Adjustment reports require a feasible DC-OPF result")
        return self.result

    def _dispatch_generators(self) -> list[_DispatchGenerator]:
        """Build dispatch variables for generators with active-power limits."""
        case = self.case
        lcc_injection_by_bus: dict[int, float] = {}
        for link in case.hvdc_links:
            lcc_injection_by_bus[link.inverter_bus] = (
                lcc_injection_by_bus.get(link.inverter_bus, 0.0) + link.inverter_active_mw
            )

        generators: list[_DispatchGenerator] = []
        for bus_index, bus in enumerate(case.buses):
            if (
                bus.p_min_mw is not None
                and bus.p_max_mw is not None
                and (not np.isfinite(bus.p_min_mw) or not np.isfinite(bus.p_max_mw))
            ):
                raise ValueError(f"Invalid active-power limits for generator at bus {bus.number}")
            if (
                bus.p_min_mw is not None
                and bus.p_max_mw is not None
                and bus.p_max_mw < bus.p_min_mw
            ):
                raise ValueError(f"Active-power maximum is below minimum at bus {bus.number}")
            scheduled = bus.p_generation_mw - lcc_injection_by_bus.get(bus.number, 0.0)
            if bus.p_min_mw is None or bus.p_max_mw is None or bus.p_max_mw <= bus.p_min_mw:
                continue
            generators.append(
                _DispatchGenerator(
                    bus_number=bus.number,
                    bus_index=bus_index,
                    scheduled_mw=float(scheduled),
                    minimum_mw=float(bus.p_min_mw),
                    maximum_mw=float(bus.p_max_mw),
                )
            )
        return generators

    def _curtailable_generation(
        self, generators: list[_DispatchGenerator]
    ) -> list[_FlexibleBusPower]:
        """Find positive non-LCC generation with no active DGER bounds."""
        generator_buses = {generator.bus_number for generator in generators}
        inverter_power: dict[int, float] = {}
        for link in self.case.hvdc_links:
            inverter_power[link.inverter_bus] = (
                inverter_power.get(link.inverter_bus, 0.0) + link.inverter_active_mw
            )
        return [
            _FlexibleBusPower(
                bus_number=bus.number,
                bus_index=index,
                maximum_mw=bus.p_generation_mw - inverter_power.get(bus.number, 0.0),
            )
            for index, bus in enumerate(self.case.buses)
            if bus.number not in generator_buses
            and bus.p_min_mw is None
            and bus.p_max_mw is None
            and bus.p_generation_mw - inverter_power.get(bus.number, 0.0) > 1e-8
        ]

    def _shedable_load(self) -> list[_FlexibleBusPower]:
        """Find native loads eligible for the configured bounded load shedding."""
        rectifier_power: dict[int, float] = {}
        for link in self.case.hvdc_links:
            rectifier_power[link.rectifier_bus] = (
                rectifier_power.get(link.rectifier_bus, 0.0) + link.rectifier_active_mw
            )
        return [
            _FlexibleBusPower(
                bus_number=bus.number,
                bus_index=index,
                maximum_mw=native_load * self.max_load_shedding_fraction,
            )
            for index, bus in enumerate(self.case.buses)
            if (native_load := bus.p_load_mw - rectifier_power.get(bus.number, 0.0)) > 1e-8
            and self.max_load_shedding_fraction > 0.0
        ]

    def _solve_with_load_shedding(
        self,
        ptdf: PTDFMatrix,
        base_net_injection_mw: np.ndarray,
        generators: list[_DispatchGenerator],
        curtailable_generation: list[_FlexibleBusPower],
        shedable_load: list[_FlexibleBusPower],
        infeasibility_reason: str,
    ) -> DCOptimalPowerFlowResult:
        """Restore feasibility with sparse angle equations and explicit controls."""
        bus_count = len(self.case.buses)
        branch_count = len(self.case.branches)
        island_count = len(ptdf.reference_buses)
        indices = self.case.bus_index
        references = np.asarray(
            [indices[number] for number in ptdf.reference_buses], dtype=np.int64
        )
        reference_set = set(references.tolist())
        free_buses = np.asarray(
            [index for index in range(bus_count) if index not in reference_set],
            dtype=np.int64,
        )
        free_position = np.full(bus_count, -1, dtype=np.int64)
        free_position[free_buses] = np.arange(free_buses.size, dtype=np.int64)
        angle_count = free_buses.size
        angle_start = 0
        flow_start = angle_start + angle_count
        generator_start = flow_start + branch_count
        generator_abs_start = generator_start + len(generators)
        curtail_start = generator_abs_start + len(generators)
        load_shed_start = curtail_start + len(curtailable_generation)
        slack_start = load_shed_start + len(shedable_load)
        slack_abs_start = slack_start + island_count
        variable_count = slack_abs_start + island_count

        from_indices = np.asarray(
            [indices[branch.from_bus] for branch in self.case.branches],
            dtype=np.int64,
        )
        to_indices = np.asarray(
            [indices[branch.to_bus] for branch in self.case.branches],
            dtype=np.int64,
        )
        coefficients = np.asarray(
            [1.0 / (branch.reactance_pu * branch.tap_ratio) for branch in self.case.branches],
            dtype=np.float64,
        )
        phases = np.asarray(
            [branch.phase_shift_radians for branch in self.case.branches],
            dtype=np.float64,
        )
        angle_rows: list[int] = []
        angle_columns: list[int] = []
        angle_values: list[float] = []
        incidence_rows: list[int] = []
        incidence_columns: list[int] = []
        incidence_values: list[float] = []
        for branch_index, (start, end) in enumerate(zip(from_indices, to_indices, strict=True)):
            start_position = free_position[start]
            end_position = free_position[end]
            branch_susceptance_mw = coefficients[branch_index] * self.case.base_mva
            if start_position >= 0:
                angle_rows.append(branch_index)
                angle_columns.append(int(start_position))
                angle_values.append(-branch_susceptance_mw)
                incidence_rows.append(int(start_position))
                incidence_columns.append(branch_index)
                incidence_values.append(1.0)
            if end_position >= 0:
                angle_rows.append(branch_index)
                angle_columns.append(int(end_position))
                angle_values.append(branch_susceptance_mw)
                incidence_rows.append(int(end_position))
                incidence_columns.append(branch_index)
                incidence_values.append(-1.0)

        angle_matrix = coo_matrix(
            (angle_values, (angle_rows, angle_columns)),
            shape=(branch_count, angle_count),
            dtype=np.float64,
        ).tocsr()
        incidence = coo_matrix(
            (incidence_values, (incidence_rows, incidence_columns)),
            shape=(angle_count, branch_count),
            dtype=np.float64,
        ).tocsr()
        generator_matrix = coo_matrix(
            (
                np.ones(len(generators), dtype=np.float64),
                (
                    [generator.bus_index for generator in generators],
                    np.arange(len(generators)),
                ),
            ),
            shape=(bus_count, len(generators)),
            dtype=np.float64,
        ).tocsr()
        curtail_matrix = coo_matrix(
            (
                np.ones(len(curtailable_generation), dtype=np.float64),
                (
                    [item.bus_index for item in curtailable_generation],
                    np.arange(len(curtailable_generation)),
                ),
            ),
            shape=(bus_count, len(curtailable_generation)),
            dtype=np.float64,
        ).tocsr()
        load_matrix = coo_matrix(
            (
                np.ones(len(shedable_load), dtype=np.float64),
                (
                    [item.bus_index for item in shedable_load],
                    np.arange(len(shedable_load)),
                ),
            ),
            shape=(bus_count, len(shedable_load)),
            dtype=np.float64,
        ).tocsr()
        generator_island_matrix = coo_matrix(
            (
                np.ones(len(generators), dtype=np.float64),
                (
                    [ptdf.island_labels[item.bus_index] for item in generators],
                    np.arange(len(generators)),
                ),
            ),
            shape=(island_count, len(generators)),
            dtype=np.float64,
        ).tocsr()
        curtail_island_matrix = coo_matrix(
            (
                np.ones(len(curtailable_generation), dtype=np.float64),
                (
                    [ptdf.island_labels[item.bus_index] for item in curtailable_generation],
                    np.arange(len(curtailable_generation)),
                ),
            ),
            shape=(island_count, len(curtailable_generation)),
            dtype=np.float64,
        ).tocsr()
        load_island_matrix = coo_matrix(
            (
                np.ones(len(shedable_load), dtype=np.float64),
                (
                    [ptdf.island_labels[item.bus_index] for item in shedable_load],
                    np.arange(len(shedable_load)),
                ),
            ),
            shape=(island_count, len(shedable_load)),
            dtype=np.float64,
        ).tocsr()

        zero_branch_generators = csr_matrix((branch_count, len(generators)))
        zero_branch_curtailment = csr_matrix((branch_count, len(curtailable_generation)))
        zero_branch_load = csr_matrix((branch_count, len(shedable_load)))
        zero_branch_islands = csr_matrix((branch_count, island_count))
        branch_equations = hstack(
            (
                angle_matrix,
                eye(branch_count, format="csr", dtype=np.float64),
                zero_branch_generators,
                zero_branch_generators,
                zero_branch_curtailment,
                zero_branch_load,
                zero_branch_islands,
                zero_branch_islands,
            ),
            format="csr",
        )

        zero_node_angles = csr_matrix((angle_count, angle_count))
        zero_node_generator_abs = csr_matrix((angle_count, len(generators)))
        zero_node_islands = csr_matrix((angle_count, island_count))
        node_equations = hstack(
            (
                zero_node_angles,
                incidence,
                -generator_matrix[free_buses],
                zero_node_generator_abs,
                curtail_matrix[free_buses],
                -load_matrix[free_buses],
                zero_node_islands,
                zero_node_islands,
            ),
            format="csr",
        )

        zero_island_angles = csr_matrix((island_count, angle_count))
        zero_island_flows = csr_matrix((island_count, branch_count))
        zero_island_generators = csr_matrix((island_count, len(generators)))
        zero_islands = csr_matrix((island_count, island_count))
        island_equations = hstack(
            (
                zero_island_angles,
                zero_island_flows,
                generator_island_matrix,
                zero_island_generators,
                -curtail_island_matrix,
                load_island_matrix,
                eye(island_count, format="csr", dtype=np.float64),
                zero_islands,
            ),
            format="csr",
        )
        equality_matrix = vstack((branch_equations, node_equations, island_equations), format="csr")
        balance_rhs = np.asarray(
            [
                -float(np.sum(base_net_injection_mw[ptdf.island_labels == island]))
                for island in range(island_count)
            ],
            dtype=np.float64,
        )
        equality_rhs = np.concatenate(
            (
                -coefficients * self.case.base_mva * phases,
                base_net_injection_mw[free_buses],
                balance_rhs,
            )
        )

        variable_bounds: list[tuple[float | None, float | None]] = (
            [(None, None)] * angle_count
            + [
                (
                    -branch.rating_mva,
                    branch.rating_mva,
                )
                if 0.0 < branch.rating_mva < _UNLIMITED_RATING_MVA
                else (None, None)
                for branch in self.case.branches
            ]
            + [
                (
                    generator.minimum_mw - generator.scheduled_mw,
                    generator.maximum_mw - generator.scheduled_mw,
                )
                for generator in generators
            ]
            + [(0.0, None)] * len(generators)
            + [(0.0, item.maximum_mw) for item in curtailable_generation]
            + [(0.0, item.maximum_mw) for item in shedable_load]
            + [(-self.balance_slack_limit_mw, self.balance_slack_limit_mw)] * island_count
            + [(0.0, None)] * island_count
        )
        absolute_rows: list[int] = []
        absolute_columns: list[int] = []
        absolute_values: list[float] = []
        for generator_index in range(len(generators)):
            positive_row = generator_index
            negative_row = len(generators) + generator_index
            absolute_rows.extend((positive_row, positive_row, negative_row, negative_row))
            absolute_columns.extend(
                (
                    generator_start + generator_index,
                    generator_abs_start + generator_index,
                    generator_start + generator_index,
                    generator_abs_start + generator_index,
                )
            )
            absolute_values.extend((1.0, -1.0, -1.0, -1.0))
        slack_row_start = 2 * len(generators)
        for island in range(island_count):
            positive_row = slack_row_start + island
            negative_row = slack_row_start + island_count + island
            absolute_rows.extend((positive_row, positive_row, negative_row, negative_row))
            absolute_columns.extend(
                (
                    slack_start + island,
                    slack_abs_start + island,
                    slack_start + island,
                    slack_abs_start + island,
                )
            )
            absolute_values.extend((1.0, -1.0, -1.0, -1.0))
        absolute_constraints = coo_matrix(
            (absolute_values, (absolute_rows, absolute_columns)),
            shape=(2 * len(generators) + 2 * island_count, variable_count),
            dtype=np.float64,
        ).tocsr()
        absolute_rhs = np.zeros(absolute_constraints.shape[0], dtype=np.float64)

        priorities = [
            (
                "reference-bus adjustment",
                np.arange(slack_abs_start, variable_count, dtype=np.int64),
            ),
            (
                "load shedding",
                np.arange(load_shed_start, slack_start, dtype=np.int64),
            ),
            (
                "fixed-generation curtailment",
                np.arange(curtail_start, load_shed_start, dtype=np.int64),
            ),
            (
                "generator redispatch",
                np.arange(generator_abs_start, curtail_start, dtype=np.int64),
            ),
        ]
        priority_limits: list[csr_matrix] = []
        priority_rhs: list[float] = []
        optimization = None
        total_iterations = 0
        for priority_name, priority in priorities:
            if not priority.size:
                continue
            objective = np.zeros(variable_count, dtype=np.float64)
            objective[priority] = 1.0
            if priority_limits:
                upper_matrix = vstack((absolute_constraints, *priority_limits), format="csr")
                upper_rhs = np.concatenate(
                    (absolute_rhs, np.asarray(priority_rhs, dtype=np.float64))
                )
            else:
                upper_matrix = absolute_constraints
                upper_rhs = absolute_rhs
            optimization = linprog(
                objective,
                A_ub=upper_matrix,
                b_ub=upper_rhs,
                A_eq=equality_matrix,
                b_eq=equality_rhs,
                bounds=variable_bounds,
                method="highs",
            )
            total_iterations += int(getattr(optimization, "nit", 0) or 0)
            if not optimization.success or optimization.x is None:
                reason = (
                    f"{infeasibility_reason} The feasibility-restoration LP also "
                    f"failed while minimizing {priority_name} with up to "
                    f"{self.max_load_shedding_fraction:.0%} load shedding and "
                    f"available fixed-generation curtailment: "
                    f"{optimization.message}"
                )
                self.result = DCOptimalPowerFlowResult(
                    converged=False,
                    objective_value_mw=None,
                    generation_mw=None,
                    redispatch_mw=None,
                    branch_flows=None,
                    max_balance_error_mw=None,
                    max_branch_violation_mw=None,
                    iterations=total_iterations,
                    reference_buses=ptdf.reference_buses,
                    termination_condition=str(optimization.message),
                    failure_reason=reason,
                )
                self._print_result()
                return self.result
            optimal_priority = float(np.sum(optimization.x[priority]))
            priority_limits.append(
                csr_matrix(
                    (
                        np.ones(priority.size, dtype=np.float64),
                        (
                            np.zeros(priority.size, dtype=np.int64),
                            priority,
                        ),
                    ),
                    shape=(1, variable_count),
                )
            )
            priority_rhs.append(optimal_priority + 1e-6)

        assert optimization is not None and optimization.x is not None
        solution = optimization.x
        delta_generation = solution[generator_start:generator_abs_start]
        curtailment_values = solution[curtail_start:load_shed_start]
        shedding_values = solution[load_shed_start:slack_start]
        slack_values = solution[slack_start:slack_abs_start]
        balance_slack_mw = {
            reference: float(value)
            for reference, value in zip(ptdf.reference_buses, slack_values, strict=True)
        }
        generation_curtailment_mw = {
            item.bus_number: float(value)
            for item, value in zip(curtailable_generation, curtailment_values, strict=True)
            if value > _FEASIBILITY_TOLERANCE_MW
        }
        load_shedding_mw = {
            item.bus_number: float(value)
            for item, value in zip(shedable_load, shedding_values, strict=True)
            if value > _FEASIBILITY_TOLERANCE_MW
        }
        generation_mw = {bus.number: float(bus.p_generation_mw) for bus in self.case.buses}
        redispatch_mw: dict[int, float] = {}
        for generator, delta in zip(generators, delta_generation, strict=True):
            generation_mw[generator.bus_number] += float(delta)
            redispatch_mw[generator.bus_number] = float(delta)
        for item, amount in zip(curtailable_generation, curtailment_values, strict=True):
            generation_mw[item.bus_number] -= float(amount)

        branch_flow_values = solution[flow_start:generator_start]
        branch_flows = [
            DCBranchFlow(
                from_bus=branch.from_bus,
                to_bus=branch.to_bus,
                circuit=branch.circuit,
                active_power_mw=float(flow),
            )
            for branch, flow in zip(self.case.branches, branch_flow_values, strict=True)
        ]
        balance_error = self._balance_error(
            base_net_injection_mw,
            delta_generation,
            generators,
            ptdf.island_labels,
            balance_slack_mw,
            generation_curtailment_mw,
            load_shedding_mw,
        )
        branch_violation = self._maximum_branch_violation(branch_flow_values)
        feasible = (
            balance_error <= _FEASIBILITY_TOLERANCE_MW
            and branch_violation <= _FEASIBILITY_TOLERANCE_MW
        )
        self.result = DCOptimalPowerFlowResult(
            converged=feasible,
            objective_value_mw=float(np.sum(np.abs(delta_generation))),
            generation_mw=generation_mw,
            redispatch_mw=redispatch_mw,
            branch_flows=branch_flows,
            max_balance_error_mw=balance_error,
            max_branch_violation_mw=branch_violation,
            iterations=total_iterations,
            reference_buses=ptdf.reference_buses,
            termination_condition="optimal sparse DC feasibility-restoration LP",
            failure_reason=(
                None
                if feasible
                else "Restoration LP solution exceeds the post-solve feasibility tolerance"
            ),
            balance_slack_mw=balance_slack_mw,
            load_shedding_mw=load_shedding_mw,
            generation_curtailment_mw=generation_curtailment_mw,
        )
        self._print_result()
        return self.result

    def _solve_without_dispatch(
        self,
        ptdf: PTDFMatrix,
        base_net_injection_mw: np.ndarray,
        line_limits: np.ndarray,
        base_line_flows: np.ndarray,
        island_labels: np.ndarray,
    ) -> DCOptimalPowerFlowResult:
        """Report whether a fixed-dispatch case already satisfies the DC model."""
        balance_slack_mw = {
            reference: -float(np.sum(base_net_injection_mw[island_labels == island]))
            for island, reference in enumerate(ptdf.reference_buses)
        }
        balance_error = self._balance_error(
            base_net_injection_mw,
            np.empty(0),
            [],
            island_labels,
            balance_slack_mw,
        )
        branch_violation = float(
            np.max(np.maximum(np.abs(base_line_flows) - line_limits, 0.0), initial=0.0)
        )
        feasible = (
            balance_error <= _FEASIBILITY_TOLERANCE_MW
            and branch_violation <= _FEASIBILITY_TOLERANCE_MW
        )
        reason = (
            None if feasible else "No dispatchable generators are available to restore feasibility"
        )  # noqa: E501
        self.result = DCOptimalPowerFlowResult(
            converged=feasible,
            objective_value_mw=0.0 if feasible else None,
            generation_mw=(
                {bus.number: float(bus.p_generation_mw) for bus in self.case.buses}
                if feasible
                else None
            ),
            redispatch_mw={} if feasible else None,
            branch_flows=(
                [
                    DCBranchFlow(branch.from_bus, branch.to_bus, branch.circuit, float(flow))
                    for branch, flow in zip(self.case.branches, ptdf.base_flows_mw, strict=True)
                ]
                if feasible
                else None
            ),
            max_balance_error_mw=balance_error,
            max_branch_violation_mw=branch_violation,
            iterations=0,
            reference_buses=ptdf.reference_buses,
            termination_condition="fixed dispatch feasibility check",
            failure_reason=reason,
            balance_slack_mw=balance_slack_mw if feasible else None,
        )
        self._print_result()
        return self.result

    def _balance_error(
        self,
        base_net_injection_mw: np.ndarray,
        delta_generation: np.ndarray,
        generators: list[_DispatchGenerator],
        island_labels: np.ndarray,
        balance_slack_mw: dict[int, float],
        generation_curtailment_mw: dict[int, float] | None = None,
        load_shedding_mw: dict[int, float] | None = None,
    ) -> float:
        injection = base_net_injection_mw.copy()
        for generator, delta in zip(generators, delta_generation, strict=True):
            injection[generator.bus_index] += float(delta)
        for bus_number, adjustment in balance_slack_mw.items():
            injection[self.case.bus_index[bus_number]] += adjustment
        for bus_number, amount in (generation_curtailment_mw or {}).items():
            injection[self.case.bus_index[bus_number]] -= amount
        for bus_number, amount in (load_shedding_mw or {}).items():
            injection[self.case.bus_index[bus_number]] += amount
        island_count = int(np.max(island_labels, initial=-1)) + 1
        return max(
            (
                abs(float(np.sum(injection[island_labels == island])))
                for island in range(island_count)
            ),
            default=0.0,
        )

    def _maximum_branch_violation(self, branch_flows_mw: np.ndarray) -> float:
        violations = [
            max(abs(float(flow)) - branch.rating_mva, 0.0)
            for branch, flow in zip(self.case.branches, branch_flows_mw, strict=True)
            if 0.0 < branch.rating_mva < _UNLIMITED_RATING_MVA
        ]
        return max(violations, default=0.0)

    def _infeasibility_reason(
        self,
        solver_message: str,
        constrained_branches: list[int],
        line_limits: np.ndarray,
        base_line_flows: np.ndarray,
        line_sensitivities: csr_matrix,
    ) -> str:
        fixed_overloads: list[tuple[float, int, float, float]] = []
        for row, branch_index in enumerate(constrained_branches):
            overload = abs(float(base_line_flows[row])) - float(line_limits[row])
            if overload <= _FEASIBILITY_TOLERANCE_MW:
                continue
            sensitivity = line_sensitivities.getrow(row).data
            if np.max(np.abs(sensitivity), initial=0.0) <= 1e-10:
                branch = self.case.branches[branch_index]
                fixed_overloads.append(
                    (overload, branch_index, float(base_line_flows[row]), branch.rating_mva)
                )
        if not fixed_overloads:
            return str(solver_message)
        fixed_overloads.sort(reverse=True)
        details = "; ".join(
            f"{self.case.branches[index].from_bus}-{self.case.branches[index].to_bus} "
            f"({flow:+.1f}/{rating:.1f} MW)"
            for _, index, flow, rating in fixed_overloads[:5]
        )
        return (
            f"DC-OPF is infeasible: {len(fixed_overloads)} already-overloaded "
            f"branch(es) have zero sensitivity to the available generator redispatch. "
            f"Examples (flow/rating): {details}. Load shedding, additional local "
            f"generation control, or relaxed branch ratings are required."
        )

    def _print_result(self) -> None:
        if not self.print_iterations or self.result is None:
            return
        result = self.result
        print(f"PTDF DC optimal power flow: {self.filename.name}")
        if result.redispatch_mw is not None:
            print(f"  Dispatchable generators: {len(result.redispatch_mw)}")
        print(f"  Reference buses: {', '.join(map(str, result.reference_buses))}")
        print(f"  LP iterations: {result.iterations}")
        if result.converged and result.objective_value_mw is not None:
            print(f"  Converged; total absolute redispatch: {result.objective_value_mw:.3f} MW")
        else:
            print(f"  Did not converge: {result.failure_reason}")


__all__ = ["DCOptimalPowerFlow", "DCOptimalPowerFlowResult"]
