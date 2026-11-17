from pathlib import Path

from pf_tool import DCOptimalPowerFlow

case_path = Path(__file__).resolve().parents[1] / "tests" / "data" / "CASO01-FLOW.dat"
solver = DCOptimalPowerFlow(
    case_path,
    max_load_shedding_fraction=0.4,
)
result = solver.solve()

if not result.converged:
    raise RuntimeError(result.failure_reason or "DC-OPF did not find a feasible solution")

print("Curtailed generators:")
print(solver.get_curtailed_generators().to_string(index=False))
print("\nShed loads:")
print(solver.get_shed_loads().to_string(index=False))
