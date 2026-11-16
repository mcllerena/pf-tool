---
title: Fast decoupled
description: Decoupled Newton and constant B'/B'' fast decoupled power flow, with SVC, LTC, phase-shifter, CSC, shunt, generator-limit, and LCC controls.
---

Source: `pf_tool/fast_decoupled.py` (`FastDecoupledPowerFlow`).

`FastDecoupledPowerFlow` subclasses [`NewtonRaphsonPowerFlow`](/pf-tool/methods/newton-raphson/). It shares the unknowns, the mismatch equations $F(x)$, the device models, the line search, and the [sequential control method](/pf-tool/methods/newton-raphson/#sequential-control-method). It changes only how each step $\Delta x$ is computed.

```python
from pf_tool import FastDecoupledPowerFlow

solver = FastDecoupledPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    enhancement=True,   # constant B'/B''; False uses the decoupled Jacobian blocks
    warm_start=True,    # start from a Newton-Raphson solution
    print_iterations=True,
)
result = solver.solve()
```

| Argument             | Default            | Meaning                                                                       |
| -------------------- | ------------------ | ----------------------------------------------------------------------------- |
| `filename`           | required           | Case to solve                                                                 |
| `enhancement`        | `False`            | `False`: decoupled Newton. `True`: constant $B'$/$B''$ fast decoupled method  |
| `warm_start`         | `True`             | Solve with sequential Newton-Raphson first and re-solve from that state       |
| `tolerance`          | `TEPA / BASE`      | Maximum absolute P, Q, and control mismatch, in pu                            |
| `max_iterations`     | `ACIT` from `DCTE` | Full (P and Q) iterations per control pass                                    |
| `max_control_passes` | 50                 | Outer passes for generator limits, shunts, LCC set-points, and LTC activation |
| `print_iterations`   | `False`            | Print the per-iteration table (same columns as Newton-Raphson)                |

After `solve()`, the solver also exposes:

- `factorizations`: sparse LU factorizations performed;
- `coupling_corrections`: decoupled iterations replaced by a [coupling correction](#coupling-correction);
- `warm_start_iterations`, `warm_start_converged`: the Newton-Raphson warm start.

## Decoupling the Newton step

With the [Newton-Raphson Jacobian](/pf-tool/methods/newton-raphson/#jacobian) partitioned by active and reactive equations, the Newton step solves

$$
\begin{bmatrix} J_{P\theta} & J_{PV} \\ J_{Q\theta} & J_{QV} \end{bmatrix}
\begin{bmatrix} \Delta\theta \\ \Delta|V| \end{bmatrix}
= -\begin{bmatrix} \Delta P \\ \Delta Q \end{bmatrix}.
$$

In transmission networks the series resistance is small compared with the reactance, and the angle difference across a branch is small. Active power then depends mainly on angles, and reactive power mainly on voltage magnitudes, so $J_{PV}$ and $J_{Q\theta}$ are neglected:

$$
J_{P\theta}\,\Delta\theta = -\Delta P, \qquad J_{QV}\,\Delta|V| = -\Delta Q .
$$

Two systems of about half the size replace one. The mismatches $\Delta P$ and $\Delta Q$ are still computed with the **full** AC power-flow equations, so the approximation affects only the path to the solution, not the solution: a converged fast decoupled result satisfies the same tolerance as Newton-Raphson.

### Device equations in each half

Each control equation is assigned to the half its unknown mainly affects:

| Half | Unknowns                                                  | Equations                                                                              |
| ---- | --------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| P-θ  | $\theta$ of PV and PQ buses; phase-shifter angles         | Active-power mismatch; phase-shifter flow ($\operatorname{Re}S_{\text{Ext}} - P^{sp}$) |
| Q-V  | $\lvert V\rvert$ of PQ buses; SVC outputs; LTC tap shifts | Reactive-power mismatch; SVC control or limit law; LTC voltage                         |

The remaining components need no equation of their own:

- **Series compensators (CSC)** and transformer taps enter through $Y_{bus}$, so they appear in both halves.
- **Generator reactive limits**, **voltage-limited buses** (`VLIM`), **switched shunts** (`STEP`), **LTC activation** (`CTAP`), and **LCC converter set-points** are updated between control passes, as in the [sequential control method](/pf-tool/methods/newton-raphson/#sequential-control-method). The LCC converters are constant P/Q injections during each pass.

## Decoupled Newton (`enhancement=False`)

At every iteration the diagonal blocks $J_{P\theta}$ and $J_{QV}$, with their device rows and columns, are taken from the full Jacobian at the current point and factored. Both corrections are computed from the same point and applied together:

$$
x^{k+1} = x^k + \alpha\left(\Delta x_{P\theta} + \Delta x_{QV}\right),
$$

where $\alpha$ comes from the [Newton-Raphson line search](/pf-tool/methods/newton-raphson/#step-control). Each iteration costs two factorizations of half-size matrices.

## Fast decoupled (`enhancement=True`)

At a flat profile ($|V| = 1$, $\theta = 0$) and with the decoupling assumptions, the blocks reduce to constant susceptance matrices:

$$
J_{P\theta} \approx -[V]\,B'\,[V], \qquad J_{QV} \approx -[V]\,B''.
$$

Replacing the right-hand $[V]$ by the identity and dividing each equation by $|V_i|$ gives the fast decoupled equations:

$$
-B'\,\Delta\theta = \frac{\Delta P}{|V|}, \qquad -B''\,\Delta|V| = \frac{\Delta Q}{|V|}.
$$

- **$B''$** is $\operatorname{Im}(Y_{bus})$ restricted to the PQ buses. It includes line charging, shunts, and off-nominal taps, which all affect reactive power.
- **$B'$** is built from **series reactances only** (the XB scheme): for each pair of connected buses, $x_{jk} = \operatorname{Im}(-1/Y_{jk})$ and $b'_{jk} = 1/x_{jk}$. Shunts, line charging, series resistance, and off-nominal tap magnitudes are left out, since they mainly affect reactive power. Using the reactance rather than $\operatorname{Im}(Y_{bus})$ matters on these cases: they contain branches with $R/X$ up to 190, and $\operatorname{Im}(1/(R+jX))$ badly underestimates their angle stiffness.
- **Device rows and columns** (SVC, LTC, phase shifter) are taken from the full Jacobian at the flat profile, the same assumption behind $B'$ and $B''$.

Both matrices are **factored once** per control pass, and refactored only when the active SVCs, their limit states, or the regulating tap and phase controls change.

The two halves **alternate**. The P-θ correction is applied first, with a line search judged on the P-θ equations. The mismatches are then recomputed, and the Q-V correction starts from the new angles. One iteration is one P half and one Q half.

## Coupling correction

The decoupling assumptions fail locally on parts of the Brazilian cases: nearly resistive branches, series capacitors, and chains of negative-reactance transformer star branches. There a decoupled iteration may not reduce the mismatch. Each iteration is therefore checked for **sufficient decrease**:

$$
\lVert F^{k+1}\rVert_\infty < 0.9\,\lVert F^k\rVert_\infty
\quad\text{or}\quad
\lVert F^{k+1}\rVert_2 < 0.9\,\lVert F^k\rVert_2 .
$$

If neither holds, the iteration is undone. If no control reached a limit during it, a **coupling-corrected step** is taken from the same point instead:

1. Solve the full system $J\,\Delta x = -F$ with GMRES (relative tolerance $10^{-3}$, restart 30, at most 3 cycles), using the decoupled blocks $J_{P\theta}$ and $J_{QV}$ at the current point as the preconditioner. The full Jacobian is only multiplied.
2. If GMRES does not reach its tolerance, factor the full Jacobian for that step.
3. Fix controls whose step is non-physical, as in [Newton-Raphson conflict screening](/pf-tool/methods/newton-raphson/#step-control), for example two LTCs regulating the same voltage.
4. Apply the step with the line search.

Away from those spots the decoupled iterations do the work. From a warm start, one to three corrections are needed per solve.

## Warm start

With `warm_start=True` (default), a copy of the case is first solved with the **sequential** Newton-Raphson method. Its solved state becomes the starting point: bus angles, PQ voltage magnitudes, generator PV/PQ types, transformer taps and phase shifts, switched-shunt steps, SVC outputs, and LCC injections. PV setpoints are not changed. The fast decoupled method then solves the case from that state with its own control passes and convergence test.

When the warm start is already a solution, the fast decoupled solve confirms it in zero iterations. Otherwise it typically needs 3–11 iterations, mainly for the LTC and shunt updates of its own passes.

With `warm_start=False`, the solve starts from the voltages stored in the `.dat` file.

## Results

All 11 cases in `tests/data/` converge with both variants and both starts. Each result is within the case tolerance of $10^{-4}$ pu, and matches the stored ANAREDE voltages to the same p99 as Newton-Raphson. Times include parsing, reduction, and, for the warm start, the Newton-Raphson solve.

### Warm start

| Case   | Buses | `enhancement=False` iterations | Corrections | Time (s) | `enhancement=True` iterations | Corrections | Time (s) | Median / p99 dV (pu) |
| ------ | ----: | -----------------------------: | ----------: | -------: | ----------------------------: | ----------: | -------: | -------------------: |
| CASO01 | 12579 |                              0 |           0 |      5.2 |                             0 |           0 |      5.3 |      0.0008 / 0.0083 |
| CASO02 | 12579 |                              0 |           0 |      4.9 |                             0 |           0 |      5.0 |      0.0008 / 0.0050 |
| CASO03 | 12579 |                              3 |           1 |      6.2 |                             3 |           1 |     20.5 |      0.0008 / 0.0077 |
| CASO04 | 12572 |                              0 |           0 |      4.7 |                             0 |           0 |      4.2 |      0.0005 / 0.0098 |
| CASO05 | 12578 |                              4 |           1 |      5.5 |                             3 |           1 |     12.2 |      0.0012 / 0.0100 |
| CASO06 | 12613 |                              4 |           1 |      5.3 |                             4 |           1 |     18.5 |      0.0009 / 0.0100 |
| CASO07 | 12613 |                              5 |           2 |      6.2 |                             5 |           1 |     18.9 |      0.0010 / 0.0093 |
| CASO08 | 12613 |                              9 |           3 |      7.4 |                            11 |           3 |     44.2 |      0.0017 / 0.0090 |
| CASO09 | 12607 |                              4 |           1 |      6.5 |                             3 |           1 |     14.7 |      0.0007 / 0.0101 |
| CASO10 | 12611 |                              3 |           1 |      5.9 |                             3 |           1 |     12.8 |      0.0010 / 0.0097 |
| CASO11 | 12613 |                              3 |           1 |      6.1 |                             3 |           1 |     13.0 |      0.0007 / 0.0098 |

### Cold start

| Case   | Buses | `enhancement=False` iterations | Corrections | Time (s) | `enhancement=True` iterations | Corrections | Time (s) | Median / p99 dV (pu) |
| ------ | ----: | -----------------------------: | ----------: | -------: | ----------------------------: | ----------: | -------: | -------------------: |
| CASO01 | 12579 |                             30 |          16 |     16.1 |                            29 |          14 |     86.9 |      0.0008 / 0.0083 |
| CASO02 | 12579 |                             30 |          15 |     13.9 |                            27 |          12 |     83.8 |      0.0008 / 0.0050 |
| CASO03 | 12579 |                             27 |          13 |     16.9 |                            26 |          11 |    104.7 |      0.0008 / 0.0077 |
| CASO04 | 12572 |                             14 |           7 |      7.4 |                            14 |           5 |     49.7 |      0.0005 / 0.0098 |
| CASO05 | 12578 |                             20 |           9 |      9.3 |                            21 |           7 |     63.0 |      0.0012 / 0.0100 |
| CASO06 | 12613 |                             20 |           9 |      9.0 |                            16 |           6 |     51.8 |      0.0009 / 0.0100 |
| CASO07 | 12613 |                             17 |           8 |      8.2 |                            18 |           7 |     53.1 |      0.0010 / 0.0093 |
| CASO08 | 12613 |                             39 |          20 |     25.4 |                            31 |          11 |    145.1 |      0.0016 / 0.0090 |
| CASO09 | 12607 |                             18 |          11 |     11.2 |                            20 |           9 |    104.4 |      0.0007 / 0.0101 |
| CASO10 | 12611 |                             23 |          12 |     16.1 |                            23 |          11 |     84.7 |      0.0010 / 0.0096 |
| CASO11 | 12613 |                             33 |          14 |     11.5 |                            24 |          11 |     81.4 |      0.0007 / 0.0098 |

### Reading the results

- **Warm start:** the fast decoupled solve starts at, or close to, the Newton-Raphson solution, so the time is mostly the warm start. It is the recommended setting.
- **Cold start:** from the stored voltages, both variants need 14–39 iterations. Coupling corrections replace about a third to a half of them, where the network violates the decoupling assumptions. The constant-matrix variant needs slightly fewer corrections.
- **Time per iteration:** the constant-matrix variant factors less often, but in pure Python the line search and the coupling corrections dominate the cost on 12,600-bus cases, so `enhancement=True` is slower here than the Jacobian-block variant. Newton-Raphson remains the fastest way to solve these cases; the fast decoupled solver is provided for comparison, teaching, and cross-checking.
