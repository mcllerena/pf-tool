---
title: DC optimal power flow
description: PTDF-based DC-OPF with least-absolute-redispatch when generator costs are unavailable.
---

`DCOptimalPowerFlow` solves a linear DC-OPF using power-transfer distribution
factors (PTDFs). Its formulation follows the generator bounds, system balance,
and transmission limits in Section 3.9 of
[the referenced lecture notes](https://arxiv.org/abs/1811.00943).
Unlike AC power flow, this is a linear program, so it has no nonlinear
convergence problem. It can still be **infeasible** when generator bounds,
fixed injections, hard branch limits, and configured flexibility bounds have
no common solution.

## Formulation

The dispatch variables are active generation values at buses with valid
`DGER` active-power bounds. Loads, generation without dispatch bounds, and
LCC converter injections are fixed to the input case. Each island has a
bounded adjustment $s_k$ at its reference bus:

$$
\sum_{g \in \mathcal{G}_k} (P_g - P_g^0) + s_k
= -\sum_{i \in \mathcal{N}_k} P_i^0,
\qquad |s_k| \leq S_{\max}.
$$

With no production-cost data, this implementation does not invent an economic
cost curve. The primary PTDF LP minimizes total reference-bus adjustment and
then total absolute generator redispatch:

$$
\operatorname{lexmin} \left(\sum_k |s_k|,\ \sum_g |P_g - P_g^0|\right),
$$

subject to generator limits and the PTDF branch-flow limits:

$$
-F_\ell^{\max}
\leq F_\ell^0 + \sum_g H_{\ell g}(P_g-P_g^0)
\leq F_\ell^{\max}.
$$

Here, $P_g^0$ is the input dispatch, $F_\ell^0$ is the base DC flow including
fixed phase shifts, and $H_{\ell g}$ is the single-reference PTDF for a
1 MW injection at generator bus $g$ balanced by withdrawal at its island's
reference bus. The island balance constraint makes the resulting flows
independent of which bus is selected as reference.

The default adjustment cap is $S_{\max}=0.01\,\text{BASE}$ MW (1 MW on the
bundled 100 MVA cases). Pass `balance_slack_limit_mw=0` for strict balance.
The adjustment is returned separately in `result.balance_slack_mw`; it is not
reported as generator output.

If that LP is infeasible, the solver tries a sparse angle-formulation
restoration LP with the following lexicographic priorities:

1. Minimize reference-bus adjustment.
2. Minimize native-load shedding, bounded per bus by
   `max_load_shedding_fraction` (default `0.2`).
3. Minimize curtailment of positive generation without DGER bounds.
4. Minimize absolute redispatch of DGER-bounded generators.

The fallback enforces the same line limits and power balance. It returns the
resulting adjustments in `balance_slack_mw`, `load_shedding_mw`, and
`generation_curtailment_mw`. Load shedding is a per-bus fraction, not a
system-wide fraction; a low cap can block relief of a local overload even when
the total load shed required by the network is small. Raise the cap explicitly
only when that operating policy is acceptable. LCC converter injections are
not adjustable by this fallback.

## PTDF construction

The implementation forms the sparse DC bus susceptance matrix from each
in-service branch's reactance and transformer tap. It factors the reduced
matrix once per island and solves only for dispatchable-generator PTDF columns,
in blocks, instead of materializing a dense all-bus PTDF matrix. Fixed phase
shifts contribute to the base flows; their sensitivities are not treated as
decision variables. The branch orientation and tap/phase conventions match
[`DCPowerFlow`](/pf-tool/methods/dc-power-flow/).

Positive branch ratings below 9999 MVA are enforced as active-power limits in
MW. This is the DC approximation to a thermal MVA rating; it does not calculate
reactive flow or apparent power. Zero and sentinel ratings are treated as
unlimited, consistently with the AC-OPF data handling.

## Limitations

- This is a feasibility/redispatch objective, not least-cost economic dispatch.
  Add generator cost curves when they become available before interpreting
  dispatch as economically optimal.
- The DC model neglects losses, reactive power, voltage magnitudes, and AC
  voltage or stability constraints.
- LCC terminal injections are fixed at their parsed operating values; the
  converter controls are not optimized.
- Generators with valid `DGER` active-power bounds are redispatched. Positive
  scheduled generation at slack/PV buses without `DGER` bounds may only be
  curtailed to zero; other injections remain fixed.
- A reference-bus adjustment is a bounded modeling slack, not a physical
  generator. Check `balance_slack_mw`; use a zero limit when exact balance is
  required.
- Hard `Cn` branch limits can make both LPs infeasible under the configured
  shedding and curtailment controls. The solver reports base overloads that
  have no PTDF sensitivity to available generator redispatch; resolving those
  may require additional local controls or a justified rating change.
- A feasible DC-OPF is not proof of AC feasibility; validate its dispatch with
  an AC power flow and check AC voltage and reactive-power limits.

## Usage

```python
from pf_tool import DCOptimalPowerFlow

solver = DCOptimalPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    max_load_shedding_fraction=0.4,
    print_iterations=True,
)
result = solver.solve()

print(f"Feasible: {result.converged}")
print(f"Total absolute redispatch: {result.objective_value_mw} MW")
print(f"Reference adjustments: {result.balance_slack_mw}")
print(f"Load shedding: {result.load_shedding_mw}")
print(f"Fixed-generation curtailment: {result.generation_curtailment_mw}")
print(solver.get_curtailed_generators())
print(solver.get_shed_loads())
for branch in result.branch_flows or []:
    print(branch.from_bus, branch.to_bus, branch.active_power_mw)
```

`generation_mw` contains bus aggregate generation after redispatch and fixed
generation curtailment, including converter injections. `redispatch_mw` maps
each dispatchable generator bus to its change from the input schedule.
`balance_slack_mw` maps reference buses to their bounded balancing adjustment.
`load_shedding_mw` and `generation_curtailment_mw` report restoration controls.
On an infeasible LP, the result reports the solver termination and leaves
solution fields unset rather than returning a partial schedule as a solution.

After a feasible solve, `get_curtailed_generators()` and `get_shed_loads()`
return pandas dataframes containing only buses whose generation or load was
reduced. Each dataframe includes the bus number and name, before/after MW
values, and the curtailed or shed MW. They raise `RuntimeError` if called
before solving or when no feasible solution was found. Generator reductions
include both downward redispatch and fixed-generation curtailment; values are
bus aggregates. Load values are bus-level active loads.

Run `python examples/run_dcopf_adjustments.py` from the repository root to see
both reports for the bundled CASO01 case.

## 11-case BIPS benchmark

The benchmark runs the eleven bundled `CASO*-FLOW.dat` cases with the
restoration LP enabled. The conservative default cap (20% of each bus's native
load) solves CASO10 only; it is insufficient for the other cases' local
overloads. The table below uses `max_load_shedding_fraction=1.0` to show that
the available restoration controls can produce a DC-feasible schedule for all
eleven cases. This is a feasibility demonstration, not a recommended shedding
policy. Times include parsing, PTDF construction, and all HiGHS solves.

| Case | Feasible | Time (s) | Load shed (MW) | Fixed-generation curtailment (MW) | Max branch violation (MW) |
| --- | --- | ---: | ---: | ---: | ---: |
| CASO01 | Yes | 21.293 | 158.463 | 2139.848 | 0 |
| CASO02 | Yes | 13.549 | 119.239 | 4008.439 | 0 |
| CASO03 | Yes | 9.185 | 242.553 | 252.342 | 0 |
| CASO04 | Yes | 12.902 | 70.639 | 32.387 | 0 |
| CASO05 | Yes | 10.916 | 72.769 | 7.567 | 0 |
| CASO06 | Yes | 12.592 | 124.699 | 90.900 | 0 |
| CASO07 | Yes | 10.944 | 93.373 | 7.667 | 0 |
| CASO08 | Yes | 8.974 | 196.296 | 575.316 | 0 |
| CASO09 | Yes | 11.209 | 72.780 | 27.320 | 0 |
| CASO10 | Yes | 9.193 | 53.406 | 8.367 | 0 |
| CASO11 | Yes | 10.090 | 80.811 | 10.167 | 0 |

These are not AC-style convergence failures: the primary PTDF LP lacks enough
controls to relieve the base overloads, and the restoration LP is bounded by
the configured per-bus shedding cap. For example, CASO01 needs 37.5 MW of
shedding at bus 4086 to relieve its local overload; that bus has 166.8 MW of
native load, so a 20% cap permits only 33.36 MW there. With the cap set to 100%,
the final solution sheds 158.463 MW in total (about 0.14% of the case's
114,209.34 MW of load eligible for shedding) and curtails 2,139.848 MW of
fixed generation.
Consequently, a feasible DC schedule is possible, but the control policy and
amount of fixed-generation curtailment must be reviewed before operational use.
The isolated bus-8004 mismatch is within the bounded reference adjustment; it
is not the blocking constraint. A DC-feasible result is not proof of AC
feasibility.
