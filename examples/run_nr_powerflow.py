import numpy as np

from pf_tool import ExportSolution, NewtonRaphsonPowerFlow
from pf_tool.models.bus import BusType
from pf_tool.models.ybus import build_ybus, calculate_power


def report_non_convergence(solver: NewtonRaphsonPowerFlow) -> None:
    result = solver.result
    if result is None or result.converged:
        return

    case = solver.case
    reduction = solver.reduction
    if reduction is None:
        raise RuntimeError("The solver did not retain its low-impedance network reduction.")

    reduced_case = reduction.case
    reduced_voltage = np.asarray(
        [result.voltage[index] for index in reduction.representatives],
        dtype=np.complex128,
    )
    calculated = calculate_power(build_ybus(reduced_case), reduced_voltage)
    specified = reduced_case.specified_power
    active_residuals = []
    voltage_violations = []

    for index, bus in enumerate(reduced_case.buses):
        if bus.bus_type != BusType.SLACK:
            mismatch_mw = (calculated[index].real - specified[index].real) * case.base_mva
            active_residuals.append((abs(mismatch_mw), mismatch_mw, bus))

    for bus, solution in zip(case.buses, result.buses, strict=True):
        voltage = solution.voltage_pu
        violation = max(bus.voltage_min_pu - voltage, voltage - bus.voltage_max_pu, 0.0)
        if violation > 1e-6:
            voltage_violations.append((violation, voltage, bus))

    print("\nNon-convergence diagnostics:")
    print(f"  Stop reason: {result.failure_reason or 'residual exceeds tolerance'}")
    print(
        f"  Final maximum equation mismatch: {result.max_mismatch:.6g} pu "
        f"(tolerance: {solver.tolerance:.6g} pu)"
    )
    print("  Largest reduced-network active-power balance residuals:")
    for _, mismatch, bus in sorted(active_residuals, key=lambda item: item[0], reverse=True)[:5]:
        print(f"    Bus {bus.number} ({bus.name}): {mismatch:+.3f} MW")

    print(f"  Voltage-limit violations: {len(voltage_violations)}")
    for _, voltage, bus in sorted(voltage_violations, key=lambda item: item[0], reverse=True)[:5]:
        print(
            f"    Bus {bus.number} ({bus.name}): {voltage:.4f} pu "
            f"(limits {bus.voltage_min_pu:.4f}-{bus.voltage_max_pu:.4f} pu)"
        )
    print(
        "  These residuals show where the solve stalls; they do not prove the case is infeasible."
    )


filename = "20240820_C_01-30"
solver = NewtonRaphsonPowerFlow(
    filename=f"tests/data/op-dat/{filename}.dat",
    control_method="sequential",
    print_iterations=True,
)
result = solver.solve()
report_non_convergence(solver)

ExportSolution(solver, output_path=f"results/{filename}-nr.xlsx")
