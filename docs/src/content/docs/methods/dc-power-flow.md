---
title: DC power flow
description: Linear active-power flow using the B-prime bus-susceptance matrix.
---

`DCPowerFlow` solves the linear DC equations with one sparse linear solve. It
is useful for fast active-power estimates and screening; it is not an AC
power-flow substitute.

## Approximation

The method follows the standard DC assumptions:

- series resistance is neglected;
- voltage magnitudes are fixed at 1 pu;
- angle differences are small and expressed in radians;
- shunt and reactive-power effects are omitted from the active-power equations.

For each in-service branch, the series coefficient is
$b_{ij}=1/(x_{ij}a_{ij})$, where $x$ is the branch reactance and $a$ is its
transformer tap ratio. The B-prime matrix is assembled as a weighted graph
Laplacian: $b_{ij}$ on the two diagonal entries and $-b_{ij}$ on the
off-diagonal entries. The reference row and column of each island are removed,
then the solver solves

$$
B'\theta=P.
$$

The supplied notes derive the reduced linear system but do not prescribe a
factorization. This implementation solves it with a sparse LU factorization
using SciPy's `splu` (SuperLU).

Here, $P$ is scheduled active generation minus load, including the fixed
active-power injections for parsed LCC links. Fixed phase shifts are included
as branch-flow offsets; controllable taps, phase-shifter setpoints, reactive
controls, and losses are not iterated.

After solving the angles, the oriented branch flow is

$$
P_{ij}=b_{ij}(\theta_i-\theta_j-\phi_{ij}),
$$

where $\phi_{ij}$ is the branch phase shift. Flows are returned in MW, positive
from the branch's `from_bus` to its `to_bus`.

## Usage

```python
from pf_tool import DCPowerFlow

solver = DCPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    print_iterations=True,
)
result = solver.solve()

print(f"Converged: {result.converged}")
print(f"DC angles (rad): {result.angles_radians[:5]}")
for flow in result.branch_flows[:5]:
    print(flow.from_bus, flow.to_bus, flow.circuit, flow.active_power_mw)
```

The solver uses one DBAR slack bus per connected island by default. If an
island has no slack bus, it uses the PV bus with the largest scheduled
generation, or the largest-generation bus if there is no PV bus. Set
`reference_bus` to override the reference in that bus's island:

```python
solver = DCPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    reference_bus=101,
    tolerance=1e-4,
)
result = solver.solve()
```

`tolerance` is the maximum active-power mismatch at non-reference buses in pu;
by default it uses `DCTE.TEPA / DCTE.BASE`. `DCPowerFlowResult` also exposes
`voltage` (unit-magnitude phasors), unwrapped `angles_radians`,
`reference_buses`, and `branch_flows`. The reference-bus injections are
calculated to balance each island. Because the method does not compute
reactive power or nonlinear AC terminal flows, use an AC solver when you need
the full-component workbook produced by `ExportSolution`.

The class converges on all eleven bundled `CASO*-FLOW.dat` cases. The
[`run_dc_powerflow.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/run_dc_powerflow.py)
example prints a short summary and the five largest branch flows.
