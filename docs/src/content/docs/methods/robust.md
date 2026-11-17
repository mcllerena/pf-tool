---
title: Robust AC power flow
description: Load-continuation restoration followed by a fast-decoupled AC power-flow solve.
---

`RobustACPowerFlow` searches for a solvable operating point by changing only positive
active and reactive loads from the `DBAR` block. It uses sequential Newton-Raphson
with voltage-profile continuation, then verifies each feasible stage with
[`FastDecoupledPowerFlow`](/pf-tool/methods/fast-decoupled/).

```python
from pf_tool import RobustACPowerFlow

solver = RobustACPowerFlow(
    "tests/data/op-dat/20240820_C_00-00.dat",
    max_load_shedding_fraction=0.20,
    initial_load_reduction=0.10,
    print_iterations=True,
)
result = solver.solve()

print(result.converged)
print(result.load_factor)
print(result.load_shed_active_mw)
print(result.solved_case_path)
```

The original file is never edited. After a successful Newton and fast-decoupled
solve, the solver writes a sibling `-solved.dat` file containing the retained
`DBAR` loads and final bus voltage/angle starting points. It does not write that
file when either solve fails.

| Argument                     |             Default | Meaning                                                                                                |
| ---------------------------- | ------------------: | ------------------------------------------------------------------------------------------------------ |
| `filename`                   |            required | Input ANAREDE `.dat` case                                                                              |
| `solved_filename`            | `<stem>-solved.dat` | Output path for a converged, adjusted case                                                             |
| `max_load_shedding_fraction` |              `0.20` | Maximum fraction of positive `DBAR` loads that may be shed                                             |
| `initial_load_reduction`     |              `0.10` | First reduced-load trial after a failed full-load attempt; it starts from the original voltage profile |
| `load_shedding_step`         |              `0.02` | Initial continuation step as a fraction of original load                                               |
| `minimum_load_step`          |            `0.0025` | Stop refining when the load-factor step becomes smaller                                                |
| `max_attempts`               |                `40` | Maximum Newton restoration trials                                                                      |
| `enhancement`                |             `False` | Select the constant-matrix fast-decoupled variant when `True`                                          |
| `tolerance`                  |       `TEPA / BASE` | Mismatch tolerance passed to both solvers                                                              |
| `max_iterations`             |              `ACIT` | Iteration limit passed to both solvers                                                                 |
| `max_control_passes`         |                `50` | Sequential control-pass limit                                                                          |
| `print_iterations`           |             `False` | Print each Newton and fast-decoupled trial                                                             |

The original case is tried first at a load factor of 1.0. If it fails, that
trial's voltage state is discarded and restoration restarts from the original
voltage profile at `1.0 - initial_load_reduction` (clamped to the configured
maximum shedding). Further downward trials use `load_shedding_step` and carry
forward their voltage profiles. After finding a converged Newton plus
fast-decoupled solve, the load is increased toward the original level. Failed
upward trials shrink the step, reducing unnecessary shedding. Only positive
`DBAR` P/Q loads are scaled; negative loads, generation schedules, and
converter injections are not shed by this procedure.

`result.converged` is true only when the final fast-decoupled solve meets its
mismatch tolerance. `result.full_load_converged` distinguishes a solution at
the original 100% load level from one that required shedding. The result also
reports `load_factor`, active/reactive MW/Mvar shed, restoration attempts,
restoration iterations, fast-decoupled iterations, and the output path.
Non-convergence is reported honestly; the solver does not label a case solved
when the restored equations still exceed tolerance.
