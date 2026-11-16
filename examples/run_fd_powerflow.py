from pf_tool import ExportSolution, FastDecoupledPowerFlow

solver = FastDecoupledPowerFlow(
    filename="tests/data/CASO05-FLOW.dat",
    enhancement=True,  # constant B'/B'' matrices; False re-evaluates the Jacobian blocks
    warm_start=True,  # start from a Newton-Raphson solution; False starts from the stored voltages
    print_iterations=True,
)
result = solver.solve()

print(
    f"factorizations: {solver.factorizations}, "
    f"coupling corrections: {solver.coupling_corrections}, "
    f"warm-start Newton iterations: {solver.warm_start_iterations}"
)

ExportSolution(solver, output_path="results/CASO05-FLOW-fd.xlsx")
