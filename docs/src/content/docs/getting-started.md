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

result = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat").solve()
print(result.converged, result.iterations, result.max_mismatch)
```

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
| both                     | `data_file`          | required                  | Case to solve                                             |
| both                     | `tolerance`          | `TEPA / BASE` from `DCTE` | Maximum absolute P, Q, and control mismatch, in pu        |
| `NewtonRaphsonPowerFlow` | `max_iterations`     | `ACIT` from `DCTE`        | Newton iterations per inner solve                         |
| `NewtonRaphsonPowerFlow` | `max_control_passes` | 50                        | Outer passes for discrete controls                        |
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
