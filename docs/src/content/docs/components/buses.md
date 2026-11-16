---
title: Buses and generators
description: How DBAR buses, generation, loads, and reactive limits are modeled.
---

Source: `pf_tool/models/bus.py`, `pf_tool/models/generator.py`, and `pf_tool/utils/utils.py`.

## Bus types

| `DBAR.Tb` | Type                   | Fixed quantities                            | Unknowns                   | Equations in Newton                            |
| --------- | ---------------------- | ------------------------------------------- | -------------------------- | ---------------------------------------------- |
| 0         | PQ (load)              | $P^{sp}$, $Q^{sp}$                          | $\theta$, $\lvert V\rvert$ | $\Delta P$, $\Delta Q$                         |
| 1         | PV (generator)         | $P^{sp}$, $\lvert V\rvert$ = `DBAR.V`       | $\theta$                   | $\Delta P$                                     |
| 2         | Reference (Vθ)         | $\lvert V\rvert$, $\theta$ = `DBAR.V`, `A0` | —                          | —                                              |
| 3         | PQ with voltage limits | as PQ                                       | as PQ                      | as PQ; becomes PV at a violated limit (`VLIM`) |

The cases contain six `Tb = 2` reference buses. Isolated systems without a reference bus receive one automatically; see [Network reduction](/pf-tool/components/network/).

## Injections

Each bus has a specified complex injection in pu:

$$
S_i^{sp} = \frac{(P_{g,i} - P_{l,i}) + j\,(Q_{g,i} - Q_{l,i})}{S_{base}}
$$

- `Pg`, `Qg`, `Pl`, and `Ql` come from `DBAR`.
- SVC output is added to `Qg` at its bus; see [SVC](/pf-tool/components/svc/).
- HVDC converter powers are added to `Pl`/`Ql` at rectifiers and to `Pg`/`Ql` at inverters; see [HVDC LCC](/pf-tool/components/hvdc-lcc/).
- Loads are **constant power**.
- The bus shunt `Sh` (Mvar at 1 pu) becomes the susceptance $b^{sh}_i = \text{Sh}/S_{base}$ on the diagonal of $Y_{bus}$. Its injection is therefore $b^{sh}_i\,|V_i|^2$.

## Generators and reactive limits

The **scheduled generation is taken from `DBAR.Pg`/`Qg`**. `DGER` contributes only the active-power limits `Pgn`/`Pgm`, which are stored on the bus as `p_min_mw`/`p_max_mw`. `Pgn` is a minimum and is never added to the schedule.

### Zero-width reactive range

ANAREDE writes blank reactive limits as `Qgn = Qgm = 0`. More than 200 `Tb = 1` buses in each case have these limits. All of them have a stored output of `Qg = 0`; ANAREDE solved them at a zero-width limit. They are therefore built as **PQ buses with fixed $Q_g$**.

### Reactive limit enforcement (`QLIM`)

With the default **simultaneous** method, every PV bus with reactive limits gets two Newton unknowns: its voltage magnitude and its reactive output change $q_g$. One complementarity equation replaces the voltage condition:

$$
\operatorname{mid}\big(q_g - q_g^{min},\; q_g - q_g^{max},\; \lvert V_g\rvert - V_g^{sp}\big) = 0 .
$$

Inside its limits the generator holds $V_g^{sp}$. At a limit it delivers exactly that limit, and its voltage is free. Because this is part of the Newton system, a generator can reach a limit and **come back off it** within the iterations. After the solve, generators at a limit are reported as PQ at that limit. See [Simultaneous control method](/pf-tool/methods/newton-raphson/#simultaneous-control-method).

With the **sequential** method, every PV bus checks the generation needed to hold its voltage after each converged inner solve:

$$
Q_{g,i}^{req} = Q_i^{calc}\,S_{base} + Q_{l,i} - Q^{svc}_i
$$

- If $Q_{g,i}^{req} > Q_{\max} + 0.1$ Mvar, the bus becomes PQ with $Q_g = Q_{\max}$.
- If $Q_{g,i}^{req} < Q_{\min} - 0.1$ Mvar, the bus becomes PQ with $Q_g = Q_{\min}$.
- The equations are then solved again. A converted bus stays PQ, so there is no back-switching.

### Voltage-limited load buses (`VLIM`)

Only buses with `Tb = 3` take part. With the simultaneous method, such a bus gets an extra reactive injection $q_i$ with $\operatorname{mid}(\lvert V_i\rvert - V^{min}_i,\; \lvert V_i\rvert - V^{max}_i,\; q_i) = 0$: the injection is zero inside `Vmn`–`Vmx` and holds the voltage at the violated limit otherwise. With the sequential method, a bus that leaves its `DGLT` band by more than 0.001 pu becomes a PV bus held at the violated limit.

Earlier versions applied this rule to every PQ bus. That conflicted with the generator reactive limits and caused PV/PQ cycling, so it is now restricted to the ANAREDE `Tb = 3` buses.
