"""Contract closed low-impedance links with exact complex-voltage scaling."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.sparse import coo_matrix, diags

from models.bus import Bus, BusType
from models.case import PowerFlowCase
from models.transformer import ControlledBranch, TapControl
from utils.network import build_ybus

# Branches with |r + jx| at or below this value (pu) are contracted.
ZERO_IMPEDANCE_THRESHOLD_PU = 2.000001e-4


@dataclass(slots=True)
class ReducedCase:
    case: PowerFlowCase
    original_to_reduced: np.ndarray
    members: list[list[int]]
    representatives: list[int]
    voltage_scales: np.ndarray
    # Index of the original SVC / shunt / LCC link behind each reduced one.
    svc_sources: list[int] | None = None
    shunt_sources: list[int] | None = None
    link_sources: list[int] | None = None

    def expand_voltage(self, voltage: np.ndarray) -> np.ndarray:
        return voltage[self.original_to_reduced] * self.voltage_scales

    def write_back(self, original: PowerFlowCase) -> None:
        """Copy solved control states from the reduced case to ``original``.

        Bus types go to each group's representative.  Switched-shunt steps,
        SVC outputs, LCC set-points and transformer taps/phases are copied to
        the original devices, and the original bus injections and shunt
        susceptances are updated by the same amounts.
        """
        solved = self.case
        seen: set[int] = set()
        for control in solved.tap_controls:
            for branch in control.branches:
                if id(branch) in seen:
                    continue
                seen.add(id(branch))
                original_branch = original.branches[branch.original_index]
                original_branch.tap_ratio = branch.tap
                original_branch.phase_shift_radians = branch.phase
        if solved is original:
            return
        for reduced_index, representative in enumerate(self.representatives):
            original.buses[representative].bus_type = solved.buses[reduced_index].bus_type
        indices = original.bus_index
        base = original.base_mva

        for source, shunt in zip(self.shunt_sources or [], solved.switched_shunts):
            target = original.switched_shunts[source]
            original.buses[indices[target.bus]].shunt_susceptance_pu += (
                shunt.current_q_mvar - target.current_q_mvar
            ) / base
            target.current_q_mvar = shunt.current_q_mvar
            target.frozen = shunt.frozen
            target.last_direction = shunt.last_direction
            for target_stage, stage in zip(target.stages, shunt.stages):
                target_stage.in_service = stage.in_service

        for source, svc in zip(self.svc_sources or [], solved.svcs):
            target = original.svcs[source]
            original.buses[indices[target.bus]].q_generation_mvar += svc.q_mvar - target.q_mvar
            target.q_mvar = svc.q_mvar

        for source, link in zip(self.link_sources or [], solved.hvdc_links):
            target = original.hvdc_links[source]
            rectifier = original.buses[indices[target.rectifier_bus]]
            inverter = original.buses[indices[target.inverter_bus]]
            rectifier.p_load_mw += link.rectifier_active_mw - target.rectifier_active_mw
            rectifier.q_load_mvar += link.rectifier_reactive_mvar - target.rectifier_reactive_mvar
            inverter.p_generation_mw += link.inverter_active_mw - target.inverter_active_mw
            inverter.q_load_mvar += link.inverter_reactive_mvar - target.inverter_reactive_mvar
            for name in _LINK_STATE_FIELDS:
                setattr(target, name, getattr(link, name))


_LINK_STATE_FIELDS = (
    "rectifier_active_mw",
    "inverter_active_mw",
    "rectifier_reactive_mvar",
    "inverter_reactive_mvar",
    "vdc_rectifier_kv",
    "vdc_inverter_kv",
    "tap_rectifier",
    "tap_inverter",
    "mu_rectifier_deg",
    "mu_inverter_deg",
)


def reduce_low_impedance_branches(
    case: PowerFlowCase, threshold_pu: float = ZERO_IMPEDANCE_THRESHOLD_PU
) -> ReducedCase:
    """Contract electrically equivalent buses, including ideal tap ratios."""
    bus_indices = case.bus_index
    count = len(case.buses)
    parent = list(range(count))
    weight = np.ones(count, dtype=np.complex128)
    degree = [0] * count

    def find(index: int) -> tuple[int, complex]:
        if parent[index] == index:
            return index, 1.0 + 0.0j
        old_parent = parent[index]
        root, parent_scale = find(old_parent)
        weight[index] *= parent_scale
        parent[index] = root
        return root, complex(weight[index])

    def union(first: int, second: int, tap: complex) -> bool:
        first_root, first_scale = find(first)
        second_root, second_scale = find(second)
        if first_root == second_root:
            expected = tap * second_scale
            return abs(first_scale - expected) <= 1e-7 * max(1.0, abs(expected))
        parent[first_root] = second_root
        weight[first_root] = tap * second_scale / first_scale
        return True

    for branch in case.branches:
        start = bus_indices[branch.from_bus]
        end = bus_indices[branch.to_bus]
        degree[start] += 1
        degree[end] += 1
        impedance = np.hypot(branch.resistance_pu, branch.reactance_pu)
        should_contract = impedance <= threshold_pu
        if should_contract:
            tap = branch.tap_ratio * np.exp(1j * branch.phase_shift_radians)
            union(start, end, tap)

    roots_and_scales = [find(index) for index in range(count)]
    grouped: dict[int, list[int]] = {}
    for index, (root, _) in enumerate(roots_and_scales):
        grouped.setdefault(root, []).append(index)
    members = list(grouped.values())
    priority = {BusType.PQ: 0, BusType.PV: 1, BusType.SLACK: 2}
    if all(len(group) == 1 for group in members):
        return ReducedCase(
            case,
            np.arange(count, dtype=np.int64),
            members,
            list(range(count)),
            np.ones(count, dtype=np.complex128),
        )

    original_to_reduced = np.empty(count, dtype=np.int64)
    representatives: list[int] = []
    reduced_buses: list[Bus] = []
    reduced_scales = np.ones(count, dtype=np.complex128)
    number_map: dict[int, int] = {}
    for reduced_index, group in enumerate(members):
        representative_index = max(
            group,
            key=lambda index: (priority[case.buses[index].bus_type], degree[index]),
        )
        representatives.append(representative_index)
        representative_scale = roots_and_scales[representative_index][1]
        pv_members = [
            case.buses[index] for index in group if case.buses[index].bus_type == BusType.PV
        ]
        member_scales = {
            index: roots_and_scales[index][1] / representative_scale for index in group
        }
        for index in group:
            original_to_reduced[index] = reduced_index
            reduced_scales[index] = member_scales[index]
            number_map[case.buses[index].number] = case.buses[representative_index].number

        representative = case.buses[representative_index]
        reduced_buses.append(
            replace(
                representative,
                p_generation_mw=sum(case.buses[index].p_generation_mw for index in group),
                q_generation_mvar=sum(case.buses[index].q_generation_mvar for index in group),
                p_load_mw=sum(case.buses[index].p_load_mw for index in group),
                q_load_mvar=sum(case.buses[index].q_load_mvar for index in group),
                shunt_susceptance_pu=sum(
                    abs(member_scales[index]) ** 2 * case.buses[index].shunt_susceptance_pu
                    for index in group
                ),
                q_min_mvar=(
                    sum(bus.q_min_mvar or 0.0 for bus in pv_members) if pv_members else None
                ),
                q_max_mvar=(
                    sum(bus.q_max_mvar or 0.0 for bus in pv_members) if pv_members else None
                ),
            )
        )

    original_ybus = build_ybus(case)
    original_shunts = diags([1j * bus.shunt_susceptance_pu for bus in case.buses], format="csc")
    branch_ybus = original_ybus - original_shunts
    transform = coo_matrix(
        (reduced_scales, (np.arange(count), original_to_reduced)),
        shape=(count, len(reduced_buses)),
    ).tocsc()
    reduced_ybus = (transform.conjugate().transpose() @ branch_ybus @ transform).tocsc()

    reduced_svcs = []
    svc_sources: list[int] = []
    for source, svc in enumerate(case.svcs):
        if svc.bus not in number_map or svc.controlled_bus not in number_map:
            continue
        device_index = bus_indices[svc.bus]
        control_index = bus_indices[svc.controlled_bus]
        svc_sources.append(source)
        reduced_svcs.append(
            replace(
                svc,
                bus=number_map[svc.bus],
                controlled_bus=number_map[svc.controlled_bus],
                device_voltage_scale=abs(reduced_scales[device_index]),
                control_voltage_scale=abs(reduced_scales[control_index]),
            )
        )
    reduced_shunts = []
    shunt_sources: list[int] = []
    for source, shunt in enumerate(case.switched_shunts):
        if shunt.bus not in number_map or shunt.controlled_bus not in number_map:
            continue
        shunt_index = bus_indices[shunt.bus]
        control_index = bus_indices[shunt.controlled_bus]
        shunt_sources.append(source)
        reduced_shunts.append(
            replace(
                shunt,
                bus=number_map[shunt.bus],
                controlled_bus=number_map[shunt.controlled_bus],
                voltage_scale=abs(reduced_scales[shunt_index]),
                control_voltage_scale=abs(reduced_scales[control_index]),
            )
        )
    link_sources = [
        source
        for source, link in enumerate(case.hvdc_links)
        if link.rectifier_bus in number_map and link.inverter_bus in number_map
    ]
    reduced_links = [
        replace(
            link,
            rectifier_bus=number_map[link.rectifier_bus],
            inverter_bus=number_map[link.inverter_bus],
            rectifier_voltage_scale=abs(reduced_scales[bus_indices[link.rectifier_bus]]),
            inverter_voltage_scale=abs(reduced_scales[bus_indices[link.inverter_bus]]),
        )
        for link in (case.hvdc_links[source] for source in link_sources)
    ]
    reduced_case = PowerFlowCase(
        base_mva=case.base_mva,
        buses=reduced_buses,
        branches=[],
        svcs=reduced_svcs,
        switched_shunts=reduced_shunts,
        hvdc_links=reduced_links,
        options=case.options.copy(),
        base_ybus=reduced_ybus,
        tap_controls=_reduce_tap_controls(case, number_map, reduced_scales, bus_indices),
    )
    return ReducedCase(
        reduced_case,
        original_to_reduced,
        members,
        representatives,
        reduced_scales,
        svc_sources,
        shunt_sources,
        link_sources,
    )


def _reduce_tap_controls(
    case: PowerFlowCase,
    number_map: dict[int, int],
    scales: np.ndarray,
    bus_indices: dict[int, int],
) -> list[TapControl]:
    """Map tap/phase controls onto reduced buses.

    Contracted transformers are dropped from their group, and LTC groups that
    now regulate the same reduced bus are merged into one control.
    """
    mapped: dict[int, ControlledBranch] = {}
    reduced: list[TapControl] = []
    voltage_groups: dict[int, TapControl] = {}
    for control in case.tap_controls:
        branches = []
        for branch in control.branches:
            start = number_map[branch.from_bus]
            end = number_map[branch.to_bus]
            if start == end:
                continue
            if id(branch) not in mapped:
                mapped[id(branch)] = replace(
                    branch,
                    from_bus=start,
                    to_bus=end,
                    from_scale=branch.from_scale * scales[bus_indices[branch.from_bus]],
                    to_scale=branch.to_scale * scales[bus_indices[branch.to_bus]],
                )
            branches.append(mapped[id(branch)])
        if not branches or control.controlled_bus not in number_map:
            continue
        target = number_map[control.controlled_bus]
        target_scale = control.controlled_scale * abs(scales[bus_indices[control.controlled_bus]])
        if control.mode == "V" and target in voltage_groups:
            group = voltage_groups[target]
            known = {id(item) for item in group.branches}
            group.branches.extend(item for item in branches if id(item) not in known)
            group.shift_min = min(max(group.shift_min, control.shift_min), 0.0)
            group.shift_max = max(min(group.shift_max, control.shift_max), 0.0)
            group.members.extend(control.members)
            continue
        new_control = replace(
            control,
            branches=branches,
            controlled_bus=target,
            controlled_scale=target_scale,
            members=list(control.members),
        )
        reduced.append(new_control)
        if control.mode == "V":
            voltage_groups[target] = new_control
    return reduced
