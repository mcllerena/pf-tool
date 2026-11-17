"""Every solver, the exporter, and the case builder on the IEEE 9-bus case with all devices."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pf_tool import ExportSolution, NewtonRaphsonPowerFlow, OptimizationPowerFlow
from pf_tool.models.bus import BusType
from pf_tool.models.shunt import (
    ShuntStage,
    SwitchedShunt,
    adjust_controlled_shunts,
    snap_to_bank_steps,
)
from pf_tool.utils.dat_parser import parse_dat_file, parse_dat_text
from pf_tool.utils.utils import build_power_flow_case


def _ipopt_available() -> bool:
    from pyomo.environ import SolverFactory

    return bool(SolverFactory("ipopt").available(exception_flag=False))


needs_ipopt = pytest.mark.skipif(not _ipopt_available(), reason="Ipopt is not installed")


# --- Case construction -------------------------------------------------------


def test_case_builder_reads_every_block(components_file: Path) -> None:
    case = build_power_flow_case(parse_dat_file(components_file))

    numbers = [bus.number for bus in case.buses]
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12], "bus 10 is out of service"
    gen = case.buses[1]
    assert (gen.bus_type, gen.q_min_mvar, gen.q_max_mvar) == (BusType.PV, -300.0, 300.0)
    assert (gen.p_min_mw, gen.p_max_mw) == (0.0, 250.0)
    assert case.buses[2].base_kv == 230.0
    assert (case.buses[2].voltage_min_pu, case.buses[2].voltage_max_pu) == (0.95, 1.10)
    csc = [branch for branch in case.branches if branch.is_csc]
    assert [branch.bypassed for branch in csc] == [True]
    # DCSC records on an existing DLIN circuit add their reactance in series.
    line = next(b for b in case.branches if (b.from_bus, b.to_bus) == (2, 7))
    assert line.reactance_pu == pytest.approx((0.0625 - 2.425) * 0.01)
    # Tap-controlled transformer (LTC) and DCTR phase shifter.
    assert sorted(control.mode for control in case.tap_controls) == ["P", "V"]
    controlled = [shunt for shunt in case.switched_shunts if not shunt.fixed]
    assert controlled[0].maximum_q_mvar == 20.0
    line_shunt = [shunt for shunt in case.switched_shunts if shunt.line]
    assert line_shunt[0].bus == 9 and line_shunt[0].line == "9-6-1"
    assert case.svcs[0].slope == pytest.approx(0.01)
    assert [link.p_dc_mw for link in case.hvdc_links] == [30.0]


def test_model_package_exposes_ybus_queries(components_file: Path) -> None:
    from pf_tool.models import build_ybus, calculate_power
    from pf_tool.models.ybus import build_ybus as module_build_ybus
    from pf_tool.utils.network import build_ybus as legacy_build_ybus

    case = build_power_flow_case(parse_dat_file(components_file))
    ybus = build_ybus(case)
    injections = calculate_power(ybus, case.initial_voltage)

    assert build_ybus is module_build_ybus is legacy_build_ybus
    assert ybus.shape == (len(case.buses), len(case.buses))
    assert ybus.nnz > 0
    assert injections.shape == (len(case.buses),)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no blocks here", "No AMPL-style"),
        ("param: DBAR: :=\n;", "no column names"),
        ("param: DBAR: No :=\n 1\n;\nparam: DBAR: No Name :=\n 2 x\n;", "inconsistent"),
        ("param: DBAR: No Name :=\n 1 x\nparam: DLIN: I :=\n 1\n;", "terminator"),
        ("param: DBAR: No Name :=\n 1 x 2\n;", "values for 2 columns"),
    ],
)
def test_parser_rejects_malformed_blocks(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_dat_text(text)


def test_parser_reads_latin1_files(tmp_path: Path) -> None:
    path = tmp_path / "latin1.dat"
    path.write_bytes('param: DBAR: No Name :=\n 1 "SÃO"\n;\n'.encode("latin-1"))
    assert parse_dat_file(path)["DBAR"][0]["Name"] == "SÃO"


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ('param: DCTE: Mn Val :=\n "BASE" 0\n;\nparam: DBAR: No :=\n 1\n;', "base power"),
        ("param: DBAR: No Tb :=\n 1 2\n 1 0\n;", "Duplicate"),
        ('param: DBAR: No O :=\n 1 "D"\n;', "no in-service buses"),
        ("param: DBAR: No Tb :=\n 1 2\n;", "no in-service branches"),
        ("param: DBAR: No Tb :=\n 1 0\n 2 0\n;\nparam: DLIN: I J X :=\n 1 2 5\n;", "reference"),
    ],
)
def test_case_builder_rejects_inconsistent_data(blocks: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_power_flow_case(parse_dat_text(blocks))


# --- Newton-Raphson -------------------------------------------------------------


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_newton_solves_components_case(components_file: Path, control_method: str) -> None:
    solver = NewtonRaphsonPowerFlow(filename=components_file, control_method=control_method)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert [bus.number for bus in result.buses] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12]
    # The bypassed compensator ties buses 8 and 12 into one electrical node.
    assert result.voltage[7] == pytest.approx(result.voltage[10], abs=1e-6)
    # The phase shifter on circuit 7-8 is part of the solve.
    assert solver.case.tap_controls[-1].mode == "P"
    # The generator holds its scheduled voltage (its limits are not reached).
    assert result.buses[1].voltage_pu == pytest.approx(1.05, abs=1e-6)


def test_newton_disabled_by_dopc_newt(components_file: Path) -> None:
    text = components_file.read_text().replace('"NEWT" "L"', '"NEWT" "D"')
    components_file.write_text(text)
    result = NewtonRaphsonPowerFlow(filename=components_file).solve()
    assert not result.converged
    assert result.failure_reason == "DOPC.NEWTON is disabled"


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_newton_reports_iteration_limit(components_file: Path, control_method: str) -> None:
    solver = NewtonRaphsonPowerFlow(
        filename=components_file, control_method=control_method, max_iterations=0
    )
    result = solver.solve()
    assert not result.converged
    assert result.failure_reason == "Newton-Raphson did not converge"


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_generator_reaches_reactive_limit_in_both_methods(
    components_file: Path, control_method: str
) -> None:
    # A tight limit forces the generator to its maximum reactive output.
    text = components_file.read_text().replace(
        '3 "A" "L" "BUS3" 1 1.050 0.11 85 0 -300 300', '3 "A" "L" "BUS3" 1 1.080 0.11 85 0 -5 5'
    )
    components_file.write_text(text)
    solver = NewtonRaphsonPowerFlow(filename=components_file, control_method=control_method)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.case.buses[2].bus_type == BusType.PQ
    assert result.buses[2].voltage_pu < 1.08
    assert result.buses[2].reactive_injection_mvar == pytest.approx(5.0, abs=0.2)


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_vlim_bus_is_held_at_its_voltage_limit(components_file: Path, control_method: str) -> None:
    # Make bus 8 a Tb = 3 bus whose band starts above its natural voltage.
    text = (
        components_file.read_text()
        .replace('"A" 0.95 1.10\n;', '"A" 0.95 1.10\n    "B" 1.05 1.10\n;')
        .replace(
            '8 "A" "L" "BUS8" 0 1.050 0.06 0 0 0 0 100 35 0 "A"',
            '8 "A" "L" "BUS8" 3 1.050 0.06 0 0 0 0 100 35 0 "B"',
        )
    )
    components_file.write_text(text)
    solver = NewtonRaphsonPowerFlow(filename=components_file, control_method=control_method)
    result = solver.solve()

    assert solver.case.buses[7].voltage_limit_control
    assert result.converged, result.failure_reason
    assert result.buses[7].voltage_pu == pytest.approx(1.05, abs=1e-4)
    assert solver.case.buses[7].bus_type == BusType.PV
    if control_method == "simultaneous":
        # At the lower limit the bus may only inject reactive power (100 + j35 load).
        assert result.buses[7].reactive_injection_mvar > -35.0


# --- Optimization ---------------------------------------------------------------


def test_optimization_model_is_satisfied_by_the_newton_solution(components_file: Path) -> None:
    # No Ipopt needed: the NLP equations are evaluated at the Newton point.
    from pf_tool.optimization import (
        build_optimization_model,
        initial_svc_regions,
        solution_metrics,
        update_svc_regions,
    )

    solver = NewtonRaphsonPowerFlow(filename=components_file, control_method="sequential")
    result = solver.solve()
    reduced = solver.reduction
    representatives = np.asarray(reduced.representatives)
    voltage = result.voltage[representatives] / reduced.voltage_scales[representatives]
    regions = initial_svc_regions(reduced.case, voltage)
    model = build_optimization_model(
        reduced.case, initial_voltage=voltage, tolerance=solver.tolerance, svc_regions=regions
    )

    metrics = solution_metrics(model)
    assert metrics["converged"], metrics
    assert not update_svc_regions(model, regions)


def test_optimization_helpers_without_ipopt(components_file: Path) -> None:
    from pf_tool.optimization import (
        _enforce_generator_q_limits,
        _parse_ipopt_iterations,
        _store_svc_outputs,
        build_optimization_model,
    )
    from pf_tool.utils.network import build_ybus

    assert _parse_ipopt_iterations("Number of Iterations....: 12\n") == 12
    assert _parse_ipopt_iterations("no summary") is None

    # Generator 3 at 1.08 pu needs more than its 5 Mvar limit.
    text = components_file.read_text().replace(
        '3 "A" "L" "BUS3" 1 1.050 0.11 85 0 -300 300', '3 "A" "L" "BUS3" 1 1.080 0.11 85 0 -5 5'
    )
    components_file.write_text(text)
    case = build_power_flow_case(parse_dat_file(components_file))
    voltage = case.initial_voltage
    assert _enforce_generator_q_limits(case, build_ybus(case), voltage)
    assert case.buses[2].bus_type == BusType.PQ
    assert case.buses[2].q_generation_mvar in (-5.0, 5.0)

    # Solved SVC outputs are written back to the devices and bus injections.
    model = build_optimization_model(case, initial_voltage=voltage)
    for index in model.SVC:
        model.qsvc[index].value = 0.25
    device = case.svcs[int(model._svc_data[next(iter(model.SVC))]["svc_index"])]
    _store_svc_outputs(model, case)
    assert device.q_mvar == pytest.approx(25.0)


@needs_ipopt
@pytest.mark.parametrize("warm_start", [True, False])
def test_optimization_solves_components_case(components_file: Path, warm_start: bool) -> None:
    newton = NewtonRaphsonPowerFlow(filename=components_file).solve()
    solver = OptimizationPowerFlow(
        filename=components_file, warm_start=warm_start, print_iterations=False
    )
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.nlp_solves >= 1
    deviation = np.abs(np.abs(result.voltage) - np.abs(newton.voltage))
    if warm_start:
        assert deviation.max() < 1e-4
    else:
        # Without the Newton start the LTC keeps its stored tap, so only the
        # regulated bus 11 (index 9) may differ noticeably.
        assert np.delete(deviation, 9).max() < 0.01


@needs_ipopt
def test_optimization_accepts_an_explicit_ipopt_path(components_file: Path) -> None:
    from pyomo.environ import SolverFactory

    executable = SolverFactory("ipopt").executable()
    result = OptimizationPowerFlow(filename=components_file, ipopt_executable=executable).solve()
    assert result.converged, result.failure_reason


# --- Exporter -------------------------------------------------------------------


@pytest.mark.parametrize("solver_class", [NewtonRaphsonPowerFlow, OptimizationPowerFlow])
def test_export_components_case(components_file: Path, tmp_path: Path, solver_class: type) -> None:
    if solver_class is OptimizationPowerFlow and not _ipopt_available():
        pytest.skip("Ipopt is not installed")
    solver = solver_class(filename=components_file)
    solver.solve()
    export = ExportSolution(solver, output_path=tmp_path / "components.xlsx")

    assert export.output_path.exists()
    sheets = export.sheets
    summary = dict(sheets["Summary"][1])
    assert summary["Converged"] is True
    assert summary["CSC"] == 1 and summary["SVC"] == 2 and summary["PST"] == 1
    assert len(sheets["Buses"][1]) == 11
    assert len(sheets["CSC"][1]) == 1
    assert len(sheets["Shunts"][1]) == 2
    # Power balance: generation equals load plus AC and DC (LCC link) losses.
    total_p = summary["Total Generation (MW)"] - summary["Total Load (MW)"]
    dc_losses = sum(
        link.rectifier_active_mw - link.inverter_active_mw for link in solver.case.hvdc_links
    )
    assert dc_losses > 0.0
    assert total_p == pytest.approx(summary["AC Losses (MW)"] + dc_losses, abs=1e-3)


# --- Switched-shunt helpers -----------------------------------------------------


def _shunt(stages: list[ShuntStage], current: float = 0.0) -> SwitchedShunt:
    return SwitchedShunt(
        bus=1,
        controlled_bus=1,
        initial_q_mvar=current,
        minimum_q_mvar=sum(min(0.0, s.unit_mvar * s.units) for s in stages),
        maximum_q_mvar=sum(max(0.0, s.unit_mvar * s.units) for s in stages),
        minimum_voltage_pu=0.98,
        maximum_voltage_pu=1.02,
        fixed=False,
        current_q_mvar=current,
        stages=stages,
    )


def test_snap_to_bank_steps_picks_nearest_combination() -> None:
    shunt = _shunt([ShuntStage(10.0, 3, 0), ShuntStage(-5.0, 2, 0)])
    assert snap_to_bank_steps(shunt, 14.0) == pytest.approx(15.0)
    assert [s.in_service for s in shunt.stages] == [2, 1]
    assert snap_to_bank_steps(_shunt([]), 10.0) == 0.0


def test_snap_to_bank_steps_fills_large_banks_greedily() -> None:
    # 13 stages of 2 units: too many combinations to enumerate.
    stages = [ShuntStage(float(10 + i), 1, 0) for i in range(13)] + [ShuntStage(0.0, 1, 1)]
    shunt = _shunt(stages)
    change = snap_to_bank_steps(shunt, 45.0)
    assert change == pytest.approx(shunt.current_q_mvar)
    assert abs(shunt.current_q_mvar - 45.0) <= 11.0


def test_sequential_shunt_switching_steps_and_freezes() -> None:
    from pf_tool.models.bus import Bus
    from pf_tool.models.case import PowerFlowCase

    shunt = _shunt([ShuntStage(10.0, 2, 0), ShuntStage(-10.0, 1, 1)], current=-10.0)
    case = PowerFlowCase(
        base_mva=100.0,
        buses=[Bus(1, "B", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.1)],
        branches=[],
        switched_shunts=[shunt],
    )
    low = np.array([0.95 + 0j])
    assert adjust_controlled_shunts(case, low)  # removes the reactor first
    assert [s.in_service for s in shunt.stages] == [0, 0]
    assert adjust_controlled_shunts(case, low)  # then inserts a capacitor
    assert shunt.current_q_mvar == pytest.approx(10.0)
    assert case.buses[0].shunt_susceptance_pu == pytest.approx(0.1)
    assert not adjust_controlled_shunts(case, np.array([1.0 + 0j]))  # inside the band
    assert not adjust_controlled_shunts(case, np.array([1.05 + 0j]))  # reversal freezes it
    assert shunt.frozen


# --- LCC converter --------------------------------------------------------------


def test_converter_operating_point_is_a_fixed_point_of_the_sequential_update(
    components_file: Path,
) -> None:
    from pf_tool.models.hvdc_lcc import converter_operating_point

    case = build_power_flow_case(parse_dat_file(components_file))
    link = next(link for link in case.hvdc_links if link.vbase_kv > 10.0)
    q, tap, overlap = converter_operating_point(link, True, 1.0)
    q_again, tap_again, _ = converter_operating_point(link, True, 1.0)
    assert (q, tap) == (q_again, tap_again)
    assert q > 0.0 and overlap > 0.0
    assert math.isclose(link.p_dc_mw, link.current_ka * link.vdc_rectifier_kv, rel_tol=1e-9) or (
        link.rectifier_slack
    )
    # A converter on its tap limit draws more reactive power at lower voltage.
    low, _, _ = converter_operating_point(link, True, 0.9)
    high, _, _ = converter_operating_point(link, True, 1.1)
    assert low != high
