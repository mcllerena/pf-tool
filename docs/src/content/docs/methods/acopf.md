---
title: AC optimal power flow
description: AC power-balance optimization for voltage-profile or real-loss minimization, with Ipopt duals.
---

Source: `pf_tool/acopf.py` (`ACOptimalPowerFlow`).

`ACOptimalPowerFlow` is separate from [`OptimizationPowerFlow`](/pf-tool/methods/optimization/):
the latter minimizes power-flow residuals, while this model optimizes a feasible
AC operating point.

## Formulation

The model enforces the AC active- and reactive-power balance at every bus:

$$
P_i(V,\theta) = P_i^{sp}, \qquad Q_i(V,\theta) = Q_i^{sp}.
$$

Its default objective minimizes the weighted squared deviation from the voltage
magnitudes stored in the case:

$$
\min \sum_i w_i\left(|V_i| - |V_i^{ref}|\right)^2.
$$

Voltage and thermal limits use nonnegative slack variables. Their weighted
penalties are added to the selected objective:

$$
\lambda_v\sum_i(s_i^-+s_i^+) + \lambda_t\sum_l s_l.
$$

The `voltage_reference` mapping can override targets by bus number, and
`voltage_weights` can set nonnegative bus weights. An alternative
`objective="loss_minimization"` minimizes the sum of calculated real bus
injections, which equals network real-power losses for this model. The default
voltage and thermal violation penalties are each `1000.0` per pu of slack;
`voltage_violation_penalty` and `branch_violation_penalty` can be adjusted.

The following quantities are optimized or constrained:

| Quantity                                                                            | Model treatment                                                                                                         |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Bus voltage magnitudes and angles                                                   | Variables; each island has one fixed reference angle. Bus voltage limits are softened with lower/upper slack variables. |
| Non-slack active generation                                                         | Fixed at the operating schedule.                                                                                        |
| Slack active generation                                                             | Variable to balance each island; parsed limits are enforced when present.                                               |
| Generator reactive output                                                           | Variable at voltage-controlling generators, with parsed Q limits when available.                                        |
| SVC reactive output                                                                 | Variable within its voltage-dependent Q limits.                                                                         |
| Transformer taps, phase shifters, switched shunts, CSC settings, and LCC set-points | Held at the operating point established by the Newton warm start (or case data when `warm_start=False`).                |
| Branch MVA ratings                                                                  | Soft apparent-power limits with penalized slack on non-contracted rated branches.                                       |

The bundled BIPS cases already contain voltage and branch-rating violations in
their Newton operating points. Soft limits let Ipopt return a balanced
operating point while minimizing those violations. `get_violations()` reports
the remaining physical excess. Branches contracted by network reduction do not
have NLP flow constraints; their post-solve loading is still available through
`get_violations()` and the `Lines` and `Transformers` sheets from
`ExportSolution`.

Ipopt solves a nonconvex NLP and may return a local optimum. No generation-cost
data is required or inferred.

## Usage

```python
from pf_tool import ACOptimalPowerFlow

solver = ACOptimalPowerFlow(
    "tests/data/CASO01-FLOW.dat",
    objective="voltage_deviation",
    warm_start=True,
)
result = solver.solve()
print(result.converged, result.objective_value, result.max_mismatch)
```

Ipopt must be available on `PATH` or passed through `ipopt_executable`.
`warm_start=True` (the default) runs Newton-Raphson first and uses its solved
voltages and control state to initialize the NLP.
Set `warm_start_linear_solver="clu"` to use VeraGridEngine's optional CLU
factorizer for that Newton solve; the default is `"scipy"`. This option affects
only the warm start: Ipopt still performs the ACOPF optimization using its own
configured linear solver.

### CLU warm-start comparison

On `CASO01-FLOW.dat`, the default ACOPF and `warm_start_linear_solver="clu"`
both converged to the same objective (`5081.8973`) with a maximum power-balance
mismatch of about `2.98e-8` pu. Both used 19 Newton warm-start iterations and 38
Ipopt iterations.

