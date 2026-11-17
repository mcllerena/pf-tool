from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pf_tool import DCOptimalPowerFlow, DCPowerFlow
from pf_tool.ptdf import build_ptdf
from pf_tool.utils.dat_parser import parse_dat_file
from pf_tool.utils.utils import build_power_flow_case


def _two_bus_case(path: Path) -> Path:
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
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh Cn :=
    1 2 1 "A" "L" "L" "L" 0 10 0 1.1 -0.5729577951 0
;
""",
        encoding="utf-8",
    )
    return path


def _redispatch_case(
    path: Path,
    second_line_limit: float = 30.0,
    second_generator_max: float = 100.0,
) -> Path:
    path.write_text(
        f"""
param: DCTE: Mn Val :=
    "BASE" 100
    "TEPA" 0.01
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh :=
    1 "A" "L" "GEN-1" 2 1 0 80 0 0 0 0 0 0
    2 "A" "L" "GEN-2" 1 1 0 20 0 0 0 0 0 0
    3 "A" "L" "LOAD" 0 1 0 0 0 0 0 100 0 0
;
param: DGER: No O Pgn Pgm :=
    1 "A" 0 100
    2 "A" 0 {second_generator_max}
;
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh Cn :=
    1 3 1 "A" "L" "L" "L" 0 10 0 1 0 70
    2 3 1 "A" "L" "L" "L" 0 10 0 1 0 {second_line_limit}
;
""",
        encoding="utf-8",
    )
    return path


def _unbalanced_island_case(path: Path, isolated_load_mw: float = 1.5) -> Path:
    path.write_text(
        f"""
param: DCTE: Mn Val :=
    "BASE" 100
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh :=
    1 "A" "L" "GEN" 2 1 0 10 0 0 0 0 0 0
    2 "A" "L" "LOAD" 0 1 0 0 0 0 0 10 0 0
    3 "A" "L" "ISOLATED-LOAD" 2 1 0 0 0 0 0 {isolated_load_mw} 0 0
;
param: DGER: No O Pgn Pgm :=
    1 "A" 0 20
;
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh Cn :=
    1 2 1 "A" "L" "L" "L" 0 10 0 1 0 100
;
""",
        encoding="utf-8",
    )
    return path


def _uncontrollable_overload_case(path: Path) -> Path:
    path.write_text(
        """
param: DCTE: Mn Val :=
    "BASE" 100
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh :=
    1 "A" "L" "SLACK-GEN" 2 1 0 50 0 0 0 0 0 0
    2 "A" "L" "RADIAL-LOAD" 0 1 0 0 0 0 0 50 0 0
;
param: DGER: No O Pgn Pgm :=
    1 "A" 0 100
;
param: DLIN: I J Nc O E Si Sj R X Bshl Tap Psh Cn :=
    1 2 1 "A" "L" "L" "L" 0 10 0 1 0 40
