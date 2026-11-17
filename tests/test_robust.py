from __future__ import annotations

from pathlib import Path

import pytest

from pf_tool import RobustACPowerFlow, RobustPowerFlowResult
from pf_tool.utils.dat_parser import parse_dat_file

IEEE9 = Path(__file__).parent / "data" / "IEEE9-FLOW.dat"


def test_robust_solver_runs_fast_decoupled_and_writes_solved_case(tmp_path: Path) -> None:
    solved_path = tmp_path / "IEEE9-FLOW-solved.dat"
    solver = RobustACPowerFlow(IEEE9, solved_filename=solved_path)

    result = solver.solve()

    assert isinstance(result, RobustPowerFlowResult)
    assert result.converged, result.failure_reason
    assert result.full_load_converged
    assert result.load_factor == 1.0
    assert result.load_shed_active_mw == 0.0
    assert result.load_shed_reactive_mvar == 0.0
    assert solver.fast_decoupled_solver is not None
    assert result.solved_case_path == solved_path
    assert solved_path.is_file()

    source_buses = {
        row["No"]: row for row in parse_dat_file(IEEE9)["DBAR"]
    }
    solved_buses = {row["No"]: row for row in parse_dat_file(solved_path)["DBAR"]}
    for number, source in source_buses.items():
        solved = solved_buses[number]
        assert float(solved["Pl"]) == pytest.approx(float(source.get("Pl", 0.0)))
        assert float(solved["Ql"]) == pytest.approx(float(source.get("Ql", 0.0)))
    assert all(float(row["V"]) > 0.0 for row in solved_buses.values())


def test_robust_solver_does_not_save_an_unconverged_case(tmp_path: Path) -> None:
    solved_path = tmp_path / "IEEE9-FLOW-solved.dat"
    solver = RobustACPowerFlow(
        IEEE9,
        solved_filename=solved_path,
        max_load_shedding_fraction=0.0,
        max_iterations=0,
        max_attempts=1,
    )

    result = solver.solve()

    assert not result.converged
    assert result.failure_reason is not None
    assert not solved_path.exists()
