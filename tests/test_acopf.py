from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pyomo.environ import SolverFactory

import pf_tool.acopf as acopf_module
from pf_tool import (
    ACOPFViolation,
    ACOptimalPowerFlow,
    ACOptimalPowerFlowResult,
    ExportSolution,
    PowerFlowResult,
)
from pf_tool.models.bus import BusType
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case

DATA = Path(__file__).parent / "data"
IEEE9 = DATA / "IEEE9-FLOW.dat"
IPOPT_AVAILABLE = bool(SolverFactory("ipopt").available(exception_flag=False))


def _two_area_shedding_case(path: Path) -> Path:
    path.write_text(
        """
param: DCTE: Mn Val :=
    "BASE" 100
    "TEPA" 0.01
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh Are :=
    1 "A" "L" "SLACK" 2 1 0 30 0 -100 100 0 0 0 1
    2 "A" "L" "GEN" 1 1 0 20 0 -100 100 0 0 0 1
    3 "A" "L" "AREA-1-LOAD" 0 1 0 0 0 0 0 40 15 0 1
    4 "A" "L" "AREA-2-LOAD" 0 1 0 0 0 0 0 40 15 0 2
;
param: DGER: No O Pgn Pgm :=
    1 "A" 0 30
    2 "A" 0 20
;
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh Cn :=
    1 2 1 "A" "L" "L" "L" 0 10 0 1 0 0
    1 3 1 "A" "L" "L" "L" 0 10 0 1 0 0
    1 4 1 "A" "L" "L" "L" 0 10 0 1 0 0
;
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_acopf_solves_and_returns_labeled_duals(tmp_path: Path) -> None:
    initial_case = build_power_flow_case(parse_dat_file(IEEE9))
    scheduled_generation = {
        bus.number: bus.p_generation_mw
        for bus in initial_case.buses
        if bus.bus_type != BusType.SLACK
    }
    solver = ACOptimalPowerFlow(IEEE9)

    result = solver.solve()

    assert isinstance(result, ACOptimalPowerFlowResult)
    assert isinstance(result, PowerFlowResult)
    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert result.objective_value >= -1e-4
    assert result.termination_condition in {"optimal", "locallyOptimal", "feasible"}
    assert result.warm_start_iterations > 0
    for bus in solver.case.buses:
        if bus.number in scheduled_generation:
            assert bus.p_generation_mw == pytest.approx(scheduled_generation[bus.number])

    duals = solver.get_result_duals(result)
    assert len(duals["active_power_balance"]) == len(initial_case.buses)
    assert len(duals["reactive_power_balance"]) == len(initial_case.buses)
    assert all(
        np.isfinite(value)
        for category in duals.values()
        if isinstance(category, dict)
        for value in category.values()
        if isinstance(value, (float, int))
    )
    assert solver.get_result_duals() == duals

    export = ExportSolution(solver, output_path=tmp_path / "acopf.xlsx", export=False)
    summary = dict(export.sheets["Summary"][1])
    assert summary["Method"].startswith("ACOPF (Ipopt")


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_get_violations_reports_solved_case_status() -> None:
    solver = ACOptimalPowerFlow(IEEE9)
    with pytest.raises(ValueError, match="solve"):
        solver.get_violations()

    result = solver.solve()
    violations = solver.get_violations(result)

    assert all(isinstance(item, ACOPFViolation) for item in violations)
    assert all(item.amount > 0.0 for item in violations)
    assert not any(
        item.category in {"active_power_balance", "reactive_power_balance"} for item in violations
    )
    assert solver.get_violations() == violations


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_acopf_supports_loss_minimization() -> None:
    result = ACOptimalPowerFlow(IEEE9, objective="loss_minimization").solve()

    assert result.converged, result.failure_reason
    assert np.isfinite(result.objective_value)


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_acopf_passes_clu_to_newton_warm_start(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_solvers: list[str] = []

    def capture_newton_solver(*args: Any, **kwargs: Any) -> Any:
        requested_solvers.append(str(kwargs.get("linear_solver")))
        return SimpleNamespace(solve=lambda: SimpleNamespace(converged=False, iterations=0))

    monkeypatch.setattr(acopf_module, "NewtonRaphsonPowerFlow", capture_newton_solver)
    solver = ACOptimalPowerFlow(IEEE9, warm_start_linear_solver="clu")

    result = solver.solve()

    assert requested_solvers == ["clu"]
    assert result.converged, result.failure_reason


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_acopf_supports_restoration_and_progressive_tightening() -> None:
    solver = ACOptimalPowerFlow(
        IEEE9,
        feasibility_restoration=True,
        progressive_tightening=True,
    )

    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.feasibility_restoration_iterations > 0
    assert result.progressive_tightening_stages == 3
    assert result.iterations >= result.feasibility_restoration_iterations
    assert solver._model is not None
    assert solver._model._limit_relaxation == 0.0
    assert not solver._model._feasibility_restoration


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"objective": "economic"}, "objective"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_cpu_time": 0.0}, "max_cpu_time"),
        ({"tolerance": 0.0}, "tolerance"),
        ({"voltage_violation_penalty": 0.0}, "voltage_violation_penalty"),
        ({"max_load_shedding_fraction": 1.1}, "max_load_shedding_fraction"),
        ({"load_shedding_penalty": 0.0}, "load_shedding_penalty"),
        ({"load_shedding_equity_beta": 1.1}, "load_shedding_equity_beta"),
        ({"warm_start_linear_solver": "unknown"}, "warm_start_linear_solver"),
    ],
)
def test_acopf_rejects_invalid_options(arguments: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ACOptimalPowerFlow(IEEE9, **arguments)


@pytest.mark.skipif(not IPOPT_AVAILABLE, reason="Ipopt is not installed")
def test_acopf_sheds_load_with_area_equity_cap(tmp_path: Path) -> None:
    solver = ACOptimalPowerFlow(
        _two_area_shedding_case(tmp_path / "area_shedding.dat"),
        warm_start=False,
        max_load_shedding_fraction=0.5,
        load_shedding_equity_beta=0.3,
    )

    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.load_shedding_mw
    assert set(result.load_shedding_mw) == {3, 4}
    assert result.area_shedding_fraction.keys() == {1, 2}
    assert result.load_shedding_equity_gini is not None
    assert result.load_shedding_equity_gini <= 0.3 + 1e-5
    assert result.max_mismatch <= solver.tolerance
