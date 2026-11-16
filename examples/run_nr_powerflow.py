import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exporter import ExportSolution
from newton_raphson import NewtonRaphsonPowerFlow

data_file = Path(__file__).resolve().parents[1] / "tests" / "data" / "CASO01-FLOW.dat"
output_file = Path(__file__).resolve().parent / "results" / "CASO01-FLOW-nr.xlsx"

solver = NewtonRaphsonPowerFlow(data_file)
result = solver.solve()

print(f"Converged: {result.converged}, iterations: {result.iterations}")
print(f"Max mismatch: {result.max_mismatch:.2e} pu")

ExportSolution(solver, output_path=output_file)
print(f"Results saved to {output_file}")
