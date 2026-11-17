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

With `print_iterations=True`, the Newton-Raphson solver prints one row per iteration, then a line after each control pass, a count of the controls at their limits, and a final summary:

```text
Pass  Iter  Mismatch (pu)   Max dP (MW)   P bus  Max dQ (Mvar)   Q bus   Ctrl (pu)  Max dV (pu)  Max dAng (deg)    Step
-----------------------------------------------------------------------------------------------------------------------
   1     0      1.359e+01         0.214    4471       1358.953    5480   2.173e-02            -               -       -
   1     1      9.647e-01         3.980    7552        -96.467    7039   3.308e-02      0.09338          2.3768  1.0000
   ...
   1    10      5.995e-05         0.000    5924          0.006    5924   2.178e-09      0.00095          0.0119  1.0000
  -- pass 1 converged; rounding switched shunts to whole bank units (0 changed) and re-solving
   2     0      3.842e-01         0.000    5924        -38.417    9523   7.683e-03            -               -       -
   2     1      2.808e-06         0.000   41935          0.000     590   4.630e-08      0.00036          0.0090  1.0000
  -- pass 2 converged; controls are stable
  -- controls: 2 generators at a reactive limit, 12 LTC groups regulating, 2 at a tap limit, 0 VLIM buses at a voltage limit
-----------------------------------------------------------------------------------------------------------------------
Converged in 11 iterations over 2 control passes; max mismatch 2.808e-06 pu (2.808e-04 MW/Mvar)
```

With the default simultaneous method, pass 1 solves switched shunts as continuous susceptances, and pass 2 re-solves after rounding them to whole bank units. With `control_method="sequential"`, there is one pass per round of discrete control updates.

| Column                | Meaning                                                                                   |
| --------------------- | ----------------------------------------------------------------------------------------- |
| `Pass` / `Iter`       | Control pass, and Newton iteration within it                                              |
| `Mismatch (pu)`       | Largest residual of any equation; the pass converges when it is below `tolerance`         |
| `Max dP` / `Max dQ`   | Largest active / reactive power mismatch and the bus where it occurs                      |
| `Ctrl (pu)`           | Largest control equation residual: SVC, tap, phase shift, generator limit, or shunt       |
| `Max dV` / `Max dAng` | Largest voltage-magnitude / angle change made by the step that produced the row           |
| `Step`                | Line-search step length; 1 is a full Newton step, smaller values mean the step was damped |

## DC power flow

The DC solver computes bus angles and active branch flows with one sparse
linear solve. Its result includes the solved `angles_radians`,
`reference_buses`, and oriented `branch_flows`:

```python
from pf_tool import DCPowerFlow

result = DCPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    print_iterations=True,
).solve()
print(result.converged, result.max_mismatch)
```

The model assumes unit voltage magnitudes, zero series resistance, small angle
differences, and omits reactive power and shunt effects. See the
[DC power-flow method](/pf-tool/methods/dc-power-flow/) for the equations and
reference-bus behavior.

```python
from pf_tool import FastDecoupledPowerFlow

result = FastDecoupledPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    enhancement=True,  # constant B'/B'' matrices; False re-evaluates the Jacobian blocks
    warm_start=True,   # start from a Newton-Raphson solution
).solve()
```

```python
from pf_tool import OptimizationPowerFlow

result = OptimizationPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    warm_start=True,        # start from a Newton-Raphson solution
    print_iterations=False, # True prints the Ipopt log for each NLP solve
).solve()
```

The same calls are available as `examples/run_nr_powerflow.py`, `examples/run_fd_powerflow.py`, and `examples/run_opt_powerflow.py`. `examples/compare_control_methods.py` solves one case with both Newton-Raphson control methods and prints their iterations, time, and agreement with the stored ANAREDE voltages.

## Export the results

Pass the solved solver to `ExportSolution` to write every component result to an Excel workbook, with one sheet per component. See [Exporting results](/pf-tool/export/).

```python
from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat")
solver.solve()
ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

## Solver options

| Class                    | Argument             | Default                     | Meaning                                                                                                                 |
| ------------------------ | -------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| all                      | `filename`           | required                    | Case to solve                                                                                                           |
| all                      | `tolerance`          | `TEPA / BASE` from `DCTE`   | Maximum mismatch in pu; DC uses active-power mismatch at non-reference buses                                            |
| `DCPowerFlow`            | `reference_bus`      | `None`                      | Optional bus number to use as the reference in its connected island                                                     |
| `DCPowerFlow`            | `print_iterations`   | `False`                     | Print the direct-solve summary                                                                                          |
| `NewtonRaphsonPowerFlow` | `max_iterations`     | `ACIT` from `DCTE`          | Newton iterations per inner solve                                                                                       |
| `NewtonRaphsonPowerFlow` | `max_control_passes` | 50                          | Outer passes for discrete controls (sequential method)                                                                  |
| `NewtonRaphsonPowerFlow` | `control_method`     | `"simultaneous"`            | Solve every control in the Newton system, or `"sequential"` to update generator limits, shunts, and LCCs between solves |
| `NewtonRaphsonPowerFlow` | `print_iterations`   | `False`                     | Print the per-iteration table shown above                                                                               |
| `FastDecoupledPowerFlow` | `enhancement`        | `False`                     | `True` uses constant `B'`/`B''` matrices; `False` re-evaluates the decoupled Jacobian blocks each iteration             |
| `FastDecoupledPowerFlow` | `warm_start`         | `True`                      | Solve with sequential Newton-Raphson first, then re-solve with the fast decoupled method                                |
| `FastDecoupledPowerFlow` | other arguments      | as `NewtonRaphsonPowerFlow` | `max_iterations`, `max_control_passes`, `print_iterations`; controls always use the sequential method                   |
| `OptimizationPowerFlow`  | `max_iterations`     | `max(200, 2·ACIT)`          | Ipopt iterations per NLP solve                                                                                          |
| `OptimizationPowerFlow`  | `max_cpu_time`       | 900 s                       | Ipopt CPU-time limit per NLP solve                                                                                      |
| `OptimizationPowerFlow`  | `max_control_passes` | 20                          | NLP re-solves for SVC regions and discrete controls                                                                     |
| `OptimizationPowerFlow`  | `warm_start`         | `True`                      | Use a Newton solve as the initial point and control state                                                               |
| `OptimizationPowerFlow`  | `print_iterations`   | `False`                     | Print the Ipopt iteration table                                                                                         |
| `OptimizationPowerFlow`  | `ipopt_executable`   | `None` (search `PATH`)      | Path to the Ipopt binary                                                                                                |

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

`DCPowerFlowResult` extends this result with the unwrapped bus angles in
`angles_radians`, reference bus numbers in `reference_buses`, and one
`DCBranchFlow` record per branch in `branch_flows`.

## Building these docs

```bash
cd docs
npm install
npm run build     # static site in docs/dist
npm run dev       # local preview at http://localhost:4321
```
