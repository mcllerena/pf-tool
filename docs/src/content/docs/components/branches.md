---
title: Lines and transformers
description: π-model branches with off-nominal taps and phase shifts, and how they form Ybus.
---

Source: `models/branch.py`, `models/transformer.py` (`branch_from_dlin`), and `utils/network.py` (`build_ybus`).

## Branch model

Every in-service `DLIN` record is one branch $k$ from bus $f$ (`I`) to bus $t$ (`J`). The model has four parts:

- **Series admittance:** $y_k = 1/(r_k + j x_k)$, where $r_k = R/100$ and $x_k = X/100$ pu.
- **Line charging:** total $b_k = \text{Bshl}/S_{base}$, with $j b_k/2$ at each end.
- **Ideal transformer at the from side:** complex ratio $N_k = a_k e^{j\varphi_k}$.
  - $a_k$ is `Tap`; a value of 0 or blank means 1.
  - $\varphi_k = -\,\text{Psh}\cdot\pi/180$.
- **Tap convention:** the series element sees $V_f / N_k$. The stored ANAREDE voltages confirm this: across fixed-tap, low-impedance transformers, $V_t \approx V_f / a$ in 96 % of cases, compared with $V_f\cdot a$.

The branch stamps used in $Y_{bus}$ are:

$$
\begin{aligned}
Y_{ff} &= \frac{y_k + j b_k/2}{a_k^2}, &
Y_{ft} &= -\frac{y_k}{a_k\,e^{-j\varphi_k}}, \\[2pt]
Y_{tf} &= -\frac{y_k}{a_k\,e^{j\varphi_k}}, &
Y_{tt} &= y_k + j b_k/2 .
\end{aligned}
$$

The branch terminal powers, flowing into the branch, are:

$$
S_f = V_f\,(Y_{ff}V_f + Y_{ft}V_t)^*,\qquad
S_t = V_t\,(Y_{tf}V_f + Y_{tt}V_t)^* .
$$

## Admittance matrix

`build_ybus` assembles the sparse matrix by adding the four stamps of every branch, plus $j\,b^{sh}_i$ on each diagonal for bus and line shunts. Bus injections follow:

$$
S_i^{calc} = V_i\,\big(\textstyle\sum_j Y_{ij} V_j\big)^* ,\qquad
P_i^{calc} = \operatorname{Re} S_i^{calc},\; Q_i^{calc} = \operatorname{Im} S_i^{calc}.
$$

For solving, the branch matrix is **frozen** as a base matrix. Shunt changes and tap or phase changes are added on top as stamp differences:

$$
Y_{bus} = Y^{base} + \operatorname{diag}(j\,b^{sh}) + \sum_{k\in\text{controlled}} \big(\text{stamps}(a_k,\varphi_k) - \text{stamps}(a_k^0,\varphi_k^0)\big).
$$

This makes the Newton step's matrix update cheap on the reduced network. A branch with zero impedance cannot be stamped; it must be contracted first. See [Network reduction](/components/network/).

## Removed circuits

A `DLIN` record is skipped when any of these conditions applies:

- `O ≠ "A"`;
- `E = "D"`;
- `Si = "D"` or `Sj = "D"`;
- either terminal bus is out of service.

## Transformer controls

`DLIN` records with $0 < T_{mn} < T_{mx}$ are on-load tap changers that regulate a bus voltage. `DCTR` records with `PCtrl = P` are phase shifters that regulate an active-power flow. Both are described in [Tap and phase controls](/components/transformer-controls/).