;
""",
        encoding="utf-8",
    )
    return path


def test_ptdf_matches_dc_power_flow_and_handles_phase_shift(tmp_path: Path) -> None:
    case_path = _two_bus_case(tmp_path / "two_bus.dat")
    case = build_power_flow_case(parse_dat_file(case_path))
    ptdf = build_ptdf(case, [1, 2])
    dc_result = DCPowerFlow(case_path).solve()

    assert ptdf.injection_buses == (1, 2)
    assert ptdf.reference_buses == (1,)
    assert ptdf.matrix[:, 0] == pytest.approx(np.zeros(1))
    assert ptdf.matrix[:, 1] == pytest.approx(np.array([-1.0]))
    assert ptdf.base_flows_mw == pytest.approx(
        [flow.active_power_mw for flow in dc_result.branch_flows]
    )


def test_dcopf_uses_least_redispatch_to_enforce_ptdf_line_limits(tmp_path: Path) -> None:
    solver = DCOptimalPowerFlow(_redispatch_case(tmp_path / "redispatch.dat"))

    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.generation_mw is not None
    assert result.redispatch_mw is not None
    assert result.generation_mw[1] == pytest.approx(70.0, abs=1e-6)
    assert result.generation_mw[2] == pytest.approx(30.0, abs=1e-6)
    assert result.objective_value_mw == pytest.approx(20.0, abs=1e-6)
    assert result.max_balance_error_mw == pytest.approx(0.0, abs=1e-6)
    assert result.max_branch_violation_mw == pytest.approx(0.0, abs=1e-6)
    curtailed = solver.get_curtailed_generators()
    assert curtailed["bus_number"].tolist() == [1]
    assert curtailed.iloc[0]["bus_name"] == "GEN-1"
    assert tuple(
        curtailed.iloc[0][["generation_before_mw", "generation_after_mw", "curtailed_mw"]]
    ) == pytest.approx((80.0, 70.0, 10.0))
    assert [flow.active_power_mw for flow in result.branch_flows or []] == pytest.approx(
        [70.0, 30.0], abs=1e-6
    )


def test_dcopf_reports_infeasible_dispatch_without_a_fake_solution(tmp_path: Path) -> None:
    solver = DCOptimalPowerFlow(
        _redispatch_case(
            tmp_path / "infeasible.dat",
            second_line_limit=15.0,
            second_generator_max=20.0,
        ),
        max_load_shedding_fraction=0.0,
    )

    result = solver.solve()

    assert not result.converged
    assert result.generation_mw is None
    assert result.branch_flows is None
    assert "infeasible" in result.failure_reason.lower()
    with pytest.raises(RuntimeError, match="feasible DC-OPF result"):
        solver.get_shed_loads()


def test_dcopf_uses_minimal_load_shedding_to_restore_line_feasibility(
    tmp_path: Path,
) -> None:
    result = DCOptimalPowerFlow(
        _redispatch_case(
            tmp_path / "restored.dat",
            second_line_limit=15.0,
            second_generator_max=20.0,
        )
    ).solve()

    assert result.converged, result.failure_reason
    assert result.load_shedding_mw is not None
    assert sum(result.load_shedding_mw.values()) == pytest.approx(15.0, abs=1e-6)
    assert result.max_balance_error_mw == pytest.approx(0.0, abs=1e-6)
    assert result.max_branch_violation_mw == pytest.approx(0.0, abs=1e-6)


def test_dcopf_reports_island_balance_outside_dispatchable_range(tmp_path: Path) -> None:
    result = DCOptimalPowerFlow(_unbalanced_island_case(tmp_path / "island.dat")).solve()

    assert not result.converged
    assert result.failure_reason is not None
    assert "reference bus 3" in result.failure_reason
    assert "allowed reference-bus adjustment" in result.failure_reason


def test_dcopf_balances_small_fixed_island_mismatch_at_reference(tmp_path: Path) -> None:
    result = DCOptimalPowerFlow(
        _unbalanced_island_case(tmp_path / "small_island_mismatch.dat", 0.25)
    ).solve()

    assert result.converged, result.failure_reason
    assert result.balance_slack_mw is not None
    assert result.balance_slack_mw[3] == pytest.approx(0.25, abs=1e-6)
    assert result.max_balance_error_mw == pytest.approx(0.0, abs=1e-6)


def test_dcopf_rejects_negative_balance_slack_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="balance_slack_limit_mw"):
        DCOptimalPowerFlow(
            _two_bus_case(tmp_path / "invalid_slack.dat"),
            balance_slack_limit_mw=-0.1,
        )


def test_dcopf_uses_load_shedding_for_fixed_generator_insensitive_overload(
    tmp_path: Path,
) -> None:
    solver = DCOptimalPowerFlow(_uncontrollable_overload_case(tmp_path / "fixed_overload.dat"))
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.load_shedding_mw is not None
    assert result.load_shedding_mw[2] == pytest.approx(10.0, abs=1e-6)
    assert result.branch_flows is not None
    assert result.branch_flows[0].active_power_mw == pytest.approx(40.0, abs=1e-6)
    curtailed = solver.get_curtailed_generators()
    assert curtailed["bus_number"].tolist() == [1]
    assert tuple(
        curtailed.iloc[0][["generation_before_mw", "generation_after_mw", "curtailed_mw"]]
    ) == pytest.approx((50.0, 40.0, 10.0))
    shed = solver.get_shed_loads()
    assert shed["bus_number"].tolist() == [2]
    assert shed.iloc[0]["bus_name"] == "RADIAL-LOAD"
    assert tuple(shed.iloc[0][["load_before_mw", "load_after_mw", "shed_mw"]]) == (
        50.0,
        40.0,
        10.0,
    )


def test_dcopf_adjustment_reports_require_a_solve(tmp_path: Path) -> None:
    solver = DCOptimalPowerFlow(_redispatch_case(tmp_path / "unsolved.dat"))

    with pytest.raises(RuntimeError, match="Call solve"):
        solver.get_curtailed_generators()


def test_dcopf_reports_uncontrollable_overload_when_shedding_is_disabled(
    tmp_path: Path,
) -> None:
    result = DCOptimalPowerFlow(
        _uncontrollable_overload_case(tmp_path / "strict_overload.dat"),
        max_load_shedding_fraction=0.0,
    ).solve()

    assert not result.converged
    assert result.failure_reason is not None
    assert "zero sensitivity" in result.failure_reason
    assert "1-2" in result.failure_reason


def test_ptdf_rejects_unknown_injection_bus_and_zero_reactance(tmp_path: Path) -> None:
    case_path = _two_bus_case(tmp_path / "invalid.dat")
    case = build_power_flow_case(parse_dat_file(case_path))

    with pytest.raises(ValueError, match="Unknown injection bus"):
        build_ptdf(case, [999])

    case.branches[0].reactance_pu = 0.0
    with pytest.raises(ValueError, match="zero reactance"):
        build_ptdf(case, [2])
