---
title: Getting started
description: Install the dependencies and run a power flow.
---

## Requirements

- Python 3.12 or newer. Installing the package pulls in `numpy`, `scipy`, `pyomo`, and `openpyxl`.
- The [Ipopt](https://coin-or.github.io/Ipopt/) executable, either on `PATH` or passed as `OptimizationPowerFlow(..., ipopt_executable="path/to/ipopt")`. This is required only for the optimization power flow. The benchmark used Ipopt 3.12.13.

```bash
python -m pip install pf-tool
```

To work from a clone of the repository instead, run `uv sync` in its root.

## Run a case

Each solver class takes the path to a `.dat` file. Its `.solve()` method returns a `PowerFlowResult`. The repository's `tests/data/` folder has eleven sample cases.

```python
from pf_tool import NewtonRaphsonPowerFlow

result = NewtonRaphsonPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    print_iterations=True,  # print the iteration table shown below
).solve()
print(result.converged, result.iterations, result.max_mismatch)
```

With `print_iterations=True`, the Newton-Raphson solver prints one row per iteration, then a line between control passes naming the controls that changed, and a final summary:

```text
Pass  Iter  Mismatch (pu)   Max dP (MW)   P bus  Max dQ (Mvar)   Q bus   Ctrl (pu)  Max dV (pu)  Max dAng (deg)    Step
-----------------------------------------------------------------------------------------------------------------------
   1     0      1.359e+01         0.214    4471       1358.953    5480   2.173e-02            -               -       -
   1     1      9.646e-01         3.980    7552        -96.464    7039   3.762e-05      0.09246          2.3670  1.0000
   ...
  -- pass 1 converged; updating generator Q limits, LCC set-points, LTC regulation and re-solving
   ...
Converged in 19 iterations over 6 control passes; max mismatch 2.582e-07 pu (2.582e-05 MW/Mvar)
```

| Column                | Meaning                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------- |
| `Pass` / `Iter`       | Outer control pass, and Newton iteration within it                                          |
| `Mismatch (pu)`       | Largest P, Q, SVC, or tap-control residual; the pass converges when it is below `tolerance` |
| `Max dP` / `Max dQ`   | Largest active / reactive power mismatch and the bus where it occurs                        |
| `Ctrl (pu)`           | Largest SVC or tap-control equation residual                                                |
| `Max dV` / `Max dAng` | Largest voltage-magnitude / angle change made by the step that produced the row             |
| `Step`                | Line-search step length; 1 is a full Newton step, smaller values mean the step was damped   |

```python
from pf_tool import OptimizationPowerFlow

result = OptimizationPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    warm_start=True,        # start from a Newton-Raphson solution
    print_iterations=False, # True prints the Ipopt log for each NLP solve
).solve()
```

The same calls are available as `examples/run_nr_powerflow.py` and `examples/run_opt_powerflow.py`.

## Export the results

Pass the solved solver to `ExportSolution` to write every component result to an Excel workbook, with one sheet per component. See [Exporting results](/pf-tool/export/).

```python
from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat")
solver.solve()
ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

## Solver options

| Class                    | Argument             | Default                   | Meaning                                                   |
| ------------------------ | -------------------- | ------------------------- | --------------------------------------------------------- |
| both                     | `filename`           | required                  | Case to solve                                             |
| both                     | `tolerance`          | `TEPA / BASE` from `DCTE` | Maximum absolute P, Q, and control mismatch, in pu        |
| `NewtonRaphsonPowerFlow` | `max_iterations`     | `ACIT` from `DCTE`        | Newton iterations per inner solve                         |
| `NewtonRaphsonPowerFlow` | `max_control_passes` | 50                        | Outer passes for discrete controls                        |
| `NewtonRaphsonPowerFlow` | `print_iterations`   | `False`                   | Print the per-iteration table shown above                 |
| `OptimizationPowerFlow`  | `max_iterations`     | `max(200, 2·ACIT)`        | Ipopt iterations per NLP solve                            |
| `OptimizationPowerFlow`  | `max_cpu_time`       | 900 s                     | Ipopt CPU-time limit per NLP solve                        |
| `OptimizationPowerFlow`  | `max_control_passes` | 20                        | NLP re-solves for SVC regions and discrete controls       |
| `OptimizationPowerFlow`  | `warm_start`         | `True`                    | Use a Newton solve as the initial point and control state |
| `OptimizationPowerFlow`  | `print_iterations`   | `False`                   | Print the Ipopt iteration table                           |
| `OptimizationPowerFlow`  | `ipopt_executable`   | `None` (search `PATH`)    | Path to the Ipopt binary                                  |

## Result object

`PowerFlowResult` contains these fields:

| Field            | Meaning                                                                                                   |
| ---------------- | --------------------------------------------------------------------------------------------------------- |
| `converged`      | `True` only when every convergence test passes                                                            |
| `iterations`     | Newton iterations, or Ipopt iterations summed over NLP solves                                             |
| `max_mismatch`   | Largest absolute P, Q, or control residual, in pu                                                         |
| `voltage`        | Complex bus voltages for the original, unreduced bus list                                                 |
| `buses`          | `BusSolution` entries: number, name, $\lvert V\rvert$ (pu), angle (degrees), and P/Q injections (MW/Mvar) |
| `failure_reason` | Text explanation when the run does not converge                                                           |

## Building these docs

```bash
cd docs
npm install
npm run build     # static site in docs/dist
npm run dev       # local preview at http://localhost:4321
```
