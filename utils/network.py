"""Sparse network admittance and power calculation helpers."""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix, csc_matrix, diags

from models.case import PowerFlowCase
from models.transformer import branch_stamps


def build_ybus(case: PowerFlowCase) -> csc_matrix:
    """Build Ybus with line charging, transformer taps, shifts, and bus shunts.

    When ``case.base_ybus`` is set (reduced networks, or networks whose branch
    matrix was frozen by a solver), tap/phase changes of controlled
    transformers are added as stamp differences.
    """
    if case.base_ybus is not None:
        shunts = diags([1j * bus.shunt_susceptance_pu for bus in case.buses], format="csc")
        ybus = case.base_ybus + shunts
        if case.tap_controls:
            ybus = ybus + tap_control_delta(case)
        return ybus.tocsc()
    indices = case.bus_index
    rows: list[int] = []
    columns: list[int] = []
    values: list[complex] = []
    for branch in case.branches:
        from_index = indices[branch.from_bus]
        to_index = indices[branch.to_bus]
        impedance = complex(branch.resistance_pu, branch.reactance_pu)
        if abs(impedance) < 1e-12:
            raise ValueError(
                f"Branch {branch.from_bus}-{branch.to_bus}-{branch.circuit} has zero impedance"
            )
        series = 1.0 / impedance
        charging = 0.5j * branch.charging_pu
        tap = branch.tap_ratio * np.exp(1j * branch.phase_shift_radians)
        rows.extend((from_index, to_index, from_index, to_index))
        columns.extend((from_index, to_index, to_index, from_index))
        values.extend(
            (
                (series + charging) / abs(tap) ** 2,
                series + charging,
                -series / np.conj(tap),
                -series / tap,
            )
        )
    for index, bus in enumerate(case.buses):
        rows.append(index)
        columns.append(index)
        values.append(1j * bus.shunt_susceptance_pu)
    return coo_matrix(
        (np.asarray(values, dtype=np.complex128), (rows, columns)),
        shape=(len(case.buses), len(case.buses)),
    ).tocsc()


def calculate_power(ybus: csc_matrix, voltage: np.ndarray) -> np.ndarray:
    """Return complex per-unit bus injections for a voltage vector."""
    return voltage * np.conj(ybus @ voltage)


def freeze_branch_matrix(case: PowerFlowCase) -> None:
    """Store the branch-only Ybus so tap controls can be applied as deltas."""
    if case.base_ybus is not None:
        return
    shunts = diags([1j * bus.shunt_susceptance_pu for bus in case.buses], format="csc")
    case.base_ybus = (build_ybus(case) - shunts).tocsc()


def tap_control_delta(case: PowerFlowCase) -> csc_matrix:
    """Ybus change produced by moving controlled taps/phases off their base."""
    indices = case.bus_index
    rows: list[int] = []
    columns: list[int] = []
    values: list[complex] = []
    seen: set[int] = set()
    for control in case.tap_controls:
        for branch in control.branches:
            if id(branch) in seen:
                continue
            seen.add(id(branch))
            if branch.tap == branch.base_tap and branch.phase == branch.base_phase:
                continue
            current = branch_stamps(branch.admittance, branch.charging_pu, branch.tap, branch.phase)
            base = branch_stamps(
                branch.admittance, branch.charging_pu, branch.base_tap, branch.base_phase
            )
            start = indices[branch.from_bus]
            end = indices[branch.to_bus]
            scales = (branch.from_scale, branch.to_scale)
            for slot, (row, column, row_side, column_side) in enumerate(
                ((start, start, 0, 0), (start, end, 0, 1), (end, start, 1, 0), (end, end, 1, 1))
            ):
                rows.append(row)
                columns.append(column)
                values.append(
                    np.conj(scales[row_side]) * (current[slot] - base[slot]) * scales[column_side]
                )
    size = len(case.buses)
    return coo_matrix(
        (np.asarray(values, dtype=np.complex128), (rows, columns)), shape=(size, size)
    ).tocsc()
