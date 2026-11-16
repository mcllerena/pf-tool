---
title: Network reduction
description: Zero impedance threshold, island reference assignment, and the reduced case.
---

Source: `models/network_reduction.py`, `utils/network.py`, and `newton_raphson.py`.

## Zero impedance threshold

Links with very small impedance make the Jacobian ill-conditioned. These include breakers modeled as lines, bypassed CSCs, and ideal ties. Any branch with $|r + jx| \le 2\times10^{-4}$ pu is therefore **contracted**. Its two terminals become one electrical node.

Contraction respects ideal transformer ratios. Union-find tracks, for every bus $i$, its group representative and a complex **scale** $s_i$ such that:

$$
V_i = s_i\,V_{\text{rep}(i)} .
$$

Contracting a branch with ratio $N = a e^{j\varphi}$ between $f$ and $t$ imposes $s_f = N\,s_t$. If a loop of contracted branches implies inconsistent ratios, that branch is not merged.

### Reduced case

The reduced case is built from the contraction matrix $T$, where $T_{i,\text{rep}(i)} = s_i$:

| Item                            | Reduced value                                                                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Branch admittance matrix        | $Y^{red} = T^{H}\,Y^{branch}\,T$, which is exact because the contracted links carry no voltage drop                            |
| Injections $P_g, Q_g, P_l, Q_l$ | Sum over the group                                                                                                             |
| Shunt susceptance               | $\sum_i                                                                                                                        | s_i | ^2\,b_i^{sh}$ |
| Bus type                        | Representative chosen by priority reference > PV > PQ, then by branch degree                                                   |
| PV reactive limits              | Sum over the group's PV members                                                                                                |
| SVC, shunt, and LCC buses       | Mapped to the representative, with voltage scales $                                                                            | s_i | $             |
| Tap controls                    | Branches mapped with their terminal scales; contracted transformers dropped; LTC groups that now regulate the same node merged |

`CASO01` shrinks from 12,579 to 11,369 buses. After solving, voltages are expanded with $V_i = s_i V_{\text{rep}(i)}$. `ReducedCase.write_back` then copies every solved control state to the original case:

- bus types go to each group's representative;
- switched-shunt steps, SVC outputs, LCC set-points, and transformer taps and phase shifts go to the original devices;
- original bus injections and shunt susceptances change by the same amounts.

See [Exporting results](/pf-tool/export/).

## Island references

The solver finds connected components of $Y_{bus}$. Every island without a reference bus receives one: the PV bus with the largest scheduled active generation, or any bus if the island has no PV bus. Isolated systems therefore keep a well-defined angle reference and slack.

## Initial point

Both solvers start from the **stored voltages** `DBAR.V` and `A0`, which are ANAREDE's solution.

1. **Tap reconciliation** fixes transformer settings that contradict those voltages; see [Tap and phase controls](/pf-tool/components/transformer-controls/#reconciling-settings-with-the-stored-voltages).
2. **Newton angle warm start:** before Newton starts, a short active-power-only angle correction may adjust the angles. It runs a few decoupled $P$–$\theta$ Newton steps from both the stored angles and flat angles, and keeps the start with the smaller mismatch.