| Warm-start solver | Lower-voltage violations (count / total excess pu) | Upper-voltage violations (count / total excess pu) | Branch-rating violations (count / total excess MVA) | Total |
| ----------------- | -------------------------------------------------: | -------------------------------------------------: | --------------------------------------------------: | ----: |
| SciPy (default)   |                                 11 / 0.30737787 pu |                                 21 / 0.30829699 pu |                                  32 / 1269.7751 MVA |    64 |
| VeraGrid CLU      |                                 11 / 0.30737787 pu |                                 21 / 0.30829699 pu |                                  32 / 1269.7751 MVA |    64 |

For this case, CLU did not reduce either the number or the measured severity
of violations; the reported amounts differed only by numerical roundoff. CLU
changes the Newton initial guess, not Ipopt's optimization algorithm or
objective, so it is not itself a way to reduce ACOPF operating-limit
violations.

## Feasibility strategies

Two optional strategies can run additional Ipopt solves before returning the
final result:

- `feasibility_restoration=True` first minimizes the weighted voltage and
  branch-limit slack penalties, keeping AC power balance and the other model
  constraints. The selected voltage-deviation or loss objective is then solved
  from that restored point.
- `progressive_tightening=True` solves the selected objective from relaxed to
  original limits. The built-in stages relax voltage bounds by `0.05`, `0.03`,
  then `0.0` p.u., and branch ratings by `5%`, `3%`, then `0%`.

When both are enabled, restoration runs first at the widest limits, followed by
the three objective stages. Each stage warm-starts from the previous NLP
solution. The result's `iterations` is the total across stages; the result also
reports `feasibility_restoration_iterations` and
`progressive_tightening_stages`. These strategies can help find a good feasible
point, but do not guarantee that the original limits can be met. Since
voltage and branch limits remain soft, inspect `get_violations()` after solving.

### CASO01 violation counts

The following comparison uses `CASO01-FLOW.dat`, the default
`voltage_deviation` objective, default penalty weights, and `warm_start=True`.
All four solves converged. Counts are physical violation records returned by
`get_violations()`; each violating bus or branch counts once, regardless of
the size of its violation.

| ACOPF approach          | Lower-voltage | Upper-voltage | Branch-rating | Total | Ipopt iterations |
| ----------------------- | ------------: | ------------: | ------------: | ----: | ---------------: |
| Default                 |            11 |            21 |            32 |    64 |               38 |
| Feasibility restoration |            11 |            21 |            32 |    64 |               91 |
| Progressive tightening  |            11 |            21 |            32 |    64 |              296 |
| Both options            |            11 |            21 |            32 |    64 |              361 |

For this case, the optional strategies did not reduce the _count_ of remaining
violations. Counts are case- and configuration-dependent and do not compare
violation severity; use the `amount` field in `get_violations()` to analyze
severity for a particular solve.

See [`examples/run_acopf_restoration.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/run_acopf_restoration.py)
for a runnable example enabling both options.

## Duals

The result includes dual multipliers for active/reactive power balance,
voltage and non-contracted branch limits, SVC capability limits, and imported
Ipopt variable-bound multipliers:

```python
result = solver.solve()
duals = solver.get_result_duals(result)
print(duals["active_power_balance"])
print(duals["voltage_upper"])
```

The dictionaries use bus numbers as labels; SVC entries include the device bus
and SVC index. Variable-bound multipliers are grouped under
`duals["variable_bounds"]["lower"]` and `["upper"]`. Missing entries mean Ipopt
did not return a multiplier for that item. These are NLP KKT multipliers for the selected objective, not market prices or
dollar-per-MW locational marginal prices. Soft voltage/branch constraint
duals should be interpreted together with their slack-variable values and
penalty weights.

`ACOptimalPowerFlowResult` extends `PowerFlowResult` with `objective_value`,
`termination_condition`, `warm_start_iterations`, restoration/tightening
iteration details, and `duals`.

## Violation report

After solving, `solver.get_violations()` returns `ACOPFViolation` records with
`category`, `element`, `value`, `limit`, `amount`, and `unit`. The report checks
power-balance residuals, bus voltage limits, generator and SVC limits, and
branch ratings. Voltage and branch-limit entries report physical excess even
when the NLP is feasible because the corresponding slack variables absorb the
violation. Contracted-branch ratings are diagnostic-only.
