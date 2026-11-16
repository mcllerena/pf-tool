---
title: Tap and phase controls
description: LTC voltage control (CTAP), phase-shifter flow control (CPHS), and tap reconciliation.
---

Source: `pf_tool/models/transformer.py` and `pf_tool/newton_raphson.py`.

## Control groups

`build_tap_controls` creates `TapControl` objects. Each object has one scalar state, the **shift** $s_c$, which is applied to every branch in the group:

| Mode                | Created from                                                     | Branches                         | State                           | Target                     |
| ------------------- | ---------------------------------------------------------------- | -------------------------------- | ------------------------------- | -------------------------- |
| `V` (LTC)           | `DLIN` with $0<T_{mn}<T_{mx}$ and a controlled bus (`DOPC CTAP`) | All LTCs regulating the same bus | $a_k = a_k^0 + s_c$             | $\lvert V_c\rvert$         |
| `P` (phase shifter) | `DCTR` with `PCtrl = P` (`DOPC CPHS`)                            | The single `DCTR` circuit        | $\varphi_k = \varphi_k^0 + s_c$ | $P$ leaving terminal `Ext` |

Parallel LTCs that regulate one bus share one shift, so they remain tap-equalized. After the network reduction, groups whose controlled buses merge are combined. Transformers that the reduction contracts leave the group.

The shift is bounded so that every branch respects its own limits:

$$
\max_k\,(T_{mn,k} - a_k^0) \;\le\; s_c \;\le\; \min_k\,(T_{mx,k} - a_k^0).
$$

For phase shifters, the bounds come from `DCTR.Fmin`/`Fmax` in the `Psh` sign convention.

## LTC voltage control with a dead band

ANAREDE treats an LTC as a band controller with tolerance `DCTE TLTC`, which is 0.01 pu in the cases. Both control methods follow this logic.

With the default **simultaneous** method, every LTC group is a Newton unknown from the start, with the dead-band law

$$
\operatorname{mid}\big(s - s^{min},\; s - s^{max},\; \sigma\,d(\kappa_c\lvert V_c\rvert) + k_t\,(s - s_0)\big) = 0,
$$

where $d$ is the distance of the controlled voltage outside $V^{sp}\pm\text{TLTC}$. Inside the band the tap holds its starting shift $s_0$. Outside it, the tap moves until the voltage is at the band edge, or until the tap reaches a limit. See [Simultaneous control method](/pf-tool/methods/newton-raphson/#simultaneous-control-method). The controlled bus may also be a generator bus with reactive limits, which takes over once that generator reaches a limit.

With the **sequential** method:

1. Every LTC group starts **inactive**: its tap is fixed.
2. After an inner Newton solve, a group is **activated** when its controlled voltage leaves $V^{sp}\pm\text{TLTC}$. Here $V^{sp}$ is the `DBAR.V` value of the controlled bus.
3. An active group regulates to the **nearest band edge**:

$$
g_c(x) = \lvert V_c\rvert\,\kappa_c - V_c^{target} = 0,\qquad
V_c^{target} = V^{sp} \pm \text{TLTC}.
$$

In this equation, $\kappa_c$ is the voltage scale of the controlled bus in the reduced network.

Weak LTCs regulate the bus on their tap side, where the tap has little effect on that voltage. Without the dead band, they swing their taps across the whole range and distort downstream voltages by up to 0.12 pu. With the band, the solution matches ANAREDE to within 0.01 pu at the 99th percentile.

LTC groups are not activated (sequential) or not added as unknowns (simultaneous) in these cases:

- the controlled bus is the reference bus or a PV bus without reactive limits (in the sequential method, any PV bus);
- the controlled bus is regulated by an SVC;
- the controlled bus does not exist.

## Phase-shifter flow control

A phase shifter holds the active power that leaves the `Ext` terminal and enters the branch:

$$
g_c(x) = \operatorname{Re} S_{\text{Ext}}(V_f, V_t, \varphi_k) - \frac{V_{sp}}{S_{base}} = 0 .
$$

It is active from the start.

## Limits and conflicting controls

Three safeguards in Newton keep the transformer controls well posed:

| Safeguard  | Rule                                                                                                                                                                                                                                                                                                     |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Step limit | If a Newton step asks for $\lvert\Delta s\rvert > 0.05$ (tap) or $> 0.1$ rad (phase), the whole step is shortened. This keeps the search direction.                                                                                                                                                      |
| Limit      | When the shift is clamped at a bound and keeps pushing outward for 3 iterations, the group is fixed at that limit and its equation is dropped.                                                                                                                                                           |
| Conflict   | When a step asks for $\lvert\Delta s\rvert > 0.2$ (tap) or $> 1$ rad (phase), the control has almost no independent effect on its target. It is fixed and the step is recomputed. Examples are two LTC groups regulating electrically identical buses, or an LTC whose target barely depends on its tap. |

## Reconciling settings with the stored voltages

Saved cases contain the **solved voltages**, but some LTCs keep their **pre-solution taps**. Around the Sapeaçu 230/500 kV units at bus 6368 in `CASO04`, the stored tap 0.9695 across a 0.05 % reactance contradicts the stored voltages, which imply a tap of 0.918. The result is a 357 pu initial mismatch, and Newton diverges.

Before solving, `reconcile_tap_controls` corrects such inconsistencies. For every group, the terminal buses $\mathcal{T}_c$ of its branches are used to linearize the mismatch $r$ with respect to the shift. The vector $r$ contains P mismatches at non-reference terminals and Q mismatches at PQ terminals. The solver then takes one least-squares step:

$$
g = \frac{\partial r}{\partial s_c},\qquad
s_c \leftarrow \operatorname{clip}\Big(s_c - \frac{g^\top r}{g^\top g},\; s_c^{\min}, s_c^{\max}\Big).
$$

The correction is applied only when these conditions hold:

- the terminal mismatch is significant, with $\lVert r\rVert^2 \ge 0.25$;
- the linearized mismatch drops to at most 25 % of its value.

Consistent transformers are therefore left unchanged. The solver performs up to three sweeps. Both solvers use this step; it is what makes the cold-start optimization possible.
