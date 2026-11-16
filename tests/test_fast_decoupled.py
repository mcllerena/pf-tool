"""Fast decoupled power flow on the IEEE 9-bus case: both variants, warm and cold start."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import pytest

from pf_tool import ExportSolution, FastDecoupledPowerFlow, NewtonRaphsonPowerFlow, fast_decoupled

CASE = Path(__file__).parent / "data" / "IEEE9-FLOW.dat"


@pytest.mark.parametrize("warm_start", [True, False])
@pytest.mark.parametrize("enhancement", [False, True])
def test_fast_decoupled_matches_newton(
    components_file: Path, enhancement: bool, warm_start: bool
) -> None:
    expected = NewtonRaphsonPowerFlow(filename=components_file, control_method="sequential").solve()
    solver = FastDecoupledPowerFlow(
        filename=components_file, enhancement=enhancement, warm_start=warm_start
    )
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert result.max_mismatch <= solver.tolerance
    assert solver.warm_start_converged is warm_start
    assert np.allclose(result.voltage, expected.voltage, atol=1e-4)
    if warm_start:
        # Newton's solution is already a fixed point of the fast decoupled solve.
        assert result.iterations == 0
    else:
        assert solver.factorizations > 0


@pytest.mark.parametrize("enhancement", [False, True])
def test_coupling_correction_converges_when_decoupled_steps_are_rejected(
    components_file: Path, enhancement: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No decoupled step can meet this decrease, so every iteration is undone
    # and replaced by a coupling-corrected step on the full Jacobian.
    monkeypatch.setattr(fast_decoupled, "_SUFFICIENT_DECREASE", 1e-12)
    expected = NewtonRaphsonPowerFlow(filename=components_file, control_method="sequential").solve()
    solver = FastDecoupledPowerFlow(
        filename=components_file, enhancement=enhancement, warm_start=False
    )
    result = solver.solve()

    assert result.converged, result.failure_reason
    assert solver.coupling_corrections > 0
    assert np.allclose(result.voltage, expected.voltage, atol=1e-4)


def test_fast_decoupled_reports_its_own_failure(ieee9_file: Path) -> None:
    solver = FastDecoupledPowerFlow(filename=ieee9_file, warm_start=False, max_iterations=0)
    result = solver.solve()
    assert not result.converged
    assert result.failure_reason == "Fast decoupled did not converge"


def test_constant_matrices_are_factored_once_per_structure(ieee9_file: Path) -> None:
    jacobian = FastDecoupledPowerFlow(filename=ieee9_file, warm_start=False)
    constant = FastDecoupledPowerFlow(filename=ieee9_file, warm_start=False, enhancement=True)
    first, second = jacobian.solve(), constant.solve()
    assert first.converged and second.converged
    # The Jacobian blocks are refactored every iteration (two halves each).
    assert jacobian.factorizations >= 2 * first.iterations
    assert constant.factorizations < 2 * second.iterations


@pytest.mark.parametrize("enhancement", [False, True])
def test_fast_decoupled_prints_and_exports(enhancement: bool, tmp_path: Path) -> None:
    solver = FastDecoupledPowerFlow(filename=CASE, enhancement=enhancement, print_iterations=True)
    output = io.StringIO()
    with redirect_stdout(output):
        result = solver.solve()

    assert result.converged, result.failure_reason
    text = output.getvalue()
    assert text.startswith("Warm start: Newton-Raphson converged")
    header = (
        "Fast decoupled power flow (constant B'/B'')"
        if enhancement
        else "Fast decoupled power flow:"
    )
    assert header in text

    export = ExportSolution(solver, output_path=tmp_path / "fd.xlsx")
    variant = "constant B'/B''" if enhancement else "Jacobian blocks"
    assert dict(export.sheets["Summary"][1])["Method"] == f"Fast decoupled ({variant}, warm start)"
