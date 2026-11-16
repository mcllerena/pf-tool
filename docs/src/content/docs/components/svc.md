---
title: Static VAR compensators
description: DCER SVCs with reactive-power (P) and current (I) droop control and susceptance limits.
---

Source: `pf_tool/models/svc.py`, `pf_tool/newton_raphson.py`, and `pf_tool/optimization.py`.

## Data

Each in-service `DCER` record has these parameters:

| Symbol               | From                 | Meaning                                                      |
| -------------------- | -------------------- | ------------------------------------------------------------ |
| $d$                  | `Bus`                | Terminal (device) bus                                        |
| $c$                  | `Bctrl`              | Controlled bus; the device bus is used if `Bctrl` is missing |
| $V^{ref}$            | `DBAR.V` of $c$      | Voltage reference                                            |
| $m$                  | `Incl`/100           | Droop slope (pu V per pu Q)                                  |
| $q^0$                | `Qg`/`BASE`          | Initial output; it is also added to the bus `Qg`             |
| $Q_{\min}, Q_{\max}$ | `Qgn`, `Qgm` /`BASE` | Inductive and capacitive capability **at 1 pu**              |
| mode                 | `Ctrl`               | `P` reactive-power droop, `I` current droop                  |

An SVC is **active** when its device bus is PQ. If the bus is PV or the reference bus, that bus already fixes the voltage.

## Operating regions

The SVC output $q_s$ is an unknown. A **region** selects one of three equations:

$$
\text{regulating (0):}\quad
\begin{cases}
\kappa_c|V_c| - V^{ref} + m\,q_s = 0 & \text{mode } P\\[2pt]
\kappa_c|V_c| - V^{ref} + m\,\dfrac{q_s}{\kappa_d|V_d|} = 0 & \text{mode } I
\end{cases}
$$

$$
\text{saturated low } (-1):\; q_s = Q_{\min}(\kappa_d|V_d|)^2,\qquad
\text{saturated high } (+1):\; q_s = Q_{\max}(\kappa_d|V_d|)^2 .
$$

- **Mode $P$:** the controlled voltage falls linearly with reactive output.
- **Mode $I$:** the voltage falls with reactive current, $q/V$.
- **Capability limits:** the SVC is a variable susceptance, so its limits scale with the square of the **terminal** voltage.
- **Scale factors:** $\kappa$ are voltage scales from the network reduction; they equal 1 for a bus that was not merged.

The output enters the reactive balance of the device bus:

$$
Q_d^{calc} - Q_d^{sp} - \sum_{s\,\in\,d} q_s = 0,
$$

Here $Q_d^{sp}$ excludes the initial SVC output that was added to `Qg`.

## Region logic

The two solvers use the same region logic at different times.

**Newton** checks the region in every iteration (`refresh_svc_limits`). From the controlled voltage, it computes the output that the control law asks for:

$$
q^{req} = \frac{V^{ref} - \kappa_c|V_c|}{m}\quad(\text{mode } I:\ \times\,\kappa_d|V_d|)
$$

It then clamps the SVC to its capability. The Jacobian row switches between the control law and the limit equation.

**The optimization model** fixes each region for an NLP solve. After the solve, `update_svc_regions` changes a region in these cases:

- A regulating SVC whose output left its capability, or sits on a limit while $q^{req}$ points beyond it, saturates.
- A saturated SVC returns to regulation when $q^{req}$ falls back inside its capability. It never jumps directly to the opposite limit, which prevents ±1 cycling.

## Solved outputs

After a converged Newton pass, the solved outputs are written back to `case.svcs`, and the bus generation is updated accordingly. The next pass and an optimization warm start therefore begin from the solved SVC state.
