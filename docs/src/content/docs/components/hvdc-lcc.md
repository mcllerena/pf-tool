---
title: HVDC LCC links
description: Line-commutated converter links represented as voltage-dependent AC injections.
---

Source: `pf_tool/models/hvdc_lcc.py`.

The cases contain 17 LCC poles and lines. They include Itaipu (Foz do Iguaçu–Ibiúna), Madeira (Santo Antônio–Araraquara), the Porto Velho back-to-back converter, Belo Monte (Xingu–Estreito and Xingu–Terminal Rio), and the Alumar links.

The DC network is not part of the AC Newton state. Each link becomes active and reactive **injections at its two AC buses**. With the default simultaneous control method, the converter reactive power is a function of the AC terminal voltage inside the Newton system (see [Simultaneous solve](#simultaneous-solve)). With `control_method="sequential"`, the injections are recomputed from the AC solution in the outer control passes.

## Building a link

For each in-service `DCLI` line, the solver builds the link in six steps:

1. It finds the converter (`DCNV`) at each DC bus. Converter `Modo = R` is the rectifier and `Modo = I` is the inverter.
2. It reads the controls (`DCCV`) of both converters. Exactly one converter must have `F = "F"`. That converter is the DC **slack**: it absorbs the line losses. The other converter must use power control (`TCnv = P`) and holds the power order.
3. It reads the power order $P_{dc} = |\text{Vsp}|$ (MW), the DC voltages $V_{dc}$ (`DCBA.V`, kV), and the line resistance $R$ (`DCLI.R`, Ω).
4. It computes the DC current and losses:

$$
I_{dc} = \frac{P_{dc}}{V_{dc}^{\,P}},\qquad P_{loss} = I_{dc}^2 R ,
$$

where $V_{dc}^{\,P}$ is the DC voltage at the power-controlled converter. The slack end carries the losses, and its DC voltage is offset by $I_{dc}R$:

| Slack converter (`F`) | Rectifier active power | Inverter active power |
| --------------------- | ---------------------- | --------------------- |
| rectifier             | $P_{dc} + P_{loss}$    | $P_{dc}$              |
| inverter              | $P_{dc}$               | $P_{dc} - P_{loss}$   |

5. It computes the **reactive consumption** of each converter from its control angle, firing angle α at the rectifier or extinction angle γ at the inverter (`DCCV.Ang`), and its commutation overlap $\mu$:

$$
Q = P\,\tan\!\big(\alpha + \tfrac{\mu}{2}\big),
$$

$$
\cos(\alpha+\mu) = \cos\alpha - \frac{\sqrt{2}\,X_c\,I_{dc}}{a\,V_{fs}\,|V_{ac}|},\qquad
X_c\,[\Omega] = \frac{X_c\,[\%]}{100}\,\frac{V_{fs}^2}{S_{nt}} .
$$

Here $a$ is the converter transformer tap (`DCCV.Ttr`), $V_{fs}$ is the secondary voltage (kV), $S_{nt}$ is the transformer rating (MVA), and $|V_{ac}|$ is the AC terminal voltage (pu). For links with a base voltage from 200 to 750 kV, the rectifier uses $P_{dc}+P_{loss}$ when computing $Q$.

6. It adds the injections to the AC buses:
   - the rectifier active and reactive power are added as **load**;
   - the inverter active power is added as **generation**, and its reactive power is added as **load**.

## Simultaneous solve

`converter_operating_point` evaluates the same relations as the sequential update, but without damping and without changing the link. Starting from the present tap and overlap, it alternates the overlap equation and the tap target until both stop changing, which takes a few iterations. The result is the converter reactive power, tap, and overlap as a **function of the AC terminal voltage** alone.

The Newton residual adds the change of this reactive load at each terminal bus. Its voltage derivative, a central difference with a $10^{-6}$ pu step, is added to the $Q$–$\lvert V\rvert$ diagonal of the Jacobian. Active power does not depend on the AC voltage. After convergence, the solved reactive power, taps, and overlap angles are written back to the links and the bus loads.

## Updates during the solve (sequential method)

After each converged inner solve, `update_lcc_from_dc_solution` performs four updates:

1. It recomputes losses and the DC voltage at the slack end.
2. It recomputes the overlap angle $\mu$ from the present AC terminal voltages first, then computes the target $Q$ at each converter from that $\mu$. A voltage change therefore affects $Q$ in the same pass.
3. It moves each **converter tap** toward the value that delivers the DC voltage at the specified angle. The tap is clipped to `Tmn`/`Tmx`:

$$
a^{target} = \frac{0.995\cdot\frac{3\sqrt{2}}{\pi}\,n_b\,V_{fs}\,|V_{ac}|\,\cos(\alpha+\mu/2)}{V_{dc}},
$$

In this expression, $n_b$ is the number of bridges (`DCNV.P`).

4. It applies the changes to the bus injections. The damping factor is 1, meaning a plain fixed-point update. The link reports a change when any P or Q value moves by more than 0.1 MW/Mvar, which triggers another outer pass. If every change stays within that tolerance, the update is **rolled back**, so the converter powers remain exactly those used for the final AC solution and the reported state stays consistent.

For links with a base voltage of 10 kV or less, reactive power is kept at its initial value.
