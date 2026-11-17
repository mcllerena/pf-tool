---
title: Levenberg-Marquardt
description: Sparse Levenberg-Marquardt AC power flow with sequential device controls.
---

Source: `pf_tool/levenberg_marquardt.py` (`LevenbergMarquardtPowerFlow`).

Levenberg-Marquardt (LM) solves the AC power-flow equations as a nonlinear
least-squares problem. It is an alternative to Newton-Raphson that adds
diagonal damping to the normal equations, which can make steps more cautious
when the starting point is far from a solution.

## Formulation

At each iteration, the solver uses the same state $x$, residual $F(x)$, and
sparse Jacobian $J(x)$ as the Newton-Raphson network model:

$$
\min_{\Delta x}\ \frac{1}{2}\lVert F(x) + J(x)\Delta x\rVert_2^2
+ \frac{\lambda}{2}\Delta x^\mathsf{T}D\Delta x
$$

The resulting sparse system is

$$
\left(J^\mathsf{T}J + \lambda D\right)\Delta x = -J^\mathsf{T}F(x),
\qquad D_{ii}=\max\left((J^\mathsf{T}J)_{ii},\,10^{-12}\max_j(J^\mathsf{T}J)_{jj}\right).
$$

The damping $\lambda$ is initialized by `initial_damping`. After each
line-searched trial step, the solver compares predicted and actual residual
reduction. Successful steps decrease damping; unsuccessful steps increase it
before the next iteration. All matrices and solves are sparse and use SciPy.

LM inherits the network model, limits, and outer sequential-control updates
from `NewtonRaphsonPowerFlow`. It starts from voltages stored in the case and
supports SVCs, generator reactive limits, switched shunts, LTCs, and LCC
updates. Controls are always handled sequentially; there is no simultaneous
control option. Since the method forms $J^\mathsf{T}J$, its factorization can
have more fill than the direct Newton system, so it may take more time and
memory on large cases.

## Usage

```python
from pf_tool import ExportSolution, LevenbergMarquardtPowerFlow

solver = LevenbergMarquardtPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    initial_damping=1e-3,
    print_iterations=True,
)
result = solver.solve()
print(result.converged, result.iterations, result.max_mismatch)
ExportSolution(solver, output_path="results/CASO01-FLOW-lm.xlsx")
```

`initial_damping` must be finite and greater than zero. The default is
`1e-3`. `tolerance`, `max_iterations`, `max_control_passes`, and
`print_iterations` have the same behavior as in Newton-Raphson.

See [Results and benchmarks](/pf-tool/methods/results/) for measured
convergence and timing on the 11 BIPS cases; CASO11 did not converge in the
documented run. See
[`examples/run_lm_powerflow.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/run_lm_powerflow.py)
for a runnable example.
