"""End-to-end tests on a real ANAREDE case (CASO01, about 12,600 buses)."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pytest
from openpyxl import load_workbook

import pf_tool
from pf_tool import ExportSolution, NewtonRaphsonPowerFlow, OptimizationPowerFlow
from pf_tool.exporter import SHEET_HEADERS
from pf_tool.models.bus import BusType
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case

CASE = Path(__file__).parent / "data" / "CASO01-FLOW.dat"


def _ipopt_available() -> bool:
    from pyomo.environ import SolverFactory

    return bool(SolverFactory("ipopt").available(exception_flag=False))


@pytest.fixture(scope="module")
def stored_voltage() -> np.ndarray:
    """Bus voltages stored in the case: the ANAREDE solution."""
    return np.array([bus.voltage_pu for bus in build_power_flow_case(parse_dat_file(CASE)).buses])


@pytest.fixture(scope="module", params=["simultaneous", "sequential"])
def solved(request: pytest.FixtureRequest) -> tuple[NewtonRaphsonPowerFlow, str]:
    solver = NewtonRaphsonPowerFlow(filename=CASE, control_method=request.param)
    output = io.StringIO()
    with redirect_stdout(output):
        solver.print_iterations = True
        solver.solve()
    return solver, output.getvalue()


def test_package_exports_and_version() -> None:
    assert pf_tool.__version__
    assert set(pf_tool.__all__) >= {
        "ExportSolution",
        "NewtonRaphsonPowerFlow",
        "OptimizationPowerFlow",
        "PowerFlowResult",
        "BusSolution",
    }


def test_newton_converges_close_to_anarede(
    solved: tuple[NewtonRaphsonPowerFlow, str], stored_voltage: np.ndarray
) -> None:
    solver, _ = solved
    result = solver.result
    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert len(result.buses) == len(stored_voltage)
    deviation = np.abs(np.abs(result.voltage) - stored_voltage)
    assert np.percentile(deviation, 99) < 0.01
    # Every generator still holding its voltage stays at its scheduled value.
    for bus, solution in zip(solver.case.buses, result.buses):
        if bus.bus_type == BusType.PV:
            assert solution.voltage_pu == pytest.approx(bus.voltage_pu, abs=1e-4)


def test_print_iterations_reports_every_pass(solved: tuple[NewtonRaphsonPowerFlow, str]) -> None:
    solver, output = solved
    assert output.startswith(f"Newton-Raphson power flow: {CASE.name}")
    assert "controls are stable" in output
    assert (
        output.rstrip()
        .splitlines()[-1]
        .startswith(f"Converged in {solver.result.iterations} iterations")
    )
    if solver.control_method == "simultaneous":
        assert "generators at a reactive limit" in output


@pytest.mark.parametrize("solved", ["simultaneous"], indirect=True)
def test_export_writes_every_sheet(
    solved: tuple[NewtonRaphsonPowerFlow, str], tmp_path: Path
) -> None:
    solver, _ = solved
    output = tmp_path / "nested" / "solution.xlsx"
    export = ExportSolution(solver, output_path=output)

    workbook = load_workbook(output, read_only=True)
    assert workbook.sheetnames == list(SHEET_HEADERS)
    summary = dict(export.sheets["Summary"][1])
    assert summary["Method"] == "Newton-Raphson"
    assert summary["Converged"] is True
    assert summary["Buses"] == len(solver.case.buses)
    for name, (headers, rows) in export.sheets.items():
        assert all(len(row) == len(headers) for row in rows), name
    assert export.sheets["HVDC"][1], "the case has LCC links"
    assert export.sheets["LTC"][1] and export.sheets["SVC"][1]


def test_export_rejects_unsolved_solver_and_unknown_format(
    small_case_file: Path, tmp_path: Path
) -> None:
    solver = NewtonRaphsonPowerFlow(filename=small_case_file)
    with pytest.raises(ValueError, match="solve"):
        ExportSolution(solver, output_path=tmp_path / "x.xlsx")
    with pytest.raises(ValueError, match="format"):
        ExportSolution(solver, output_path=tmp_path / "x.csv", format="csv")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"control_method": "outer"}, "control_method"),
        ({"tolerance": 0.0}, "tolerance"),
        ({"max_iterations": -1}, "max_iterations"),
        ({"max_control_passes": 0}, "max_control_passes"),
    ],
)
def test_newton_rejects_invalid_options(
    small_case_file: Path, arguments: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        NewtonRaphsonPowerFlow(filename=small_case_file, **arguments)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"tolerance": -1.0}, "tolerance"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_cpu_time": 0.0}, "max_cpu_time"),
        ({"max_control_passes": -1}, "max_control_passes"),
    ],
)
def test_optimization_rejects_invalid_options(
    small_case_file: Path, arguments: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        OptimizationPowerFlow(filename=small_case_file, **arguments)


@pytest.mark.skipif(not _ipopt_available(), reason="Ipopt is not installed")
def test_optimization_warm_start_matches_newton(stored_voltage: np.ndarray, tmp_path: Path) -> None:
    solver = OptimizationPowerFlow(filename=CASE, warm_start=True)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.warm_start_converged
    assert solver.nlp_solves >= 1
    deviation = np.abs(np.abs(result.voltage) - stored_voltage)
    assert np.percentile(deviation, 99) < 0.01
    export = ExportSolution(solver, output_path=tmp_path / "opt.xlsx", export=False)
    assert dict(export.sheets["Summary"][1])["Method"].startswith("Optimization")


def test_optimization_reports_a_missing_ipopt_executable(small_case_file: Path) -> None:
    solver = OptimizationPowerFlow(
        filename=small_case_file, warm_start=False, ipopt_executable="no-such-dir/no-such-ipopt"
    )
    with pytest.raises(RuntimeError, match="no-such-ipopt"):
        solver.solve()
