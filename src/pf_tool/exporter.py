"""Export a solved power flow to an Excel workbook, one sheet per component.

Usage::

    from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

    solver = NewtonRaphsonPowerFlow("CASO01-FLOW.dat")
    solver.solve()
    ExportSolution(solver, output_path="results/CASO01.xlsx")

Sheets: Summary, Buses, Generators, Loads, Lines, Transformers, LTC, PST,
HVDC, SVC, CSC and Shunts.  Powers are in MW/Mvar, voltages in pu (and kV
where the DGBT base is known), angles in degrees.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font

from pf_tool.models.branch import Branch
from pf_tool.models.bus import BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.network_reduction import ZERO_IMPEDANCE_THRESHOLD_PU, ReducedCase
from pf_tool.models.transformer import TapControl, branch_stamps
from pf_tool.utils.network import build_ybus, calculate_power

_FLOW_HEADERS = (
    "MW:From-To",
    "Mvar:From-To",
    "Power Factor:From-To",
    "F:Capac/Inductive",
    "MW:To-From",
    "Mvar:To-From",
    "Power Factor:To-From",
    "T:Capac/Inductive",
)

SHEET_HEADERS: dict[str, tuple[str, ...]] = {
    "Summary": ("Field", "Value"),
    "Buses": (
        "Bus",
        "Name",
        "Type",
        "Area",
        "InServ",
        "Vm(pu)",
        "Vm(kV)",
        "Va(deg)",
        "Pg(MW)",
        "Qg(MVAr)",
        "Pl(MW)",
        "Ql(MVAr)",
        "Qshunt(MVAr)",
        "Qsvc(MVAr)",
        "Phvdc(MW)",
        "Qhvdc(MVAr)",
        "Vmin",
        "Vmax",
        "Violation",
        "RepresentativeBus",
        "Collapsed",
    ),
    "Generators": (
        "Bus",
        "Name",
        "Type",
        "Pg(MW)",
        "Qg(MVAr)",
        "Qmin(MVAr)",
        "Qmax(MVAr)",
        "Pmax(MW)",
        "Reserve(MW)",
        "Vm(pu)",
        "Va(deg)",
        "QLimit",
    ),
    "Loads": ("Bus", "Name", "Pl(MW)", "Ql(MVAr)", "Vm(pu)", "Source"),
    "Lines": (
        "Line",
        "From",
        "To",
        "Circuit",
        "R(pu)",
        "X(pu)",
        "B(pu)",
        "Tap",
        "Shift(deg)",
        "Rate(MVA)",
        *_FLOW_HEADERS,
        "Loading%",
        "P_loss(MW)",
        "Q_loss(MVAr)",
        "Violation",
        "Collapsed",
    ),
    "Transformers": (
        "Device",
        "From",
        "To",
        "Circuit",
        "R(pu)",
        "X(pu)",
        "Tap",
        "Shift(deg)",
        "Rate(MVA)",
        *_FLOW_HEADERS,
        "Loading%",
        "P_loss(MW)",
        "Q_loss(MVAr)",
        "Violation",
        "Collapsed",
    ),
    "LTC": (
        "Device",
        "From",
        "To",
        "Circuit",
        "CtrlBus",
        "Tap",
        "TapMin",
        "TapMax",
        "Vset(pu)",
        "Vctrl(pu)",
        "State",
        *_FLOW_HEADERS,
    ),
    "PST": (
        "Device",
        "From",
        "To",
        "Circuit",
        "CtrlBus",
        "Shift(deg)",
        "ShiftMin(deg)",
        "ShiftMax(deg)",
        "Ptarget(MW)",
        "Pmeasured(MW)",
        "State",
        *_FLOW_HEADERS,
    ),
    "HVDC": (
        "Link",
        "Bus#",
        "BusName",
        "Volt(pu)",
        "Type",
        "Control",
        "Porder(MW)",
        "P(MW)",
        "Q(MVAr)",
        "Loss(MW)",
        "Vdc(kV)",
        "Idc(kA)",
        "Idc(A)",
        "Angle(deg)",
        "Mu(deg)",
        "Phi(deg)",
        "Tap",
        "Status",
    ),
    "SVC": (
        "Device",
        "Bus",
        "Name",
        "CtrlBus",
        "Mode",
        "Slope(%)",
        "Vref(pu)",
        "Vctrl(pu)",
        "Vterm(pu)",
        "Qsvc(MVAr)",
        "Qmin(MVAr)",
        "Qmax(MVAr)",
        "Residual",
        "State",
    ),
    "CSC": (
        "Device",
        "From",
        "To",
        "Circuit",
        "Mode",
        "X(pu)",
        "Xmin(pu)",
        "Xmax(pu)",
        "Bypass",
        *_FLOW_HEADERS,
        "Loading%",
        "Status",
    ),
    "Shunts": (
        "Device",
        "Bus",
        "Kind",
        "Line",
        "Control",
        "CtrlBus",
        "Vmin(pu)",
        "Vmax(pu)",
        "Vctrl(pu)",
        "Units",
        "Qnom(MVAr)",
        "Q(MVAr)",
        "Qmin(MVAr)",
        "Qmax(MVAr)",
        "State",
    ),
}

SheetRows = tuple[Sequence[str], Iterable[Sequence[object]]]


class ExportSolution:
    """Export the solved components of a power-flow solver to a workbook.

    ``solver`` is a :class:`NewtonRaphsonPowerFlow` or
    :class:`OptimizationPowerFlow` on which ``solve()`` has been called.
    """

    def __init__(
        self,
        solver: Any,
        output_path: str | Path,
        *,
        format: str = "excel",
        export: bool = True,
    ) -> None:
        if format.strip().lower() not in {"excel", "xlsx"}:
            raise ValueError(f"Unsupported solution export format: {format!r}")
        if getattr(solver, "result", None) is None or getattr(solver, "reduction", None) is None:
            raise ValueError("Call solve() on the solver before exporting its solution")
        self.solver = solver
        self.format = "excel"
        self.output_path = Path(output_path)
        self.sheets = _SolutionTables(solver).sheets(self.output_path)
        if export:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_excel(self.sheets, self.output_path)


@dataclass(slots=True)
class _Flow:
    from_power: complex
    to_power: complex
    collapsed: bool


class _SolutionTables:
    """Derive component results from the solved original-network case."""

    def __init__(self, solver: Any) -> None:
        self.solver = solver
        self.result = solver.result
        self.case: PowerFlowCase = solver.case
        self.reduction: ReducedCase = solver.reduction
        self.base = self.case.base_mva
        self.voltage = np.asarray(self.result.voltage, dtype=np.complex128)
        self.magnitude = np.abs(self.voltage)
        self.angle_deg = np.degrees(np.angle(self.voltage))
        self.index = self.case.bus_index
        self.node = self.reduction.original_to_reduced
        self._bus_quantities()
        self._generation()
        self._flows()
        self._control_states()

    # ------------------------------------------------------------------ buses
    def _bus_quantities(self) -> None:
        count = len(self.case.buses)
        self.rect_p = np.zeros(count)
        self.rect_q = np.zeros(count)
        self.inv_p = np.zeros(count)
        self.inv_q = np.zeros(count)
        for link in self.case.hvdc_links:
            rectifier = self.index[link.rectifier_bus]
            inverter = self.index[link.inverter_bus]
            self.rect_p[rectifier] += link.rectifier_active_mw
            self.rect_q[rectifier] += link.rectifier_reactive_mvar
            self.inv_p[inverter] += link.inverter_active_mw
            self.inv_q[inverter] += link.inverter_reactive_mvar
        self.svc_q = np.zeros(count)
        for svc in self.case.svcs:
            self.svc_q[self.index[svc.bus]] += svc.q_mvar
        buses = self.case.buses
        self.load_p = np.array([bus.p_load_mw for bus in buses]) - self.rect_p
        self.load_q = np.array([bus.q_load_mvar for bus in buses]) - self.rect_q - self.inv_q
        self.hvdc_p = self.inv_p - self.rect_p
        self.hvdc_q = -(self.rect_q + self.inv_q)
        self.scheduled_p = np.array([bus.p_generation_mw for bus in buses]) - self.inv_p
        self.scheduled_q = np.array([bus.q_generation_mvar for bus in buses]) - self.svc_q
        self.shunt_b = np.array([bus.shunt_susceptance_pu for bus in buses])
        self.shunt_q = self.shunt_b * self.magnitude**2 * self.base
        self.ybus = build_ybus(self.case)
        self.injection = calculate_power(self.ybus, self.voltage) * self.base
        self.node_type = [
            self.case.buses[self.reduction.representatives[n]].bus_type
            for n in range(len(self.reduction.members))
        ]

    def _generation(self) -> None:
        """Machine outputs: node balance split among the node's generators."""
        self.gen_p = self.scheduled_p.copy()
        self.gen_q = self.scheduled_q.copy()
        self.is_generator = np.array(
            [
                bus.dbar_type in (1, 2)
                or abs(self.scheduled_p[i]) > 1e-9
                or abs(self.scheduled_q[i]) > 1e-9
                for i, bus in enumerate(self.case.buses)
            ]
        )
        for node, members in enumerate(self.reduction.members):
            members = list(members)
            node_p = sum(self.injection[m].real - self.hvdc_p[m] + self.load_p[m] for m in members)
            node_q = sum(
                self.injection[m].imag - self.svc_q[m] - self.hvdc_q[m] + self.load_q[m]
                for m in members
            )
            kind = self.node_type[node]
            if kind == BusType.SLACK:
                slack = [m for m in members if self.case.buses[m].dbar_type == 2] or [
                    self.reduction.representatives[node]
                ]
                fixed = sum(self.scheduled_p[m] for m in members if m not in slack)
                for m in slack:
                    self.gen_p[m] = (node_p - fixed) / len(slack)
            if kind in (BusType.PV, BusType.SLACK):
                regulating = [m for m in members if self.case.buses[m].dbar_type in (1, 2)] or [
                    self.reduction.representatives[node]
                ]
                fixed = sum(self.scheduled_q[m] for m in members if m not in regulating)
                ranges = np.array(
                    [
                        max(
                            (self.case.buses[m].q_max_mvar or 0.0)
                            - (self.case.buses[m].q_min_mvar or 0.0),
                            0.0,
                        )
                        for m in regulating
                    ]
                )
                weights = (
                    ranges / ranges.sum()
                    if ranges.sum() > 0
                    else np.full(len(regulating), 1.0 / len(regulating))
                )
                for m, weight in zip(regulating, weights):
                    self.gen_q[m] = (node_q - fixed) * weight
        self.local = (
            (self.gen_p + self.hvdc_p - self.load_p)
            + 1j * (self.gen_q + self.svc_q + self.hvdc_q - self.load_q)
        ) / self.base

    # --------------------------------------------------------------- branches
    def _branch_flow(self, branch: Branch) -> tuple[complex, complex]:
        impedance = complex(branch.resistance_pu, branch.reactance_pu)
        yff, yft, ytf, ytt = branch_stamps(
            1.0 / impedance, branch.charging_pu, branch.tap_ratio, branch.phase_shift_radians
        )
        vf = self.voltage[self.index[branch.from_bus]]
        vt = self.voltage[self.index[branch.to_bus]]
        return vf * np.conj(yff * vf + yft * vt), vt * np.conj(ytf * vf + ytt * vt)

    def _flows(self) -> None:
        """Branch flows; contracted links are solved from KCL in their group."""
        self.flows: list[_Flow] = []
        collapsed: dict[int, list[int]] = {}
        charging: dict[int, tuple[complex, complex]] = {}
        outflow = np.zeros(len(self.case.buses), dtype=np.complex128)
        for position, branch in enumerate(self.case.branches):
            start = self.index[branch.from_bus]
            end = self.index[branch.to_bus]
            if (
                self.node[start] == self.node[end]
                and math.hypot(branch.resistance_pu, branch.reactance_pu)
                <= ZERO_IMPEDANCE_THRESHOLD_PU
            ):
                # The series part of a contracted link is lossless and solved
                # from KCL; its line charging still draws -j b/2 |V|^2 per end.
                half = 0.5 * branch.charging_pu
                charge_from = -1j * half * self.magnitude[start] ** 2 / branch.tap_ratio**2
                charge_to = -1j * half * self.magnitude[end] ** 2
                charging[position] = (charge_from, charge_to)
                outflow[start] += charge_from
                outflow[end] += charge_to
                collapsed.setdefault(int(self.node[start]), []).append(position)
                self.flows.append(_Flow(charge_from, charge_to, True))
                continue
            from_power, to_power = self._branch_flow(branch)
            outflow[start] += from_power
            outflow[end] += to_power
            self.flows.append(_Flow(from_power, to_power, False))

        shunt_power = -1j * self.shunt_b * self.magnitude**2
        demand = self.local - shunt_power - outflow
        for node, positions in collapsed.items():
            members = list(self.reduction.members[node])
            row = {bus: k for k, bus in enumerate(members)}
            incidence = np.zeros((len(members), len(positions)))
            for column, position in enumerate(positions):
                branch = self.case.branches[position]
                incidence[row[self.index[branch.from_bus]], column] += 1.0
                incidence[row[self.index[branch.to_bus]], column] -= 1.0
            values, *_ = np.linalg.lstsq(
                incidence.astype(np.complex128), demand[members], rcond=None
            )
            for column, position in enumerate(positions):
                series = complex(values[column])
                charge_from, charge_to = charging[position]
                self.flows[position] = _Flow(series + charge_from, -series + charge_to, True)

    # --------------------------------------------------------------- controls
    def _control_states(self) -> None:
        self.ltc_state: dict[int, tuple[str, TapControl]] = {}
        self.pst_state: dict[int, tuple[str, TapControl]] = {}
        for control in self.reduction.case.tap_controls:
            if not control.enabled:
                state = "Disabled"
            elif control.conflicting:
                state = "Conflict"
            elif control.limited:
                state = "AtLimit"
            elif control.regulating:
                state = "Regulating"
            else:
                state = "InBand"
            target = self.pst_state if control.mode == "P" else self.ltc_state
            for branch in control.branches:
                target[branch.original_index] = (state, control)

    # ----------------------------------------------------------------- sheets
    def sheets(self, output_path: Path) -> dict[str, SheetRows]:
        rows = {
            "Summary": self._summary(output_path),
            "Buses": self._buses(),
            "Generators": self._generators(),
            "Loads": self._loads(),
            "Lines": self._branch_rows(lambda b: _branch_kind(b) == "line", True),
            "Transformers": self._branch_rows(lambda b: _branch_kind(b) == "transformer", False),
            "LTC": self._ltc(),
            "PST": self._pst(),
            "HVDC": self._hvdc(),
            "SVC": self._svc(),
            "CSC": self._csc(),
            "Shunts": self._shunts(),
        }
        return {name: (SHEET_HEADERS[name], list(rows[name])) for name in SHEET_HEADERS}

    def _summary(self, output_path: Path) -> list[tuple[str, object]]:
        solver = self.solver
        method = type(solver).__name__
        if method == "NewtonRaphsonPowerFlow":
            method = "Newton-Raphson"
        elif method == "FastDecoupledPowerFlow":
            variant = (
                "constant B'/B''" if getattr(solver, "enhancement", False) else "Jacobian blocks"
            )
            start = "warm start" if getattr(solver, "warm_start", False) else "cold start"
            method = f"Fast decoupled ({variant}, {start})"
        elif method == "OptimizationPowerFlow":
            start = "warm start" if getattr(solver, "warm_start", False) else "cold start"
            method = f"Optimization (Pyomo/Ipopt, {start})"
        kinds = [_branch_kind(branch) for branch in self.case.branches]
        losses = sum(flow.from_power + flow.to_power for flow in self.flows)
        overloads = sum(
            1
            for branch, flow in zip(self.case.branches, self.flows)
            if _loading(branch, flow, self.base) > 100.0
        )
        upper = sum(
            1
            for i, bus in enumerate(self.case.buses)
            if self.magnitude[i] > bus.voltage_max_pu + 1e-6
        )
        lower = sum(
            1
            for i, bus in enumerate(self.case.buses)
            if self.magnitude[i] < bus.voltage_min_pu - 1e-6
        )
        return [
            ("Case", str(getattr(solver, "filename", None) or "")),
            ("Workbook", str(output_path)),
            ("Method", method),
            ("Converged", bool(self.result.converged)),
            ("Iterations", int(self.result.iterations)),
            ("Max Mismatch (pu)", float(self.result.max_mismatch)),
            ("Failure Reason", self.result.failure_reason or ""),
            ("Base MVA", self.base),
            ("Buses", len(self.case.buses)),
            ("Buses After Reduction", len(self.reduction.members)),
            ("Generators", int(self.is_generator.sum())),
            (
                "Loads",
                int(sum(1 for p, q in zip(self.load_p, self.load_q) if abs(p) + abs(q) > 1e-9)),
            ),
            ("Branches", len(self.case.branches)),
            ("Lines", kinds.count("line")),
            ("Transformers", kinds.count("transformer")),
            ("CSC", kinds.count("csc")),
            ("LTC", len(self.ltc_state)),
            ("PST", len(self.pst_state)),
            ("HVDC Links", len(self.case.hvdc_links)),
            ("SVC", len(self.case.svcs)),
            ("Shunts", len(self.case.switched_shunts)),
            ("Total Generation (MW)", float(self.gen_p.sum())),
            ("Total Generation (MVAr)", float(self.gen_q.sum())),
            ("Total Load (MW)", float(self.load_p.sum())),
            ("Total Load (MVAr)", float(self.load_q.sum())),
            ("AC Losses (MW)", float(losses.real * self.base)),
            ("AC Losses (MVAr)", float(losses.imag * self.base)),
            ("Voltage Upper Violations", upper),
            ("Voltage Lower Violations", lower),
            ("Line Flow Overloads", overloads),
        ]

    def _buses(self) -> Iterable[Sequence[object]]:
        representative = {
            m: self.case.buses[self.reduction.representatives[node]].number
            for node, members in enumerate(self.reduction.members)
            for m in members
        }
        for i, bus in enumerate(self.case.buses):
            node = int(self.node[i])
            yield (
                bus.number,
                bus.name,
                bus.bus_type.value,
                bus.area,
                True,
                self.magnitude[i],
                self.magnitude[i] * bus.base_kv if bus.base_kv else None,
                self.angle_deg[i],
                self.gen_p[i],
                self.gen_q[i],
                self.load_p[i],
                self.load_q[i],
                self.shunt_q[i],
                self.svc_q[i],
                self.hvdc_p[i],
                self.hvdc_q[i],
                bus.voltage_min_pu,
                bus.voltage_max_pu,
                _voltage_violation(self.magnitude[i], bus.voltage_min_pu, bus.voltage_max_pu),
                representative[i],
                len(self.reduction.members[node]) > 1,
            )

    def _generators(self) -> Iterable[Sequence[object]]:
        for i, bus in enumerate(self.case.buses):
            if not self.is_generator[i]:
                continue
            q_limit = ""
            if bus.dbar_type == 1 and bus.bus_type == BusType.PQ:
                low, high = bus.q_min_mvar or 0.0, bus.q_max_mvar or 0.0
                if abs(high - low) <= 1e-9:
                    q_limit = "ZeroRange"
                elif self.gen_q[i] >= high - 0.5:
                    q_limit = "Qmax"
                elif self.gen_q[i] <= low + 0.5:
                    q_limit = "Qmin"
            reserve = bus.p_max_mw - self.gen_p[i] if bus.p_max_mw else None
            yield (
                bus.number,
                bus.name,
                bus.bus_type.value,
                self.gen_p[i],
                self.gen_q[i],
                bus.q_min_mvar,
                bus.q_max_mvar,
                bus.p_max_mw,
                reserve,
                self.magnitude[i],
                self.angle_deg[i],
                q_limit,
            )

    def _loads(self) -> Iterable[Sequence[object]]:
        for i, bus in enumerate(self.case.buses):
            if abs(self.load_p[i]) + abs(self.load_q[i]) > 1e-9:
                yield (
                    bus.number,
                    bus.name,
                    self.load_p[i],
                    self.load_q[i],
                    self.magnitude[i],
                    "DBAR",
                )

    def _flow_columns(self, flow: _Flow) -> tuple[object, ...]:
        from_power = flow.from_power * self.base
        to_power = flow.to_power * self.base
        return (
            from_power.real,
            from_power.imag,
            _power_factor(from_power),
            _reactive_type(from_power),
            to_power.real,
            to_power.imag,
            _power_factor(to_power),
            _reactive_type(to_power),
        )

    def _branch_rows(self, select: Any, with_charging: bool) -> Iterable[Sequence[object]]:
        for position, (branch, flow) in enumerate(zip(self.case.branches, self.flows), start=1):
            if not select(branch):
                continue
            loss = (flow.from_power + flow.to_power) * self.base
            loading = _loading(branch, flow, self.base)
            electrical = (branch.resistance_pu, branch.reactance_pu)
            if with_charging:
                electrical += (branch.charging_pu,)
            yield (
                position,
                branch.from_bus,
                branch.to_bus,
                branch.circuit,
                *electrical,
                branch.tap_ratio,
                -math.degrees(branch.phase_shift_radians),
                branch.rating_mva,
                *self._flow_columns(flow),
                loading,
                loss.real,
                loss.imag,
                loading > 100.0,
                flow.collapsed,
            )

    def _ltc(self) -> Iterable[Sequence[object]]:
        voltage_set = {bus.number: bus.voltage_pu for bus in self.case.buses}
        for position, (branch, flow) in enumerate(zip(self.case.branches, self.flows), start=1):
            if not branch.tap_max > branch.tap_min > 0.0:
                continue
            state, _ = self.ltc_state.get(position - 1, ("Fixed", None))
            if flow.collapsed:
                state = "Contracted"
            controlled = branch.controlled_bus
            actual = self.magnitude[self.index[controlled]] if controlled in self.index else None
            yield (
                position,
                branch.from_bus,
                branch.to_bus,
                branch.circuit,
                controlled,
                branch.tap_ratio,
                branch.tap_min,
                branch.tap_max,
                voltage_set.get(controlled),
                actual,
                state,
                *self._flow_columns(flow),
            )

    def _pst(self) -> Iterable[Sequence[object]]:
        originals = {
            branch.original_index: control
            for control in self.case.tap_controls
            if control.mode == "P"
            for branch in control.branches
        }
        for index, control in sorted(originals.items()):
            branch = self.case.branches[index]
            flow = self.flows[index]
            state, _ = self.pst_state.get(index, ("Contracted", None))
            base_phase = control.branches[0].base_phase
            shift_min = -math.degrees(base_phase + control.shift_max)
            shift_max = -math.degrees(base_phase + control.shift_min)
            measured = flow.from_power if control.measure_from else flow.to_power
            yield (
                index + 1,
                branch.from_bus,
                branch.to_bus,
                branch.circuit,
                control.controlled_bus,
                -math.degrees(branch.phase_shift_radians),
                shift_min,
                shift_max,
                control.setpoint * self.base,
                measured.real * self.base,
                state,
                *self._flow_columns(flow),
            )

    def _hvdc(self) -> Iterable[Sequence[object]]:
        buses = self.case.buses
        for link in self.case.hvdc_links:
            active = link.p_dc_mw > 1e-12
            current = link.current_ka if active else 0.0
            loss = current**2 * link.r_dc_ohm
            for kind in ("Rectifier", "Inverter"):
                rectifier = kind == "Rectifier"
                number = link.rectifier_bus if rectifier else link.inverter_bus
                bus = buses[self.index[number]]
                slack = link.rectifier_slack if rectifier else link.inverter_slack
                angle = link.alpha_deg if rectifier else link.gamma_deg
                mu = link.mu_rectifier_deg if rectifier else link.mu_inverter_deg
                power = link.rectifier_active_mw if rectifier else link.inverter_active_mw
                reactive = (
                    link.rectifier_reactive_mvar if rectifier else link.inverter_reactive_mvar
                )
                phi = math.degrees(math.atan2(reactive, power)) if active and power else None
                yield (
                    link.name,
                    number,
                    bus.name,
                    self.magnitude[self.index[number]],
                    kind,
                    "Slack" if slack else "Power",
                    link.p_dc_mw,
                    power,
                    reactive,
                    loss,
                    link.vdc_rectifier_kv if rectifier else link.vdc_inverter_kv,
                    current,
                    current * 1000.0,
                    angle,
                    mu,
                    phi,
                    link.tap_rectifier if rectifier else link.tap_inverter,
                    "InSvc" if active else "Idle (Porder = 0)",
                )

    def _svc(self) -> Iterable[Sequence[object]]:
        for position, svc in enumerate(self.case.svcs, start=1):
            device = self.index[svc.bus]
            control = self.index[svc.controlled_bus]
            terminal = self.magnitude[device]
            controlled = self.magnitude[control]
            q = svc.q_mvar / self.base
            low = svc.q_min_mvar * terminal**2
            high = svc.q_max_mvar * terminal**2
            active = self.node_type[int(self.node[device])] == BusType.PQ
            droop = q / terminal if svc.mode == "I" else q
            residual = controlled - svc.reference_voltage + svc.slope * droop
            if not active:
                state = "Inactive"
            elif svc.q_mvar <= low + 1e-3:
                state = "Qmin"
            elif svc.q_mvar >= high - 1e-3:
                state = "Qmax"
            else:
                state = "Free"
            yield (
                position,
                svc.bus,
                self.case.buses[device].name,
                svc.controlled_bus,
                svc.mode,
                svc.slope * 100.0,
                svc.reference_voltage,
                controlled,
                terminal,
                svc.q_mvar,
                low,
                high,
                residual if state == "Free" else None,
                state,
            )

    def _csc(self) -> Iterable[Sequence[object]]:
        for position, (branch, flow) in enumerate(zip(self.case.branches, self.flows), start=1):
            if not branch.is_csc:
                continue
            yield (
                position,
                branch.from_bus,
                branch.to_bus,
                branch.circuit,
                branch.csc_mode,
                0.0 if branch.bypassed else branch.reactance_pu,
                branch.csc_x_min_pu,
                branch.csc_x_max_pu,
                branch.bypassed,
                *self._flow_columns(flow),
                _loading(branch, flow, self.base),
                "Bypassed" if branch.bypassed else "InSvc",
            )

    def _shunts(self) -> Iterable[Sequence[object]]:
        for position, shunt in enumerate(self.case.switched_shunts, start=1):
            bus_voltage = self.magnitude[self.index[shunt.bus]]
            controlled = (
                self.magnitude[self.index[shunt.controlled_bus]]
                if shunt.controlled_bus in self.index
                else None
            )
            units = ", ".join(
                f"{stage.in_service}/{stage.units} x {stage.unit_mvar:g}" for stage in shunt.stages
            )
            if shunt.fixed:
                state = "Fixed"
            elif shunt.frozen:
                state = "Frozen"
            elif controlled is None:
                state = "NoControl"
            elif controlled < shunt.minimum_voltage_pu:
                state = "BelowBand"
            elif controlled > shunt.maximum_voltage_pu:
                state = "AboveBand"
            else:
                state = "InBand"
            yield (
                position,
                shunt.bus,
                "Line" if shunt.line else "Bus",
                shunt.line,
                "Fixed" if shunt.fixed else "Controlled",
                shunt.controlled_bus,
                shunt.minimum_voltage_pu,
                shunt.maximum_voltage_pu,
                controlled,
                units,
                shunt.current_q_mvar,
                shunt.current_q_mvar * bus_voltage**2,
                shunt.minimum_q_mvar,
                shunt.maximum_q_mvar,
                state,
            )


