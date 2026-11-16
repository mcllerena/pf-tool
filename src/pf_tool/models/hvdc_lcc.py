"""Fixed AC-terminal injections for linked line-commutated HVDC converters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pf_tool.models.case import PowerFlowCase


@dataclass(slots=True)
class HVDCLink:
    name: str
    rectifier_bus: int
    inverter_bus: int
    rectifier_active_mw: float
    inverter_active_mw: float
    rectifier_reactive_mvar: float
    inverter_reactive_mvar: float
    p_dc_mw: float = 0.0
    r_dc_ohm: float = 0.0
    vdc_rectifier_kv: float = 0.0
    vdc_inverter_kv: float = 0.0
    vbase_kv: float = 0.0
    rectifier_slack: bool = False
    inverter_slack: bool = False
    alpha_deg: float = 0.0
    gamma_deg: float = 0.0
    xcr_percent: float = 0.0
    xci_percent: float = 0.0
    rectifier_bridge_voltage_kv: float = 0.0
    inverter_bridge_voltage_kv: float = 0.0
    rectifier_nominal_mva: float = 0.0
    inverter_nominal_mva: float = 0.0
    rectifier_poles: int = 1
    inverter_poles: int = 1
    tap_rectifier: float = 1.0
    tap_inverter: float = 1.0
    tap_rectifier_min: float = 0.0
    tap_rectifier_max: float = 0.0
    tap_inverter_min: float = 0.0
    tap_inverter_max: float = 0.0
    mu_rectifier_deg: float = 0.0
    mu_inverter_deg: float = 0.0
    rectifier_voltage_scale: float = 1.0
    inverter_voltage_scale: float = 1.0

    @property
    def current_ka(self) -> float:
        reference_voltage = self.vdc_inverter_kv if self.rectifier_slack else self.vdc_rectifier_kv
        return self.p_dc_mw / reference_voltage if reference_voltage > 1e-12 else 0.0


def build_lcc_links(
    tables: dict[str, list[dict[str, str]]],
    ac_bus_voltages: Mapping[int, float] | None = None,
) -> list[HVDCLink]:
    converters_by_dc_bus = {
        int(float(record["Bdc"])): record
        for record in tables.get("DCNV", [])
        if record.get("O", "A").upper() == "A"
    }
    controls = {
        int(float(record["No"])): record
        for record in tables.get("DCCV", [])
        if record.get("O", "A").upper() == "A"
    }
    dc_buses = {
        int(float(record["No"])): record
        for record in tables.get("DCBA", [])
        if record.get("O", "A").upper() == "A"
    }
    links_by_id = {
        int(float(record["No"])): record
        for record in tables.get("DELO", [])
        if record.get("O", "A").upper() == "A" and record.get("E", "L").upper() != "D"
    }
    result: list[HVDCLink] = []
    for dc_line in tables.get("DCLI", []):
        if dc_line.get("O", "A").upper() != "A":
            continue
        first = converters_by_dc_bus.get(int(float(dc_line["I"])))
        second = converters_by_dc_bus.get(int(float(dc_line["J"])))
        if first is None or second is None:
            continue
        rectifier, inverter = (
            (first, second) if first.get("Modo", "").upper() == "R" else (second, first)
        )
        if rectifier.get("Modo", "").upper() != "R" or inverter.get("Modo", "").upper() != "I":
            continue
        rectifier_control = controls.get(int(float(rectifier["No"])))
        inverter_control = controls.get(int(float(inverter["No"])))
        if rectifier_control is None or inverter_control is None:
            continue
        rectifier_slack = rectifier_control.get("F", "N").upper() == "F"
        inverter_slack = inverter_control.get("F", "N").upper() == "F"
        if rectifier_slack == inverter_slack:
            continue
        power_control = rectifier_control if not rectifier_slack else inverter_control
        if power_control.get("TCnv", "").upper() != "P":
            continue

        rectifier_dc = dc_buses.get(int(float(rectifier["Bdc"])), {})
        inverter_dc = dc_buses.get(int(float(inverter["Bdc"])), {})
        link_id = int(float(rectifier_dc.get("Elo", 0)))
        link_record = links_by_id.get(link_id, {})
        p_dc = abs(float(power_control.get("Vsp", 0.0)))
        v_rectifier = abs(float(rectifier_dc.get("V", link_record.get("V", 0.0))))
        v_inverter = abs(float(inverter_dc.get("V", v_rectifier)))
        resistance = max(0.0, float(dc_line.get("R", 0.0)))
        if rectifier_slack:
            current = p_dc / v_inverter if v_inverter > 1e-12 else 0.0
            p_rectifier = p_dc + current**2 * resistance
            p_inverter = p_dc
            v_rectifier = v_inverter + current * resistance
        else:
            current = p_dc / v_rectifier if v_rectifier > 1e-12 else 0.0
            p_rectifier = p_dc
            p_inverter = max(0.0, p_dc - current**2 * resistance)
            v_inverter = max(0.0, v_rectifier - current * resistance)

        link = HVDCLink(
            name=link_record.get("Name", ""),
            rectifier_bus=int(float(rectifier["Bac"])),
            inverter_bus=int(float(inverter["Bac"])),
            rectifier_active_mw=p_rectifier,
            inverter_active_mw=p_inverter,
            rectifier_reactive_mvar=0.0,
            inverter_reactive_mvar=0.0,
            p_dc_mw=p_dc,
            r_dc_ohm=resistance,
            vdc_rectifier_kv=v_rectifier,
            vdc_inverter_kv=v_inverter,
            vbase_kv=abs(float(link_record.get("V", v_rectifier))),
            rectifier_slack=rectifier_slack,
            inverter_slack=inverter_slack,
            alpha_deg=float(rectifier_control.get("Ang", 0.0)),
            gamma_deg=float(inverter_control.get("Ang", 0.0)),
            xcr_percent=float(rectifier.get("Xc", 0.0)),
            xci_percent=float(inverter.get("Xc", 0.0)),
            rectifier_bridge_voltage_kv=float(rectifier.get("Vfs", 0.0)),
            inverter_bridge_voltage_kv=float(inverter.get("Vfs", 0.0)),
            rectifier_nominal_mva=float(rectifier.get("Snt", 0.0)),
            inverter_nominal_mva=float(inverter.get("Snt", 0.0)),
            rectifier_poles=max(1, int(float(rectifier.get("P", 1)))),
            inverter_poles=max(1, int(float(inverter.get("P", 1)))),
            tap_rectifier=float(rectifier_control.get("Ttr", 1.0)),
            tap_inverter=float(inverter_control.get("Ttr", 1.0)),
            tap_rectifier_min=float(rectifier_control.get("Tmn", 0.0)),
            tap_rectifier_max=float(rectifier_control.get("Tmx", 0.0)),
            tap_inverter_min=float(inverter_control.get("Tmn", 0.0)),
            tap_inverter_max=float(inverter_control.get("Tmx", 0.0)),
        )
        ac_voltages = ac_bus_voltages or {}
        rectifier_ac_voltage = ac_voltages.get(link.rectifier_bus, 1.0)
        inverter_ac_voltage = ac_voltages.get(link.inverter_bus, 1.0)
        link.mu_rectifier_deg = _overlap_angle(link, True, rectifier_ac_voltage)
        link.mu_inverter_deg = _overlap_angle(link, False, inverter_ac_voltage)
        dc_loss = link.current_ka**2 * link.r_dc_ohm
        rectifier_q_power = p_dc + dc_loss if 200.0 <= link.vbase_kv < 750.0 else p_dc
        link.rectifier_reactive_mvar = _reactive_power(
            rectifier_q_power, link.alpha_deg, link.mu_rectifier_deg
        )
        link.inverter_reactive_mvar = _reactive_power(p_dc, link.gamma_deg, link.mu_inverter_deg)
        result.append(link)
    return result


def update_lcc_from_dc_solution(
    case: PowerFlowCase,
    voltage: np.ndarray,
    *,
    damping: float = 1.0,
    active_tolerance_mw: float = 0.1,
    reactive_tolerance_mvar: float = 0.1,
) -> bool:
    """Update LCC terminal powers and converter taps from the solved AC voltage.

    When every power change stays within the tolerances, the update is rolled
    back and ``False`` is returned, so the case keeps the converter state the
    AC solution was computed with (no re-solve is triggered by the caller).
    """
    if not case.hvdc_links:
        return False
    indices = case.bus_index
    buses = {bus.number: bus for bus in case.buses}
    maximum_active_change = 0.0
    maximum_reactive_change = 0.0
    link_snapshot = [replace(link) for link in case.hvdc_links]
    touched = {
        number for link in case.hvdc_links for number in (link.rectifier_bus, link.inverter_bus)
    }
    bus_snapshot = {
        number: (
            buses[number].p_load_mw,
            buses[number].q_load_mvar,
            buses[number].p_generation_mw,
        )
        for number in touched
    }

    for link in case.hvdc_links:
        if link.p_dc_mw <= 1e-12:
            continue
        current = link.current_ka
        loss = current**2 * max(0.0, link.r_dc_ohm)
        if link.rectifier_slack:
            target_p_rectifier = link.p_dc_mw + loss
            target_p_inverter = link.p_dc_mw
            link.vdc_rectifier_kv = link.vdc_inverter_kv + current * link.r_dc_ohm
        else:
            target_p_rectifier = link.p_dc_mw
            target_p_inverter = max(0.0, link.p_dc_mw - loss)
            link.vdc_inverter_kv = max(0.0, link.vdc_rectifier_kv - current * link.r_dc_ohm)
        next_p_rectifier = _damped(link.rectifier_active_mw, target_p_rectifier, damping)
        next_p_inverter = _damped(link.inverter_active_mw, target_p_inverter, damping)

        rectifier_voltage = abs(voltage[indices[link.rectifier_bus]]) * link.rectifier_voltage_scale
        inverter_voltage = abs(voltage[indices[link.inverter_bus]]) * link.inverter_voltage_scale
        # Commutation overlap at the present AC voltages, before the targets.
        link.mu_rectifier_deg = _overlap_angle(link, True, rectifier_voltage)
        link.mu_inverter_deg = _overlap_angle(link, False, inverter_voltage)
        low_voltage = link.vbase_kv <= 10.0
        if low_voltage:
            target_q_rectifier = link.rectifier_reactive_mvar
            target_q_inverter = link.inverter_reactive_mvar
        else:
            rectifier_q_power = (
                link.p_dc_mw + loss if 200.0 <= link.vbase_kv < 750.0 else link.p_dc_mw
            )
            target_q_rectifier = _reactive_power(
                rectifier_q_power, link.alpha_deg, link.mu_rectifier_deg
            )
            target_q_inverter = _reactive_power(link.p_dc_mw, link.gamma_deg, link.mu_inverter_deg)
        next_q_rectifier = _damped(link.rectifier_reactive_mvar, target_q_rectifier, damping)
        next_q_inverter = _damped(link.inverter_reactive_mvar, target_q_inverter, damping)

        next_tap_rectifier = _clamp_tap(
            _damped(
                link.tap_rectifier,
                _target_tap(
                    link.p_dc_mw,
                    link.vdc_rectifier_kv,
                    link.alpha_deg,
                    link.mu_rectifier_deg,
                    link.rectifier_bridge_voltage_kv,
                    link.rectifier_poles,
                    1.0 if low_voltage else rectifier_voltage,
                    link.tap_rectifier_min,
                    link.tap_rectifier_max,
                ),
                damping,
            ),
            link.tap_rectifier_min,
            link.tap_rectifier_max,
        )
        next_tap_inverter = _clamp_tap(
            _damped(
                link.tap_inverter,
                _target_tap(
                    link.p_dc_mw,
                    link.vdc_inverter_kv,
                    link.gamma_deg,
                    link.mu_inverter_deg,
                    link.inverter_bridge_voltage_kv,
                    link.inverter_poles,
                    1.0 if low_voltage else inverter_voltage,
                    link.tap_inverter_min,
                    link.tap_inverter_max,
                ),
                damping,
            ),
            link.tap_inverter_min,
            link.tap_inverter_max,
        )

        rectifier = buses[link.rectifier_bus]
        inverter = buses[link.inverter_bus]
        rectifier.p_load_mw += next_p_rectifier - link.rectifier_active_mw
        rectifier.q_load_mvar += next_q_rectifier - link.rectifier_reactive_mvar
        inverter.p_generation_mw += next_p_inverter - link.inverter_active_mw
        inverter.q_load_mvar += next_q_inverter - link.inverter_reactive_mvar
        maximum_active_change = max(
            maximum_active_change,
            abs(next_p_rectifier - link.rectifier_active_mw),
            abs(next_p_inverter - link.inverter_active_mw),
        )
        maximum_reactive_change = max(
            maximum_reactive_change,
            abs(next_q_rectifier - link.rectifier_reactive_mvar),
            abs(next_q_inverter - link.inverter_reactive_mvar),
        )
        link.rectifier_active_mw = next_p_rectifier
        link.inverter_active_mw = next_p_inverter
        link.rectifier_reactive_mvar = next_q_rectifier
        link.inverter_reactive_mvar = next_q_inverter
        link.tap_rectifier = next_tap_rectifier
        link.tap_inverter = next_tap_inverter
        link.mu_rectifier_deg = _overlap_angle(link, True, rectifier_voltage)
        link.mu_inverter_deg = _overlap_angle(link, False, inverter_voltage)

    changed = (
        maximum_active_change > active_tolerance_mw
        or maximum_reactive_change > reactive_tolerance_mvar
    )
    if not changed:
        for link, saved in zip(case.hvdc_links, link_snapshot):
            for name in link.__slots__:
                setattr(link, name, getattr(saved, name))
        for number, (p_load, q_load, p_generation) in bus_snapshot.items():
            buses[number].p_load_mw = p_load
            buses[number].q_load_mvar = q_load
            buses[number].p_generation_mw = p_generation
    return changed


def _damped(current: float, target: float, damping: float) -> float:
    return (1.0 - damping) * current + damping * target


def _reactive_power(power_mw: float, angle_deg: float, overlap_deg: float) -> float:
    return abs(power_mw) * math.tan(math.radians(angle_deg + 0.5 * overlap_deg))


def _clamp_tap(tap: float, minimum: float, maximum: float) -> float:
    if minimum > 0.0 and maximum > minimum:
        return float(np.clip(tap, minimum, maximum))
    return tap


def _target_tap(
    power_mw: float,
    dc_voltage_kv: float,
    angle_deg: float,
    overlap_deg: float,
    bridge_voltage_kv: float,
    poles: int,
    ac_voltage_pu: float,
    minimum: float,
    maximum: float,
) -> float:
    if min(power_mw, dc_voltage_kv, bridge_voltage_kv, ac_voltage_pu) <= 1e-12:
        return 1.0
    open_voltage = 0.995 * 3.0 * math.sqrt(2.0) / math.pi
    open_voltage *= max(1, poles) * bridge_voltage_kv * ac_voltage_pu
    tap = open_voltage * math.cos(math.radians(angle_deg + 0.5 * overlap_deg))
    tap /= dc_voltage_kv
    return _clamp_tap(max(0.0, tap), minimum, maximum)


def _overlap_angle(link: HVDCLink, rectifier: bool, ac_voltage_pu: float) -> float:
    dc_voltage = link.vdc_rectifier_kv if rectifier else link.vdc_inverter_kv
    reactance = link.xcr_percent if rectifier else link.xci_percent
    bridge_voltage = (
        link.rectifier_bridge_voltage_kv if rectifier else link.inverter_bridge_voltage_kv
    )
    nominal_mva = link.rectifier_nominal_mva if rectifier else link.inverter_nominal_mva
    tap = link.tap_rectifier if rectifier else link.tap_inverter
    angle_deg = link.alpha_deg if rectifier else link.gamma_deg
    previous = link.mu_rectifier_deg if rectifier else link.mu_inverter_deg
    if min(dc_voltage, reactance, bridge_voltage, nominal_mva, tap, ac_voltage_pu) <= 1e-12:
        return previous
    transformer_x_ohm = reactance * 0.01 * bridge_voltage**2 / nominal_mva
    terminal_voltage = tap * bridge_voltage * ac_voltage_pu
    argument = math.cos(math.radians(angle_deg))
    argument -= math.sqrt(2.0) * transformer_x_ohm * link.current_ka / terminal_voltage
    overlap = math.degrees(math.acos(float(np.clip(argument, -1.0, 1.0)))) - angle_deg
    return max(0.0, overlap)
