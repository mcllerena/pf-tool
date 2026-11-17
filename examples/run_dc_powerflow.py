from pathlib import Path

from pf_tool import DCPowerFlow

case = Path(__file__).parent.parent / "tests" / "data" / "CASO01-FLOW.dat"
solver = DCPowerFlow(filename=case, print_iterations=True)
result = solver.solve()

if not result.converged:
    raise RuntimeError(result.failure_reason or "DC power flow did not converge")

print("\nLargest DC branch flows:")
for flow in sorted(result.branch_flows, key=lambda item: abs(item.active_power_mw), reverse=True)[
    :5
]:
    print(
        f"{flow.from_bus:>6} -> {flow.to_bus:<6} circuit {flow.circuit}: "
        f"{flow.active_power_mw:>10.2f} MW"
    )
