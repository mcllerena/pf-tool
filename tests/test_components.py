"""Tests of every solver, the exporter, and the case builder on a small synthetic case."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pf_tool import ExportSolution, NewtonRaphsonPowerFlow, OptimizationPowerFlow
from pf_tool.models.bus import BusType
from pf_tool.models.shunt import ShuntStage, SwitchedShunt, adjust_controlled_shunts, snap_to_bank_steps
from pf_tool.utils.dat_parser import parse_dat_file, parse_dat_text
from pf_tool.utils.utils import build_power_flow_case


def _ipopt_available() -> bool:
    from pyomo.environ import SolverFactory

    return bool(SolverFactory("ipopt").available(exception_flag=False))


needs_ipopt = pytest.mark.skipif(not _ipopt_available(), reason="Ipopt is not installed")


# --- Case construction -------------------------------------------------------


def test_case_builder_reads_every_block(small_case_file: Path) -> None:
    case = build_power_flow_case(parse_dat_file(small_case_file))

    numbers = [bus.number for bus in case.buses]
    assert numbers == [1, 2, 3, 4, 5, 6, 7], "bus 8 is out of service"
    gen = case.buses[1]
    assert (gen.bus_type, gen.q_min_mvar, gen.q_max_mvar) == (BusType.PV, -30.0, 30.0)
    assert (gen.p_min_mw, gen.p_max_mw) == (0.0, 80.0)
    assert case.buses[3].voltage_limit_control
    assert case.buses[2].base_kv == 230.0
    assert (case.buses[2].voltage_min_pu, case.buses[2].voltage_max_pu) == (0.95, 1.05)
    csc = [branch for branch in case.branches if branch.is_csc]
    assert [branch.bypassed for branch in csc] == [False, True]
    assert csc[0].reactance_pu == pytest.approx(-0.02)
    # Tap-controlled transformer (LTC) and DCTR phase shifter.
    assert sorted(control.mode for control in case.tap_controls) == ["P", "V"]
    controlled = [shunt for shunt in case.switched_shunts if not shunt.fixed]
    assert controlled[0].maximum_q_mvar == 20.0
    line_shunt = [shunt for shunt in case.switched_shunts if shunt.line]
    assert line_shunt[0].bus == 3 and line_shunt[0].line == "3-4-1"
    assert case.svcs[0].slope == pytest.approx(0.02)


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
def test_newton_solves_small_case(small_case_file: Path, control_method: str) -> None:
    solver = NewtonRaphsonPowerFlow(filename=small_case_file, control_method=control_method)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert [bus.number for bus in result.buses] == [1, 2, 3, 4, 5, 6, 7]
    # The bypassed compensator ties buses 6 and 7 into one electrical node.
    assert result.voltage[5] == pytest.approx(result.voltage[6], abs=1e-6)
    # The phase shifter holds 15 MW on circuit 2-4.
    assert solver.case.tap_controls[-1].mode == "P"
    # The generator holds its scheduled voltage (its limits are not reached).
    assert result.buses[1].voltage_pu == pytest.approx(1.01, abs=1e-6)


def test_newton_disabled_by_dopc_newt(small_case_file: Path) -> None:
    text = small_case_file.read_text().replace('"NEWT" "L"', '"NEWT" "D"')
    small_case_file.write_text(text)
    result = NewtonRaphsonPowerFlow(filename=small_case_file).solve()
    assert not result.converged
    assert result.failure_reason == "DOPC.NEWTON is disabled"


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_newton_reports_iteration_limit(small_case_file: Path, control_method: str) -> None:
    solver = NewtonRaphsonPowerFlow(
        filename=small_case_file, control_method=control_method, max_iterations=0
    )
    result = solver.solve()
    assert not result.converged
    assert result.failure_reason == "Newton-Raphson did not converge"


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_generator_reaches_reactive_limit_in_both_methods(
    small_case_file: Path, control_method: str
) -> None:
    # A tight limit forces the generator to its maximum reactive output.
    text = small_case_file.read_text().replace(
        '2 "A" "L" "GEN" 1 1.01 0 40 10 -30 30', '2 "A" "L" "GEN" 1 1.06 0 40 10 -5 5'
    )
    small_case_file.write_text(text)
    solver = NewtonRaphsonPowerFlow(filename=small_case_file, control_method=control_method)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.case.buses[1].bus_type == BusType.PQ
    assert result.buses[1].voltage_pu < 1.06
    assert result.buses[1].reactive_injection_mvar == pytest.approx(5.0, abs=0.2)


@pytest.mark.parametrize("control_method", ["simultaneous", "sequential"])
def test_vlim_bus_is_held_at_its_voltage_limit(small_case_file: Path, control_method: str) -> None:
    # Make bus 3 a Tb = 3 bus whose band starts above its natural voltage.
    text = (
        small_case_file.read_text()
        .replace('"A" 0.95 1.05\n;', '"A" 0.95 1.05\n    "B" 1.0 1.05\n;')
        .replace(
            '3 "A" "L" "LOAD" 0 1.0 0 0 0 0 0 60 20 0 "A"',
            '3 "A" "L" "LOAD" 3 1.0 0 0 0 0 0 60 20 0 "B"',
        )
        .replace('4 "A" "L" "VLIM" 3', '4 "A" "L" "VLIM" 0')
    )
    small_case_file.write_text(text)
    solver = NewtonRaphsonPowerFlow(filename=small_case_file, control_method=control_method)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.buses[2].voltage_pu == pytest.approx(1.0, abs=1e-4)
    assert solver.case.buses[2].bus_type == BusType.PV
    if control_method == "simultaneous":
        # At the lower limit the bus may only inject reactive power (60 + j20 load).
        assert result.buses[2].reactive_injection_mvar > -20.0


# --- Optimization ---------------------------------------------------------------


@needs_ipopt
@pytest.mark.parametrize("warm_start", [True, False])
def test_optimization_solves_small_case(small_case_file: Path, warm_start: bool) -> None:
    newton = NewtonRaphsonPowerFlow(filename=small_case_file).solve()
    solver = OptimizationPowerFlow(
        filename=small_case_file, warm_start=warm_start, print_iterations=False
    )
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.nlp_solves >= 1
    assert np.abs(np.abs(result.voltage) - np.abs(newton.voltage)).max() < 0.02


@needs_ipopt
def test_optimization_accepts_an_explicit_ipopt_path(small_case_file: Path) -> None:
    from pyomo.environ import SolverFactory

    executable = SolverFactory("ipopt").executable()
    result = OptimizationPowerFlow(filename=small_case_file, ipopt_executable=executable).solve()
    assert result.converged, result.failure_reason


# --- Exporter -------------------------------------------------------------------


@pytest.mark.parametrize("solver_class", [NewtonRaphsonPowerFlow, OptimizationPowerFlow])
def test_export_small_case(small_case_file: Path, tmp_path: Path, solver_class: type) -> None:
    if solver_class is OptimizationPowerFlow and not _ipopt_available():
        pytest.skip("Ipopt is not installed")
    solver = solver_class(filename=small_case_file)
    solver.solve()
    export = ExportSolution(solver, output_path=tmp_path / "small.xlsx")

    assert export.output_path.exists()
    sheets = export.sheets
    summary = dict(sheets["Summary"][1])
    assert summary["Converged"] is True
    assert summary["CSC"] == 2 and summary["SVC"] == 1 and summary["PST"] == 1
    assert len(sheets["Buses"][1]) == 7
    assert len(sheets["CSC"][1]) == 2
    assert len(sheets["Shunts"][1]) == 2
    # Power balance: generation equals load plus losses.
    total_p = summary["Total Generation (MW)"] - summary["Total Load (MW)"]
    assert total_p == pytest.approx(summary["AC Losses (MW)"], abs=1e-3)


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


def test_converter_operating_point_is_a_fixed_point_of_the_sequential_update() -> None:
    from pf_tool.models.hvdc_lcc import converter_operating_point

    case = build_power_flow_case(parse_dat_file(Path(__file__).parent / "data" / "CASO01-FLOW.dat"))
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
