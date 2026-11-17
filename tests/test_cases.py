"""End-to-end tests on the IEEE 9-bus case (tests/data/IEEE9-FLOW.dat)."""

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

CASE = Path(__file__).parent / "data" / "IEEE9-FLOW.dat"


def _ipopt_available() -> bool:
    from pyomo.environ import SolverFactory

    return bool(SolverFactory("ipopt").available(exception_flag=False))


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
        "DCPowerFlow",
        "FastDecoupledPowerFlow",
        "NewtonRaphsonPowerFlow",
        "OptimizationPowerFlow",
        "PowerFlowResult",
        "BusSolution",
    }


def test_newton_solves_the_ieee9_case(solved: tuple[NewtonRaphsonPowerFlow, str]) -> None:
    solver, _ = solved
    result = solver.result
    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert [bus.number for bus in result.buses] == list(range(1, 10))
    # Generators hold their scheduled 1.05 pu and the slack angle stays at zero.
    for bus, solution in zip(solver.case.buses, result.buses):
        if bus.bus_type in (BusType.PV, BusType.SLACK):
            assert solution.voltage_pu == pytest.approx(1.05, abs=1e-6)
    assert result.buses[0].angle_degrees == pytest.approx(0.0)
    # The network absorbs the difference between generation and load.
    losses = sum(bus.active_injection_mw for bus in result.buses)
    assert 0.0 <= losses < 5.0


def test_newton_lu_option_solves_the_ieee9_case() -> None:
    solver = NewtonRaphsonPowerFlow(filename=CASE, lu_decomposition=True)

    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance


def test_both_control_methods_agree() -> None:
    voltages = [
        NewtonRaphsonPowerFlow(filename=CASE, control_method=method).solve().voltage
        for method in ("simultaneous", "sequential")
    ]
    assert np.allclose(*voltages, atol=1e-6)


def test_print_iterations_reports_every_pass(solved: tuple[NewtonRaphsonPowerFlow, str]) -> None:
    solver, output = solved
    assert output.startswith(f"Newton-Raphson power flow: {CASE.name}")
    assert "controls are stable" in output
    assert (
        output.rstrip()
        .splitlines()[-1]
        .startswith(f"Converged in {solver.result.iterations} iterations")
    )


def test_export_writes_every_sheet(components_file: Path, tmp_path: Path) -> None:
    solver = NewtonRaphsonPowerFlow(filename=components_file)
    solver.solve()
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
    assert export.sheets["HVDC"][1], "the case has an LCC link"
    assert export.sheets["LTC"][1] and export.sheets["SVC"][1]


def test_export_rejects_unsolved_solver_and_unknown_format(
    ieee9_file: Path, tmp_path: Path
) -> None:
    solver = NewtonRaphsonPowerFlow(filename=ieee9_file)
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
def test_newton_rejects_invalid_options(ieee9_file: Path, arguments: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        NewtonRaphsonPowerFlow(filename=ieee9_file, **arguments)


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
    ieee9_file: Path, arguments: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        OptimizationPowerFlow(filename=ieee9_file, **arguments)


@pytest.mark.skipif(not _ipopt_available(), reason="Ipopt is not installed")
def test_optimization_warm_start_matches_newton(tmp_path: Path) -> None:
    newton = NewtonRaphsonPowerFlow(filename=CASE).solve()
    solver = OptimizationPowerFlow(filename=CASE, warm_start=True)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.warm_start_converged
    assert solver.nlp_solves >= 1
    assert np.abs(np.abs(result.voltage) - np.abs(newton.voltage)).max() < 1e-4
    export = ExportSolution(solver, output_path=tmp_path / "opt.xlsx", export=False)
    assert dict(export.sheets["Summary"][1])["Method"].startswith("Optimization")


def test_optimization_reports_a_missing_ipopt_executable(ieee9_file: Path) -> None:
    solver = OptimizationPowerFlow(
        filename=ieee9_file, warm_start=False, ipopt_executable="no-such-dir/no-such-ipopt"
    )
    with pytest.raises(RuntimeError, match="no-such-ipopt"):
        solver.solve()
