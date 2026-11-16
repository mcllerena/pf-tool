---
title: Getting started
description: Install the dependencies and run a power flow.
---

## Requirements

- Python 3.12 with `numpy`, `scipy`, `pyomo`, and `openpyxl` (see `requirements.txt`).
- The [Ipopt](https://coin-or.github.io/Ipopt/) executable on `PATH`. This is required only for the optimization power flow. The benchmark used Ipopt 3.12.13.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

## Run a case

Each solver class takes the path to a `.dat` file. Its `.solve()` method returns a `PowerFlowResult`.

```python
from newton_raphson import NewtonRaphsonPowerFlow

result = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat").solve()
print(result.converged, result.iterations, result.max_mismatch)
```

```python
from optimization import OptimizationPowerFlow

result = OptimizationPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    warm_start=True,        # start from a Newton-Raphson solution
    print_iterations=False, # True prints the Ipopt log for each NLP solve
).solve()
```

The same calls are available as `examples/run_nr_powerflow.py` and `examples/run_opt_powerflow.py`.

## Export the results

Pass the solved solver to `ExportSolution` to write every component result to an Excel workbook, with one sheet per component. See [Exporting results](/export/).

```python
from exporter import ExportSolution

solver = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat")
solver.solve()
ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

## Solver options

| Class                    | Argument             | Default                   | Meaning                                                   |
| ------------------------ | -------------------- | ------------------------- | --------------------------------------------------------- |
| both                     | `data_file`          | `tests/data/CASO01-FLOW.dat`    | Case to solve                                             |
| both                     | `tolerance`          | `TEPA / BASE` from `DCTE` | Maximum absolute P, Q, and control mismatch, in pu        |
| `NewtonRaphsonPowerFlow` | `max_iterations`     | `ACIT` from `DCTE`        | Newton iterations per inner solve                         |
| `NewtonRaphsonPowerFlow` | `max_control_passes` | 50                        | Outer passes for discrete controls                        |
| `OptimizationPowerFlow`  | `max_iterations`     | `max(200, 2·ACIT)`        | Ipopt iterations per NLP solve                            |
| `OptimizationPowerFlow`  | `max_cpu_time`       | 900 s                     | Ipopt CPU-time limit per NLP solve                        |
| `OptimizationPowerFlow`  | `max_control_passes` | 20                        | NLP re-solves for SVC regions and discrete controls       |
| `OptimizationPowerFlow`  | `warm_start`         | `True`                    | Use a Newton solve as the initial point and control state |
| `OptimizationPowerFlow`  | `print_iterations`   | `False`                   | Print the Ipopt iteration table                           |

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