def _branch_kind(branch: Branch) -> str:
    if branch.is_csc:
        return "csc"
    if branch.is_transformer or branch.tap_max > branch.tap_min > 0.0:
        return "transformer"
    return "line"


def _loading(branch: Branch, flow: _Flow, base_mva: float) -> float:
    if branch.rating_mva <= 0.0:
        return 0.0
    return 100.0 * max(abs(flow.from_power), abs(flow.to_power)) * base_mva / branch.rating_mva


def _power_factor(power: complex) -> float:
    magnitude = abs(power)
    return abs(power.real) / magnitude if magnitude > 1e-9 else 1.0


def _reactive_type(power: complex) -> str:
    return "Inductive" if power.imag >= 0.0 else "Capacitive"


def _voltage_violation(value: float, minimum: float, maximum: float) -> str:
    if value > maximum + 1e-6:
        return "Vmax"
    if value < minimum - 1e-6:
        return "Vmin"
    return ""


def _write_excel(sheets: dict[str, SheetRows], output_path: Path) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    bold = Font(bold=True)
    for name, (headers, rows) in sheets.items():
        sheet = workbook.create_sheet(name)
        sheet.freeze_panes = "A2"
        sheet.append(list(headers))
        for cell in sheet[1]:
            cell.font = bold
        for row in rows:
            sheet.append([_excel_value(value) for value in row])
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, float):
                    cell.number_format = "0.0000"
        for column, header in enumerate(headers, start=1):
            letter = sheet.cell(row=1, column=column).column_letter
            sheet.column_dimensions[letter].width = max(len(str(header)) + 3, 11)
    workbook.save(output_path)


def _excel_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


__all__ = ["SHEET_HEADERS", "ExportSolution"]
