from collections import Counter
from pathlib import Path

from pf_tool import ACOptimalPowerFlow, ExportSolution

data_file = Path(__file__).resolve().parents[1] / "tests" / "data" / "CASO01-FLOW.dat"
output_file = Path(__file__).resolve().parent / "results" / "CASO01-FLOW-acopf-restored.xlsx"

solver = ACOptimalPowerFlow(
    data_file,
    feasibility_restoration=True,
    progressive_tightening=True,
    print_iterations=True
)
result = solver.solve()

print(f"Converged: {result.converged}, iterations: {result.iterations}")
print(f"Max power-balance residual: {result.max_mismatch:.2e} pu")
print(f"Restoration iterations: {result.feasibility_restoration_iterations}")
print(f"Progressive-tightening stages: {result.progressive_tightening_stages}")
print(f"Remaining violations: {dict(Counter(v.category for v in solver.get_violations(result)))}")

ExportSolution(solver, output_path=output_file)
print(f"Results saved to {output_file}")
