from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow(
    filename="tests/data/op-dat/20240820_C_00-30.dat",
    control_method="sequential",
    print_iterations=True,
)
result = solver.solve()

ExportSolution(solver, output_path="results/20240820_C_00-30-nr.xlsx")
