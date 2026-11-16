"""Helpers to assemble component models from parsed ANAREDE data blocks."""

from __future__ import annotations

import math

from pf_tool.models.branch import Branch
from pf_tool.models.bus import Bus, BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.csc import apply_csc_records
from pf_tool.models.generator import Generator
from pf_tool.models.hvdc_lcc import build_lcc_links
from pf_tool.models.shunt import build_shunt
from pf_tool.models.svc import SVC
from pf_tool.models.transformer import branch_from_dlin, build_tap_controls


def build_power_flow_case(tables: dict[str, list[dict[str, str]]]) -> PowerFlowCase:
    """Create bus, branch, and control models from parsed data blocks."""
    constants = {
        record.get("Mn", "").upper(): float(record.get("Val", 0.0))
        for record in tables.get("DCTE", [])
    }
    base_mva = constants.get("BASE", 100.0)
    if base_mva <= 0.0:
        raise ValueError(f"Invalid system base power: {base_mva}")

    voltage_groups = {record.get("G", ""): record for record in tables.get("DGLT", [])}
    base_voltages = {
        record.get("G", ""): float(record.get("V") or 0.0) for record in tables.get("DGBT", [])
    }
    buses: list[Bus] = []
    bus_by_number: dict[int, Bus] = {}
    for record in tables.get("DBAR", []):
        if record.get("E", "L").upper() == "D" or record.get("O", "A").upper() != "A":
            continue
        number = int(float(record["No"]))
        group = voltage_groups.get(record.get("Gl", ""), {})
        bus_kind = int(float(record.get("Tb", 0)))
        bus_type = {
            1: BusType.PV,
            2: BusType.SLACK,
        }.get(bus_kind, BusType.PQ)
        bus = Bus(
            number=number,
            name=record.get("Name", ""),
            bus_type=bus_type,
            voltage_pu=max(float(record.get("V", 1.0)), 1e-6),
            angle_radians=math.radians(float(record.get("A0", 0.0))),
            p_generation_mw=float(record.get("Pg", 0.0)),
            q_generation_mvar=float(record.get("Qg", 0.0)),
            p_load_mw=float(record.get("Pl", 0.0)),
            q_load_mvar=float(record.get("Ql", 0.0)),
            shunt_susceptance_pu=float(record.get("Sh", 0.0)) / base_mva,
            voltage_min_pu=float(group.get("Vmn", 0.9)),
            voltage_max_pu=float(group.get("Vmx", 1.1)),
            q_min_mvar=float(record.get("Qgn", 0.0)),
            q_max_mvar=float(record.get("Qgm", 0.0)),
            voltage_group=record.get("Gl", ""),
            voltage_limit_control=bus_kind == 3,
            area=int(float(record.get("Are") or 0)),
            base_kv=base_voltages.get(record.get("Gb", ""), 0.0),
            dbar_type=bus_kind,
        )
        if number in bus_by_number:
            raise ValueError(f"Duplicate active bus number {number}")
        buses.append(bus)
        bus_by_number[number] = bus
    if not buses:
        raise ValueError("The DBAR block contains no in-service buses")

    # DBAR already holds the scheduled generation; DGER only adds limits.
    for record in tables.get("DGER", []):
        generator = Generator.from_record(record)
        bus = bus_by_number.get(generator.bus)
        if generator.available and bus is not None:
            bus.p_min_mw = generator.minimum_active_power_mw
            bus.p_max_mw = generator.maximum_active_power_mw

    # ANAREDE writes blank reactive limits as 0/0; such PV buses were solved
    # at their (zero-width) reactive limit, i.e. as fixed-Q load buses.
    for bus in buses:
        if (
            bus.bus_type == BusType.PV
            and bus.q_min_mvar is not None
            and bus.q_max_mvar is not None
            and abs(bus.q_max_mvar - bus.q_min_mvar) <= 1e-9
        ):
            bus.bus_type = BusType.PQ
            bus.q_generation_mvar = bus.q_max_mvar

    branches: list[Branch] = []
    for record in tables.get("DLIN", []):
        if (
            record.get("O", "A").upper() != "A"
            or record.get("E", "L").upper() == "D"
            or record.get("Si", "L").upper() == "D"
            or record.get("Sj", "L").upper() == "D"
        ):
            continue
        branch = branch_from_dlin(record, base_mva)
        if branch.from_bus in bus_by_number and branch.to_bus in bus_by_number:
            branches.append(branch)
    apply_csc_records(branches, tables.get("DCSC", []))
    branches = [
        branch
        for branch in branches
        if branch.from_bus in bus_by_number and branch.to_bus in bus_by_number
    ]

    svcs: list[SVC] = []
    for record in tables.get("DCER", []):
        if record.get("O", "A").upper() != "A" or record.get("E", "L").upper() == "D":
            continue
        bus_number = int(float(record["Bus"]))
        bus = bus_by_number.get(bus_number)
        if bus is None:
            continue
        q_mvar = float(record.get("Qg", 0.0))
        bus.q_generation_mvar += q_mvar
        control_bus = int(float(record.get("Bctrl", bus_number)))
        svcs.append(
            SVC(
                bus=bus_number,
                controlled_bus=control_bus if control_bus in bus_by_number else bus_number,
                mode=record.get("Ctrl", "P").upper(),
                slope=float(record.get("Incl", 0.0)) * 0.01,
                q_mvar=q_mvar,
                q_min_mvar=float(record.get("Qgn", 0.0)),
                q_max_mvar=float(record.get("Qgm", 0.0)),
                reference_voltage=bus_by_number.get(control_bus, bus).voltage_pu,
            )
        )

    switched_shunts = []
    banks = tables.get("DBSH_BANK", [])
    branch_keys = {
        key
        for branch in branches
        for key in (
            (branch.from_bus, branch.to_bus, branch.circuit),
            (branch.to_bus, branch.from_bus, branch.circuit),
        )
    }
    for record_index, record in enumerate(tables.get("DBSH", []), start=1):
        if record.get("O", "A").upper() != "A":
            continue
        start = int(float(record["I"]))
        end = int(float(record.get("J") or 0))
        if end:
            # Line shunt: connected at terminal ``Extr`` and switched out with
            # its circuit.
            circuit = int(float(record.get("Nc") or 1))
            if (start, end, circuit) not in branch_keys:
                continue
            terminal = int(float(record.get("Extr") or start))
            bus = bus_by_number.get(terminal if terminal in (start, end) else start)
        else:
            bus = bus_by_number.get(start)
        if bus is None:
            continue
        if not end and record.get("Clr", "N").upper() == "S":
            bus.shunt_susceptance_pu = 0.0
        susceptance, shunt = build_shunt(record, banks, record_index, base_mva)
        shunt.bus = bus.number
        if end:
            shunt.fixed = True
            shunt.line = f"{start}-{end}-{circuit}"
        bus.shunt_susceptance_pu += susceptance
        switched_shunts.append(shunt)

    lcc_links = build_lcc_links(
        tables,
        {number: bus.voltage_pu for number, bus in bus_by_number.items()},
    )
    for link in lcc_links:
        rectifier = bus_by_number.get(link.rectifier_bus)
        inverter = bus_by_number.get(link.inverter_bus)
        if rectifier is not None:
            rectifier.p_load_mw += link.rectifier_active_mw
            rectifier.q_load_mvar += link.rectifier_reactive_mvar
        if inverter is not None:
            inverter.p_generation_mw += link.inverter_active_mw
            inverter.q_load_mvar += link.inverter_reactive_mvar

    if not branches:
        raise ValueError("The DLIN block contains no in-service branches")
    if not any(bus.bus_type == BusType.SLACK for bus in buses):
        raise ValueError("No reference (Tb=2) bus was found")
    options = {
        record.get("Op", "").upper(): record.get("E", "L").upper()
        for record in tables.get("DOPC", [])
    }
    tap_controls = build_tap_controls(
        branches,
        tables.get("DCTR", []),
        {number: bus.voltage_pu for number, bus in bus_by_number.items()},
        base_mva,
        tap_control=options.get("CTAP", "L") == "L",
        phase_control=options.get("CPHS", "L") == "L",
    )
    return PowerFlowCase(
        base_mva=base_mva,
        buses=buses,
        branches=branches,
        svcs=svcs,
        switched_shunts=switched_shunts,
        hvdc_links=lcc_links,
        options=options,
        tap_controls=tap_controls,
    )
