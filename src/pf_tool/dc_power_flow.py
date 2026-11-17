"""Linear DC power-flow solver based on the B-prime equations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

from pf_tool.models.bus import BusType
from pf_tool.newton_raphson import BusSolution, PowerFlowResult
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case


@dataclass(slots=True)
class DCBranchFlow:
    """Active-power flow on a branch, positive from ``from_bus`` to ``to_bus``."""

    from_bus: int
    to_bus: int
    circuit: int
    active_power_mw: float


@dataclass(slots=True)
class DCPowerFlowResult(PowerFlowResult):
    """DC solution with unwrapped angles, branch flows, and island references."""

    angles_radians: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    branch_flows: list[DCBranchFlow] = field(default_factory=list)
    reference_buses: tuple[int, ...] = ()


class DCPowerFlow:
    """Solve the linear DC power-flow equations for an ANAREDE ``.dat`` case.

    The model follows the DC approximation: zero series resistance, unit bus
    voltage magnitudes, small angle differences, and no shunt contribution to
    active power. Parsed transformer taps and phase shifts are retained. The
    reference bus in each island balances that island's active-power mismatch.

    ``reference_bus`` optionally selects the reference bus in its island;
    other islands use their DBAR slack bus, or the largest-generation bus if
    no slack is present. ``tolerance`` is the maximum non-reference active
    power mismatch in per unit and defaults to DCTE ``TEPA`` on the case base.
    """

    def __init__(
        self,
        filename: str | Path,
        *,
        tolerance: float | None = None,
        reference_bus: int | None = None,
        print_iterations: bool = False,
    ) -> None:
        self.filename = Path(filename)
        tables = parse_dat_file(self.filename)
        self.case = build_power_flow_case(tables)
        constants = {
            record.get("Mn", "").upper(): float(record.get("Val", 0.0))
            for record in tables.get("DCTE", [])
        }
        if tolerance is None:
            tolerance = constants.get("TEPA", 0.01) / self.case.base_mva
        if tolerance <= 0.0:
            raise ValueError("tolerance must be greater than zero")
        if reference_bus is not None and reference_bus not in self.case.bus_index:
            raise ValueError(f"Reference bus {reference_bus} is not in service")
        self.tolerance = float(tolerance)
        self.reference_bus = reference_bus
        self.print_iterations = bool(print_iterations)
        self.result: DCPowerFlowResult | None = None

    def solve(self) -> DCPowerFlowResult:
        """Solve bus angles and return the active-power flow on every branch."""
        case = self.case
        indices = case.bus_index
        size = len(case.buses)
        rows: list[int] = []
        columns: list[int] = []
        values: list[float] = []
        graph_rows: list[int] = []
        graph_columns: list[int] = []
        branch_coefficients: list[float] = []
        branch_indices: list[tuple[int, int]] = []
        phase_rhs = np.zeros(size, dtype=np.float64)

        for branch in case.branches:
            start = indices[branch.from_bus]
            end = indices[branch.to_bus]
            reactance = branch.reactance_pu
            if not np.isfinite(reactance) or abs(reactance) <= 1e-12:
                raise ValueError(
                    f"DC power flow cannot model zero reactance on branch "
                    f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
                )
            if not np.isfinite(branch.tap_ratio) or branch.tap_ratio <= 0.0:
                raise ValueError(
                    f"Invalid tap ratio on branch "
                    f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
                )
            coefficient = 1.0 / (reactance * branch.tap_ratio)
            if not np.isfinite(coefficient):
                raise ValueError(
                    f"Invalid DC branch susceptance on "
                    f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
                )
            phase = branch.phase_shift_radians
            rows.extend((start, start, end, end))
            columns.extend((start, end, start, end))
            values.extend((coefficient, -coefficient, -coefficient, coefficient))
            graph_rows.extend((start, end))
            graph_columns.extend((end, start))
            phase_rhs[start] += coefficient * phase
            phase_rhs[end] -= coefficient * phase
            branch_coefficients.append(coefficient)
            branch_indices.append((start, end))

        susceptance = coo_matrix(
            (values, (rows, columns)), shape=(size, size), dtype=np.float64
        ).tocsc()
        graph = coo_matrix(
            (np.ones(len(graph_rows), dtype=np.int8), (graph_rows, graph_columns)),
            shape=(size, size),
        ).tocsr()
        island_count, islands = connected_components(graph, directed=False)
        reference_indices = self._reference_indices(island_count, islands)
        reference_set = set(reference_indices)
        free = np.array([index for index in range(size) if index not in reference_set])

        specified = case.specified_power.real
        right_hand_side = specified + phase_rhs
        angles = np.zeros(size, dtype=np.float64)
        if free.size:
            reduced = susceptance[free][:, free].tocsc()
            try:
                angles[free] = splu(reduced).solve(right_hand_side[free])
            except RuntimeError as error:
                raise ValueError(
                    "The reduced DC susceptance matrix is singular; check network islands "
                    "and branch reactances"
                ) from error
        if not np.isfinite(angles).all():
            raise RuntimeError("DC power flow produced non-finite bus angles")

        calculated = susceptance @ angles - phase_rhs
        mismatch = calculated - specified
        mismatch[reference_indices] = 0.0
        maximum_mismatch = float(np.max(np.abs(mismatch), initial=0.0))
        converged = maximum_mismatch <= self.tolerance
        failure_reason = None if converged else "DC power flow did not converge"

        voltage = np.exp(1j * angles)
        case.solved_voltage = voltage.copy()
        base_mva = case.base_mva
        buses = [
            BusSolution(
                number=bus.number,
                name=bus.name,
                voltage_pu=1.0,
                angle_degrees=float(np.rad2deg(angles[index])),
                active_injection_mw=float(calculated[index] * base_mva),
                reactive_injection_mvar=0.0,
            )
            for index, bus in enumerate(case.buses)
        ]
        branch_flows = [
            DCBranchFlow(
                from_bus=branch.from_bus,
                to_bus=branch.to_bus,
                circuit=branch.circuit,
                active_power_mw=float(
                    coefficient
                    * (angles[start] - angles[end] - branch.phase_shift_radians)
                    * base_mva
                ),
            )
            for branch, coefficient, (start, end) in zip(
                case.branches, branch_coefficients, branch_indices
            )
        ]
        self.result = DCPowerFlowResult(
            converged=converged,
            iterations=int(bool(free.size)),
            max_mismatch=maximum_mismatch,
            voltage=voltage,
            buses=buses,
            failure_reason=failure_reason,
            angles_radians=angles,
            branch_flows=branch_flows,
            reference_buses=tuple(case.buses[index].number for index in reference_indices),
        )
        if self.print_iterations:
            references = ", ".join(str(number) for number in self.result.reference_buses)
            print(f"DC power flow: {self.filename.name}")
            print(f"  Buses: {size}; branches: {len(case.branches)}")
            print(f"  Reference buses: {references}")
            print(f"  Direct linear solves: {self.result.iterations}")
            print(
                f"  {'Converged' if converged else 'Did not converge'}; "
                f"maximum active-power mismatch: {maximum_mismatch:.3e} pu"
            )
        return self.result

    def _reference_indices(self, island_count: int, islands: np.ndarray) -> np.ndarray:
        """Choose one angle reference per connected network island."""
        indices = self.case.bus_index
        override = None if self.reference_bus is None else indices[self.reference_bus]
        references: list[int] = []
        for island in range(island_count):
            members = np.flatnonzero(islands == island)
            if override is not None and islands[override] == island:
                references.append(override)
                continue
            slack = [index for index in members if self.case.buses[index].bus_type == BusType.SLACK]
            if slack:
                references.append(slack[0])
                continue
            candidates = [
                index for index in members if self.case.buses[index].bus_type == BusType.PV
            ]
            if not candidates:
                candidates = members.tolist()
            references.append(
                max(candidates, key=lambda index: self.case.buses[index].p_generation_mw)
            )
        return np.asarray(references, dtype=np.int64)


__all__ = ["DCBranchFlow", "DCPowerFlow", "DCPowerFlowResult"]
