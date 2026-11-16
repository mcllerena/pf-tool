import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exporter import ExportSolution
from optimization import OptimizationPowerFlow

data_file = Path(__file__).resolve().parents[1] / "data" / "CASO01-FLOW.dat"
output_file = Path(__file__).resolve().parent / "results" / "CASO01-FLOW-opt.xlsx"
warm_start = True  # False starts the NLP from the voltages stored in the case
print_iterations = True  # print the Ipopt iteration log of each NLP solve

solver = OptimizationPowerFlow(data_file, warm_start=warm_start, print_iterations=print_iterations)
result = solver.solve()

print(f"Converged: {result.converged}, iterations: {result.iterations}")
print(f"Max residual: {result.max_mismatch:.2e} pu")

ExportSolution(solver, output_path=output_file)
print(f"Results saved to {output_file}")
