from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pf_tool import DCPowerFlow, DCPowerFlowResult
from pf_tool.models.bus import BusType

DATA = Path(__file__).parent / "data"
IEEE9 = DATA / "IEEE9-FLOW.dat"


def _two_bus_case(tmp_path: Path) -> Path:
    path = tmp_path / "two_bus.dat"
    path.write_text(
        """
param: DCTE: Mn Val :=
    "BASE" 100
    "TEPA" 0.01
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh :=
    1 "A" "L" "SLACK" 2 1 0 0 0 0 0 0 0 0
    2 "A" "L" "LOAD" 0 1 0 0 0 0 0 50 0 0
;
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh :=
    1 2 1 "A" "L" "L" "L" 0 10 0 1.1 -0.5729577951
;
""",
        encoding="utf-8",
    )
    return path


def test_dc_solves_two_bus_equation_and_reports_oriented_flow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    solver = DCPowerFlow(_two_bus_case(tmp_path), print_iterations=True)
    result = solver.solve()

    assert isinstance(result, DCPowerFlowResult)
    assert result.converged
    assert result.iterations == 1
    assert result.reference_buses == (1,)
    assert result.angles_radians[0] == pytest.approx(0.0)
    assert result.angles_radians[1] == pytest.approx(-0.065, abs=1e-10)
    assert result.branch_flows[0].active_power_mw == pytest.approx(50.0)
    assert result.buses[0].active_injection_mw == pytest.approx(50.0)
    assert result.buses[1].active_injection_mw == pytest.approx(-50.0)
    assert np.abs(result.voltage) == pytest.approx(np.ones(2))
    assert "Direct linear solves: 1" in capsys.readouterr().out


def test_dc_reference_bus_can_be_overridden(tmp_path: Path) -> None:
    solver = DCPowerFlow(_two_bus_case(tmp_path), reference_bus=2)

    result = solver.solve()

    assert result.reference_buses == (2,)
    assert result.angles_radians[1] == pytest.approx(0.0)
    assert result.max_mismatch <= solver.tolerance


def test_dc_rejects_invalid_tolerance_and_reference_bus(tmp_path: Path) -> None:
    case = _two_bus_case(tmp_path)

    with pytest.raises(ValueError, match="tolerance"):
        DCPowerFlow(case, tolerance=0.0)
    with pytest.raises(ValueError, match="Reference bus 999"):
        DCPowerFlow(case, reference_bus=999)


def test_dc_rejects_zero_reactance_branch(tmp_path: Path) -> None:
    solver = DCPowerFlow(_two_bus_case(tmp_path))
    solver.case.branches[0].reactance_pu = 0.0

    with pytest.raises(ValueError, match="zero reactance"):
        solver.solve()


def test_dc_converges_on_ieee9_case() -> None:
    solver = DCPowerFlow(IEEE9)
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.iterations == 1
    assert result.max_mismatch <= solver.tolerance
    assert len(result.branch_flows) == len(solver.case.branches)
    assert result.reference_buses == (1,)
    assert all(
        solver.case.buses[solver.case.bus_index[number]].bus_type == BusType.SLACK
        for number in result.reference_buses
    )
