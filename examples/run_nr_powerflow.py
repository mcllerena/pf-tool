from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow(
    filename="tests/data/CASO01-FLOW.dat", control_method="simultaneous", print_iterations=True
)
result = solver.solve()

ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
