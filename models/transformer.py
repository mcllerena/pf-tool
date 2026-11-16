"""Transformer branches with under-load tap (CTAP) and phase-shift (CPHS) controls.

Tap convention (ANAREDE): the off-nominal tap ``a`` and phase ``phi`` sit on the
``from`` side, so the series element sees ``V_from / (a e^{j phi})``.

A :class:`TapControl` groups the transformers acting on one control target:

* ``mode == "V"``: LTCs that hold the voltage of ``controlled_bus`` at
  ``setpoint`` (pu) by shifting every tap in the group by the same amount.
* ``mode == "P"``: a phase shifter that holds the active power leaving the
  measured terminal at ``setpoint`` (pu) by shifting its phase angle.

The shift is the state variable used by the solvers and is bounded so every
branch in the group stays within its own limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from models.branch import Branch

if TYPE_CHECKING:
    from models.case import PowerFlowCase


@dataclass(slots=True)
class ControlledBranch:
    from_bus: int
    to_bus: int
    admittance: complex
    charging_pu: float
    base_tap: float
    base_phase: float
    tap: float
    phase: float
    original_index: int
    from_scale: complex = 1.0 + 0.0j
    to_scale: complex = 1.0 + 0.0j


@dataclass(slots=True)
class TapControl:
    mode: str
    branches: list[ControlledBranch]
    controlled_bus: int
    setpoint: float
    shift_min: float
    shift_max: float
    controlled_scale: float = 1.0
    measure_from: bool = True
    shift: float = 0.0
    enabled: bool = True
    # LTCs act only once their controlled voltage leaves ``setpoint ± band``
    # (ANAREDE DCTE TLTC); they then regulate to ``target``, the band edge.
    regulating: bool = False
    target: float = 0.0
    limited: bool = False
    # Set when the control has (almost) no independent effect on its target,
    # e.g. two LTC groups regulating electrically identical buses.
    conflicting: bool = False
    bound_hits: int = 0
    members: list[str] = field(default_factory=list)

    def apply(self, shift: float) -> None:
        self.shift = float(shift)
        for branch in self.branches:
            if self.mode == "V":
                branch.tap = branch.base_tap + self.shift
            else:
                branch.phase = branch.base_phase + self.shift


def branch_from_dlin(record: dict[str, str], base_mva: float) -> Branch:
    """Build a line/transformer branch from a DLIN record."""
    tap = _number(record.get("Tap"), 1.0)
    if abs(tap) < 1e-12:
        tap = 1.0
    resistance = _number(record.get("R")) * 0.01
    reactance = _number(record.get("X")) * 0.01
    start = int(float(record["I"]))
    end = int(float(record["J"]))
    return Branch(
        from_bus=start,
        to_bus=end,
        circuit=int(float(record.get("Nc", 1))),
        resistance_pu=resistance,
        reactance_pu=reactance,
        charging_pu=_number(record.get("Bshl")) / base_mva,
        tap_ratio=tap,
        phase_shift_radians=-math.radians(_number(record.get("Psh"))),
        rating_mva=_number(record.get("Cn")),
        is_transformer=abs(tap - 1.0) > 1e-9 or abs(_number(record.get("Psh"))) > 1e-9,
        tap_min=_number(record.get("Tmn")),
        tap_max=_number(record.get("Tmx")),
        controlled_bus=_controlled_bus(record.get("Bctrl"), start, end),
    )


def _controlled_bus(token: str | None, start: int, end: int) -> int | None:
    text = (token or "").strip().upper().replace("_", " ")
    if text in ("", "FROM BUS"):
        return start
    if text == "TO BUS":
        return end
    try:
        return abs(int(float(text)))
    except ValueError:
        return start


def _controlled_branch(branch: Branch, index: int) -> ControlledBranch:
    return ControlledBranch(
        from_bus=branch.from_bus,
        to_bus=branch.to_bus,
        admittance=1.0 / complex(branch.resistance_pu, branch.reactance_pu),
        charging_pu=branch.charging_pu,
        base_tap=branch.tap_ratio,
        base_phase=branch.phase_shift_radians,
        tap=branch.tap_ratio,
        phase=branch.phase_shift_radians,
        original_index=index,
    )


def build_tap_controls(
    branches: list[Branch],
    dctr_records: list[dict[str, str]],
    bus_voltages: dict[int, float],
    base_mva: float,
    *,
    tap_control: bool = True,
    phase_control: bool = True,
) -> list[TapControl]:
    """Create LTC voltage controls (DLIN) and phase-shifter flow controls (DCTR)."""
    controls: list[TapControl] = []
    shared: dict[int, ControlledBranch] = {}

    def controlled(index: int) -> ControlledBranch:
        if index not in shared:
            shared[index] = _controlled_branch(branches[index], index)
        return shared[index]

    if tap_control:
        groups: dict[int, TapControl] = {}
        for index, branch in enumerate(branches):
            if not (branch.tap_max > branch.tap_min > 0.0):
                continue
            target = branch.controlled_bus
            if target is None or target not in bus_voltages:
                continue
            if abs(complex(branch.resistance_pu, branch.reactance_pu)) < 1e-12:
                continue
            low = branch.tap_min - branch.tap_ratio
            high = branch.tap_max - branch.tap_ratio
            group = groups.get(target)
            if group is None:
                group = TapControl(
                    mode="V",
                    branches=[],
                    controlled_bus=target,
                    setpoint=bus_voltages[target],
                    shift_min=min(low, 0.0),
                    shift_max=max(high, 0.0),
                )
                groups[target] = group
                controls.append(group)
            else:
                group.shift_min = min(max(group.shift_min, low), 0.0)
                group.shift_max = max(min(group.shift_max, high), 0.0)
            group.branches.append(controlled(index))
            group.members.append(f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}")

    if phase_control:
        lookup = {
            (branch.from_bus, branch.to_bus, branch.circuit): index
            for index, branch in enumerate(branches)
        }
        for record in dctr_records:
            if record.get("O", "A").upper() != "A":
                continue
            if record.get("PCtrl", "").upper() != "P":
                continue
            start = int(float(record["I"]))
            end = int(float(record["J"]))
            circuit = int(float(record.get("Nc") or 1))
            index = lookup.get((start, end, circuit))
            if index is None:
                continue
            branch = branches[index]
            measured = int(float(record.get("Ext") or start))
            if measured not in (branch.from_bus, branch.to_bus):
                measured = branch.from_bus
            # DCTR limits are in DLIN ``Psh`` degrees; the model phase is -Psh.
            phase_low = -math.radians(_number(record.get("Fmax")))
            phase_high = -math.radians(_number(record.get("Fmin")))
            if phase_high <= phase_low:
                phase_low, phase_high = -math.pi / 2, math.pi / 2
            controls.append(
                TapControl(
                    mode="P",
                    branches=[controlled(index)],
                    controlled_bus=measured,
                    setpoint=_number(record.get("Vsp")) / base_mva,
                    shift_min=min(phase_low - branch.phase_shift_radians, 0.0),
                    shift_max=max(phase_high - branch.phase_shift_radians, 0.0),
                    measure_from=measured == branch.from_bus,
                    regulating=True,
                    target=_number(record.get("Vsp")) / base_mva,
                    members=[f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"],
                )
            )
    return controls


def branch_stamps(
    admittance: complex, charging_pu: float, tap: float, phase: float
) -> tuple[complex, complex, complex, complex]:
    """Return the branch ``(Yff, Yft, Ytf, Ytt)`` admittance stamps."""
    ratio = tap * np.exp(1j * phase)
    shunt = 0.5j * charging_pu
    return (
        (admittance + shunt) / abs(ratio) ** 2,
        -admittance / np.conj(ratio),
        -admittance / ratio,
        admittance + shunt,
    )


@dataclass(slots=True)
class BranchSensitivity:
    """Terminal powers and their partial derivatives for one controlled branch.

    ``theta`` / ``magnitude`` derivatives are with respect to the voltage of
    the case buses the branch is attached to (``from``, ``to``).
    """

    from_power: complex
    to_power: complex
    from_theta: tuple[complex, complex]
    to_theta: tuple[complex, complex]
    from_magnitude: tuple[complex, complex]
    to_magnitude: tuple[complex, complex]
    from_tap: complex
    to_tap: complex
    from_phase: complex
    to_phase: complex


def branch_sensitivity(
    branch: ControlledBranch, from_voltage: complex, to_voltage: complex
) -> BranchSensitivity:
    """Evaluate the controlled-branch power derivatives at case bus voltages."""
    yff, yft, ytf, ytt = branch_stamps(
        branch.admittance, branch.charging_pu, branch.tap, branch.phase
    )
    vf = branch.from_scale * from_voltage
    vt = branch.to_scale * to_voltage
    magnitude_f = max(abs(from_voltage), 1e-12)
    magnitude_t = max(abs(to_voltage), 1e-12)
    cross_f = vf * np.conj(yft * vt)
    cross_t = vt * np.conj(ytf * vf)
    self_f = abs(vf) ** 2 * np.conj(yff)
    self_t = abs(vt) ** 2 * np.conj(ytt)
    return BranchSensitivity(
        from_power=self_f + cross_f,
        to_power=self_t + cross_t,
        # (dS/dtheta_from, dS/dtheta_to) for the from- and to-side powers.
        from_theta=(1j * cross_f, -1j * cross_f),
        to_theta=(-1j * cross_t, 1j * cross_t),
        from_magnitude=((2.0 * self_f + cross_f) / magnitude_f, cross_f / magnitude_t),
        to_magnitude=(cross_t / magnitude_f, (2.0 * self_t + cross_t) / magnitude_t),
        from_tap=-(2.0 * self_f + cross_f) / branch.tap,
        to_tap=-cross_t / branch.tap,
        from_phase=-1j * cross_f,
        to_phase=1j * cross_t,
    )


def reconcile_tap_controls(case: PowerFlowCase, voltage: np.ndarray, sweeps: int = 3) -> bool:
    """Re-estimate tap/phase settings that contradict the stored voltages.

    Saved ANAREDE cases hold solved voltages but may keep pre-solution
    transformer settings.  For each control group, a one-variable
    least-squares step on the P/Q mismatch at its terminal buses removes
    such inconsistencies (which otherwise appear as huge initial
    mismatches across low-impedance transformers).  A correction is only
    applied when it removes most of a significant terminal mismatch.
    """
    from models.bus import BusType
    from utils.network import build_ybus, calculate_power

    if not case.tap_controls:
        return False
    indices = case.bus_index
    kinds = [bus.bus_type for bus in case.buses]
    changed_any = False
    for _ in range(sweeps):
        mismatch = calculate_power(build_ybus(case), voltage) - case.specified_power
        changed = False
        for control in case.tap_controls:
            derivatives: dict[int, complex] = {}
            for branch in control.branches:
                start = indices[branch.from_bus]
                end = indices[branch.to_bus]
                sensitivity = branch_sensitivity(branch, voltage[start], voltage[end])
                if control.mode == "V":
                    terms = ((start, sensitivity.from_tap), (end, sensitivity.to_tap))
                else:
                    terms = ((start, sensitivity.from_phase), (end, sensitivity.to_phase))
                for bus, value in terms:
                    derivatives[bus] = derivatives.get(bus, 0.0) + value
            residual: list[float] = []
            gradient: list[float] = []
            for bus, value in derivatives.items():
                if kinds[bus] == BusType.SLACK:
                    continue
                residual.append(float(mismatch[bus].real))
                gradient.append(float(value.real))
                if kinds[bus] == BusType.PQ:
                    residual.append(float(mismatch[bus].imag))
                    gradient.append(float(value.imag))
            if not residual:
                continue
            r = np.asarray(residual)
            g = np.asarray(gradient)
            before = float(r @ r)
            if before < 0.25 or float(g @ g) <= 1e-12:
                continue
            target = float(
                np.clip(
                    control.shift - float(g @ r) / float(g @ g),
                    control.shift_min,
                    control.shift_max,
                )
            )
            after_vector = r + g * (target - control.shift)
            if float(after_vector @ after_vector) > 0.25 * before:
                continue
            for bus, value in derivatives.items():
                mismatch[bus] += value * (target - control.shift)
            control.apply(target)
            changed = True
        changed_any |= changed
        if not changed:
            break
    return changed_any


def _number(value: str | None, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default
