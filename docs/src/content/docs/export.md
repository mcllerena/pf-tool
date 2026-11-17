---
title: Exporting results
description: ExportSolution writes every solved component to an Excel workbook, one sheet per component.
---

Source: `pf_tool/exporter.py` (`ExportSolution`).

## Usage

Solve the case, then pass the solver to `ExportSolution`:

```python
from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat")
solver.solve()
ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

The same call works with `OptimizationPowerFlow` and `ACOptimalPowerFlow`. The solver-script examples in `examples/` end with this step.

| Argument      | Default   | Meaning                                                        |
| ------------- | --------- | -------------------------------------------------------------- |
| `solver`      | —         | A solver on which `solve()` was called                         |
| `output_path` | —         | Workbook file; missing folders are created                     |
| `format`      | `"excel"` | Only Excel (`.xlsx`) is supported                              |
| `export`      | `True`    | `False` builds the tables (`.sheets`) without writing the file |

## Sheets

Each sheet has a bold, frozen header row. Powers are in MW and Mvar, voltages in pu, and angles in degrees.

| Sheet          | One row per                                         | Main columns                                                                                                                                                                 |
| -------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Summary`      | Field                                               | Case, method, convergence, iterations, max mismatch, component counts, total generation and load, AC losses, voltage violations, overloads                                   |
| `Buses`        | Original bus                                        | Type, area, $V$ (pu and kV from `DGBT`), angle, generation, load, shunt Q, SVC Q, HVDC P/Q, `DGLT` limits, violation, representative bus, collapsed flag                     |
| `Generators`   | Bus with generation (`Tb` 1/2 or nonzero `Pg`/`Qg`) | $P_g$, $Q_g$, `Qgn`/`Qgm`, `Pgm` and reserve, $V$, angle, reactive-limit state                                                                                               |
| `Loads`        | Bus with `DBAR` load                                | $P_l$, $Q_l$, $V$                                                                                                                                                            |
| `Lines`        | `DLIN` line                                         | R, X, B, tap, shift, rating, flows at both ends, power factor, capacitive/inductive, loading %, losses, overload flag, collapsed flag                                        |
| `Transformers` | `DLIN` transformer (tap ≠ 1, phase shift, or LTC)   | As for lines, with the solved tap and shift                                                                                                                                  |
| `LTC`          | LTC transformer                                     | Controlled bus, solved tap and limits, set-point and actual voltage, control state                                                                                           |
| `PST`          | `DCTR` phase shifter                                | Solved shift and limits, target and measured MW, control state                                                                                                               |
| `HVDC`         | Converter: two rows per link                        | AC bus and voltage, rectifier/inverter, power or slack control, power order, AC P and Q, link loss, $V_{dc}$, $I_{dc}$, α or γ, overlap μ, power-factor angle, converter tap |
| `SVC`          | `DCER` SVC                                          | Mode, slope, $V_{ref}$, controlled and terminal voltage, output, voltage-dependent limits, control-law residual, state                                                       |
| `CSC`          | `DCSC` compensator                                  | Mode, X and its limits, bypass, flows, loading, status                                                                                                                       |
| `Shunts`       | `DBSH` bank, bus or line                            | Kind, line, control, band, controlled voltage, units in service per group, nominal and actual Q, range, state                                                                |

### State columns

| Sheet               | Values                                                                                                                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LTC`               | `InBand` (tap fixed, voltage inside $V^{sp}\pm$`TLTC`), `Regulating`, `AtLimit`, `Conflict`, `Disabled` (controlled bus is PV or regulated by an SVC), `Contracted`, `Fixed` (no control created) |
| `PST`               | `Regulating`, `AtLimit`, `Conflict`                                                                                                                                                               |
| `SVC`               | `Free` (regulating), `Qmin`/`Qmax` (saturated), `Inactive` (device bus is not PQ)                                                                                                                 |
| `Shunts`            | `Fixed`, `InBand`, `BelowBand`, `AboveBand`, `Frozen` (anti-hunting)                                                                                                                              |
| `Generators.QLimit` | `Qmax`/`Qmin` (PV bus switched by `QLIM`), `ZeroRange` (`Qgn = Qgm`)                                                                                                                              |

## How the values are obtained

The solvers work on the [reduced network](/pf-tool/components/network/). After solving, `ReducedCase.write_back` copies every solved control back to the **original** case. That covers bus types, switched-shunt steps, SVC outputs, LCC set-points, and transformer taps and phase shifts. The exporter then works on the original buses and branches with the expanded voltages.

**Branch flows.** For every branch that was not contracted, the exporter uses the π-model terminal powers $S_f$ and $S_t$ with the final tap and phase.

**Collapsed links.** These are switches, bypassed CSCs, and other branches at or below the [zero impedance threshold](/pf-tool/components/network/#zero-impedance-threshold). They cannot be evaluated from voltages, because both ends are the same electrical node. In each reduced node, the exporter solves the series flows from Kirchhoff's current law at the member buses:

$$
\sum_{k\,\text{collapsed}} A_{mk}\,f_k = S^{local}_m - S^{shunt}_m - \sum_{k\,\text{not collapsed}} S_{m,k} - \sum_{k\,\text{collapsed}} S^{charging}_{m,k}
$$

The system is solved with least squares, which gives the minimum-norm solution for meshed groups. Each link keeps its line charging, $-j\,\tfrac{b}{2}|V|^2$ per end. The test case `CASO01` has 1,222 collapsed links. In the exported solution, every bus balances to within $3\times10^{-5}$ MVA, and the system active-power balance closes to $10^{-6}$ MW.

**Generator outputs.** The solver balances power per reduced node, so the machine output of a node is:

$$
P^{m}_{node} = \sum_{i\in node}\big(P^{calc}_i - P^{hvdc}_i + P^{l}_i\big),\qquad
Q^{m}_{node} = \sum_{i\in node}\big(Q^{calc}_i - Q^{svc}_i - Q^{hvdc}_i + Q^{l}_i\big).
$$

It is allocated to the node's buses in three ways:

- **Active power:** PV generators keep their scheduled $P_g$. A reference node gives the remainder to its `Tb = 2` buses.
- **Reactive power at PV and reference nodes:** the output is shared among the voltage-controlling generators in proportion to their reactive ranges `Qgm − Qgn`.
- **Reactive power at PQ nodes:** each bus keeps its scheduled $Q_g$.

**HVDC and SVC terms** are reported separately from machine generation and `DBAR` load:

- **Rectifier:** P and Q are load.
- **Inverter:** P is a source, and Q is load.
- **SVC:** the output is listed in its own column.

The `Loads` sheet therefore shows the `DBAR` loads only.
