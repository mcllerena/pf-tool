"""Fixed and voltage-controlled bus-shunt models (DBSH / DBSH_BANK)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pf_tool.models.case import PowerFlowCase


@dataclass(slots=True)
class ShuntStage:
    """Bank group: ``units`` identical units of ``unit_mvar`` at 1 pu voltage."""

    unit_mvar: float
    units: int
    in_service: int


@dataclass(slots=True)
class SwitchedShunt:
    bus: int
    controlled_bus: int
    initial_q_mvar: float
    minimum_q_mvar: float
    maximum_q_mvar: float
    minimum_voltage_pu: float
    maximum_voltage_pu: float
    fixed: bool
    current_q_mvar: float
    voltage_scale: float = 1.0
    control_voltage_scale: float = 1.0
    stages: list[ShuntStage] = field(default_factory=list)
    last_direction: int = 0
    frozen: bool = False
    # ``I-J-Nc`` of the circuit for a line shunt; empty for a bus shunt.
    line: str = ""


def build_shunt(
    record: dict[str, str],
    banks: list[dict[str, str]],
    record_index: int,
    base_mva: float,
) -> tuple[float, SwitchedShunt]:
    """Return a DBSH susceptance and its control data from its bank stages."""
    live_banks = [
        bank
        for bank in banks
        if int(float(bank.get("Parent", 0))) == record_index
        and bank.get("O", "A").upper() == "A"
        and bank.get("E", "L").upper() != "D"
    ]
    initial = float(record.get("Qini", 0.0))
    if live_banks:
        stages = [
            ShuntStage(
                unit_mvar=float(bank.get("Sht", 0.0)),
                units=int(float(bank.get("U", 0))),
                in_service=int(float(bank.get("UOp", 0))),
            )
            for bank in live_banks
        ]
        initial = sum(stage.unit_mvar * stage.in_service for stage in stages)
    else:
        stages = [ShuntStage(unit_mvar=initial, units=1, in_service=1)] if initial else []
    minimum = sum(min(0.0, stage.unit_mvar * stage.units) for stage in stages)
    maximum = sum(max(0.0, stage.unit_mvar * stage.units) for stage in stages)
    controlled_bus = int(float(record.get("Bctrl") or record["I"]))
    shunt = SwitchedShunt(
        bus=int(float(record["I"])),
        controlled_bus=controlled_bus or int(float(record["I"])),
        initial_q_mvar=initial,
        minimum_q_mvar=minimum,
        maximum_q_mvar=maximum,
        minimum_voltage_pu=float(record.get("Vmn", 0.9)),
        maximum_voltage_pu=float(record.get("Vmx", 1.1)),
        fixed=record.get("Ctrl", "F").upper() == "F",
        current_q_mvar=initial,
        stages=stages,
    )
    return initial / base_mva, shunt


def _next_switch(shunt: SwitchedShunt, raise_voltage: bool) -> ShuntStage | None:
    """Pick one unit to switch: remove opposing units before adding helping ones."""
    sign = 1.0 if raise_voltage else -1.0
    for stage in shunt.stages:
        if stage.unit_mvar * sign < 0.0 and stage.in_service > 0:
            return stage
    for stage in shunt.stages:
        if stage.unit_mvar * sign > 0.0 and stage.in_service < stage.units:
            return stage
    return None


def snap_to_bank_steps(shunt: SwitchedShunt, target_q_mvar: float) -> float:
    """Switch the bank units whose total is nearest ``target_q_mvar``.

    Updates the stage counts and ``current_q_mvar`` (Mvar at 1 pu) and returns
    the change, so the caller can update the bus susceptance.
    """
    if not shunt.stages:
        return 0.0
    choices = [range(stage.units + 1) for stage in shunt.stages]
    combinations = 1
    for options in choices:
        combinations *= len(options)
    if combinations <= 4096:
        best = min(
            itertools.product(*choices),
            key=lambda counts: (
                abs(sum(s.unit_mvar * n for s, n in zip(shunt.stages, counts)) - target_q_mvar),
                sum(abs(n - s.in_service) for s, n in zip(shunt.stages, counts)),
            ),
        )
    else:
        # Too many combinations: fill the largest units first.
        remaining = target_q_mvar
        counts_by_stage: dict[int, int] = {}
        for index in sorted(
            range(len(shunt.stages)), key=lambda i: -abs(shunt.stages[i].unit_mvar)
        ):
            stage = shunt.stages[index]
            if abs(stage.unit_mvar) < 1e-12:
                counts_by_stage[index] = stage.in_service
                continue
            count = int(np.clip(round(remaining / stage.unit_mvar), 0, stage.units))
            counts_by_stage[index] = count
            remaining -= count * stage.unit_mvar
        best = tuple(counts_by_stage[i] for i in range(len(shunt.stages)))
    total = sum(stage.unit_mvar * count for stage, count in zip(shunt.stages, best))
    for stage, count in zip(shunt.stages, best):
        stage.in_service = int(count)
    change = total - shunt.current_q_mvar
    shunt.current_q_mvar = total
    return change


def adjust_controlled_shunts(case: PowerFlowCase, voltage: object) -> bool:
    """Switch one bank unit per controlled shunt whose voltage leaves its band.

    A shunt that would reverse its previous switching direction is frozen to
    prevent hunting when one unit moves the voltage across the whole band.
    """
    indices = case.bus_index
    changed = False
    for shunt in case.switched_shunts:
        if shunt.fixed or shunt.frozen or not shunt.stages:
            continue
        if shunt.minimum_voltage_pu >= shunt.maximum_voltage_pu:
            continue
        if shunt.controlled_bus not in indices or shunt.bus not in indices:
            continue
        control_voltage = abs(voltage[indices[shunt.controlled_bus]]) * shunt.control_voltage_scale
        if control_voltage < shunt.minimum_voltage_pu:
            direction = 1
        elif control_voltage > shunt.maximum_voltage_pu:
            direction = -1
        else:
            continue
        if shunt.last_direction == -direction:
            shunt.frozen = True
            continue
        stage = _next_switch(shunt, direction > 0)
        if stage is None:
            continue
        removing = stage.unit_mvar * direction < 0.0
        delta = -stage.unit_mvar if removing else stage.unit_mvar
        stage.in_service += -1 if removing else 1
        case.buses[indices[shunt.bus]].shunt_susceptance_pu += (
            delta * shunt.voltage_scale**2
        ) / case.base_mva
        shunt.current_q_mvar += delta
        shunt.last_direction = direction
        changed = True
    return changed
