"""Sparse construction of selected DC power-transfer distribution factors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

from pf_tool.models.bus import BusType
from pf_tool.models.case import PowerFlowCase

_PTDF_BLOCK_SIZE = 32


@dataclass(slots=True)
class PTDFMatrix:
    """PTDF sensitivities and base flows for selected injection buses.

    ``matrix[line, bus]`` is the MW change on a line for a 1 MW injection at
    the selected bus, balanced by a withdrawal at that island's reference.
    The full all-bus matrix is deliberately not constructed automatically.
    """

    injection_buses: tuple[int, ...]
    branch_keys: tuple[tuple[int, int, int], ...]
    matrix: np.ndarray
    base_flows_mw: np.ndarray
    reference_buses: tuple[int, ...]
    island_labels: np.ndarray


def build_ptdf(
    case: PowerFlowCase,
    injection_buses: Sequence[int],
    *,
    reference_bus: int | None = None,
) -> PTDFMatrix:
    """Build PTDF columns only for ``injection_buses`` using sparse solves.

    Each sensitivity uses a single-reference transfer within its connected
    island. Base flows include the case's scheduled injections and fixed
    branch phase shifts. Transformer taps are included in the DC branch
    susceptance. Sensitivity right-hand sides are solved in bounded blocks to
    avoid materializing an all-bus dense inverse.
    """
    buses = tuple(int(number) for number in injection_buses)
    if len(set(buses)) != len(buses):
        raise ValueError("injection_buses cannot contain duplicates")
    indices = case.bus_index
    unknown = [number for number in buses if number not in indices]
    if unknown:
        raise ValueError(f"Unknown injection bus numbers: {unknown}")
    if reference_bus is not None and reference_bus not in indices:
        raise ValueError(f"Reference bus {reference_bus} is not in service")

    bus_count = len(case.buses)
    branch_count = len(case.branches)
    from_indices = np.empty(branch_count, dtype=np.int64)
    to_indices = np.empty(branch_count, dtype=np.int64)
    coefficients = np.empty(branch_count, dtype=np.float64)
    phase_shifts = np.empty(branch_count, dtype=np.float64)

    matrix_rows: list[int] = []
    matrix_columns: list[int] = []
    matrix_values: list[float] = []
    graph_rows: list[int] = []
    graph_columns: list[int] = []
    for branch_index, branch in enumerate(case.branches):
        start = indices[branch.from_bus]
        end = indices[branch.to_bus]
        reactance = branch.reactance_pu
        tap = branch.tap_ratio
        if not np.isfinite(reactance) or abs(reactance) <= 1e-12:
            raise ValueError(
                f"PTDF cannot model zero reactance on branch "
                f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
            )
        if not np.isfinite(tap) or tap <= 0.0:
            raise ValueError(
                f"Invalid tap ratio on branch {branch.from_bus}-{branch.to_bus}-{branch.circuit}"
            )
        coefficient = 1.0 / (reactance * tap)
        if not np.isfinite(coefficient):
            raise ValueError(
                f"Invalid DC branch susceptance on "
                f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
            )

        from_indices[branch_index] = start
        to_indices[branch_index] = end
        coefficients[branch_index] = coefficient
        phase_shifts[branch_index] = branch.phase_shift_radians
        matrix_rows.extend((start, start, end, end))
        matrix_columns.extend((start, end, start, end))
        matrix_values.extend((coefficient, -coefficient, -coefficient, coefficient))
        graph_rows.extend((start, end))
        graph_columns.extend((end, start))

    susceptance = coo_matrix(
        (matrix_values, (matrix_rows, matrix_columns)),
        shape=(bus_count, bus_count),
        dtype=np.float64,
    ).tocsc()
    graph = coo_matrix(
        (np.ones(len(graph_rows), dtype=np.int8), (graph_rows, graph_columns)),
        shape=(bus_count, bus_count),
    ).tocsr()
    island_count, islands = connected_components(graph, directed=False)
    references = _reference_indices(case, islands, island_count, reference_bus)
    reference_by_island = {int(islands[index]): index for index in references}

    phase_rhs = np.zeros(bus_count, dtype=np.float64)
    np.add.at(phase_rhs, from_indices, coefficients * phase_shifts)
    np.add.at(phase_rhs, to_indices, -coefficients * phase_shifts)
    base_rhs = case.specified_power.real + phase_rhs
    base_angles = np.zeros(bus_count, dtype=np.float64)
    injection_indices = np.asarray([indices[number] for number in buses], dtype=np.int64)
    ptdf = np.zeros((branch_count, len(buses)), dtype=np.float64)

    for island in range(island_count):
        members = np.flatnonzero(islands == island)
        reference = reference_by_island[island]
        free = members[members != reference]
        if free.size == 0:
            continue

        reduced = susceptance[free][:, free].tocsc()
        try:
            factor = splu(reduced)
            base_angles[free] = factor.solve(base_rhs[free])
        except RuntimeError as error:
            raise ValueError(
                "The reduced DC susceptance matrix is singular; check network islands "
                "and branch reactances"
            ) from error

        island_columns = np.flatnonzero(islands[injection_indices] == island)
        free_position = np.full(bus_count, -1, dtype=np.int64)
        free_position[free] = np.arange(free.size, dtype=np.int64)
        branch_rows = np.flatnonzero(islands[from_indices] == island)

        for block_start in range(0, island_columns.size, _PTDF_BLOCK_SIZE):
            columns = island_columns[block_start : block_start + _PTDF_BLOCK_SIZE]
            rhs = np.zeros((free.size, len(columns)), dtype=np.float64)
            for local_column, output_column in enumerate(columns):
                position = free_position[injection_indices[output_column]]
                if position >= 0:
                    rhs[position, local_column] = 1.0
            try:
                free_angles = factor.solve(rhs)
            except RuntimeError as error:
                raise ValueError(
                    "The reduced DC susceptance matrix is singular; check network islands "
                    "and branch reactances"
                ) from error
            angles = np.zeros((bus_count, len(columns)), dtype=np.float64)
            angles[free] = free_angles
            ptdf[np.ix_(branch_rows, columns)] = coefficients[branch_rows, None] * (
                angles[from_indices[branch_rows]] - angles[to_indices[branch_rows]]
            )

    if not np.isfinite(ptdf).all():
        raise RuntimeError("PTDF construction produced non-finite sensitivities")
    if not np.isfinite(base_angles).all():
        raise RuntimeError("PTDF construction produced non-finite base angles")

    base_flows_mw = (
        coefficients
        * (base_angles[from_indices] - base_angles[to_indices] - phase_shifts)
        * case.base_mva
    )
    branch_keys = tuple(
        (branch.from_bus, branch.to_bus, branch.circuit) for branch in case.branches
    )
    return PTDFMatrix(
        injection_buses=buses,
        branch_keys=branch_keys,
        matrix=ptdf,
        base_flows_mw=base_flows_mw,
        reference_buses=tuple(case.buses[index].number for index in references),
        island_labels=islands,
    )


def _reference_indices(
    case: PowerFlowCase,
    islands: np.ndarray,
    island_count: int,
    reference_bus: int | None,
) -> np.ndarray:
    """Select one angle reference per island, matching the DC power-flow rules."""
    indices = case.bus_index
    override = None if reference_bus is None else indices[reference_bus]
    references: list[int] = []
    for island in range(island_count):
        members = np.flatnonzero(islands == island)
        if override is not None and islands[override] == island:
            references.append(override)
            continue
        slack = [index for index in members if case.buses[index].bus_type == BusType.SLACK]
        if slack:
            references.append(slack[0])
            continue
        candidates = [index for index in members if case.buses[index].bus_type == BusType.PV]
        if not candidates:
            candidates = members.tolist()
        references.append(max(candidates, key=lambda index: case.buses[index].p_generation_mw))
    return np.asarray(references, dtype=np.int64)


__all__ = ["PTDFMatrix", "build_ptdf"]
