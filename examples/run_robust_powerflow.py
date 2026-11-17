from pathlib import Path

from pf_tool import RobustACPowerFlow

case_path = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "data"
    / "op-dat"
    / "20240820_C_00-00.dat"
)

solver = RobustACPowerFlow(
    case_path,
    max_load_shedding_fraction=0.2,
    print_iterations=True,
)
result = solver.solve()

print(f"Converged: {result.converged}")
print(f"Full-load case converged: {result.full_load_converged}")
print(f"Retained DBAR load: {result.load_factor:.2%}")
print(f"Shed active load: {result.load_shed_active_mw:.3f} MW")
print(f"Shed reactive load: {result.load_shed_reactive_mvar:.3f} Mvar")
print(f"Fast-decoupled mismatch: {result.max_mismatch:.6g} pu")
print(f"Failure reason: {result.failure_reason or 'none'}")
if result.solved_case_path is not None:
    print(f"Solved case saved to: {result.solved_case_path}")
