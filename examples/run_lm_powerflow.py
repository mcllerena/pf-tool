from pf_tool import ExportSolution, LevenbergMarquardtPowerFlow

solver = LevenbergMarquardtPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    initial_damping=1e-3,
    print_iterations=True,
)
result = solver.solve()
print(f"Converged: {result.converged}; max mismatch: {result.max_mismatch:.3e} pu")

ExportSolution(solver, output_path="results/CASO01-FLOW-lm.xlsx")
