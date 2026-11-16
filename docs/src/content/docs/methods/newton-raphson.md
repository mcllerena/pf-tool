---
title: Newton-Raphson
description: Sparse polar Newton-Raphson with SVC, LTC, and phase-shifter states, line search, and outer control passes.
---

Source: `pf_tool/newton_raphson.py` (`NewtonRaphsonPowerFlow`).

## Unknowns and equations

At each iteration, the bus types define the index sets:

- $\mathcal{A}$ (angle buses): PV ∪ PQ;
- $\mathcal{Q}$: PQ buses;
- $\mathcal{S}$: active SVCs (device bus is PQ);
- $\mathcal{C}$: regulating tap and phase controls (not limited, not conflicting).

The state vector and the mismatch vector are:

$$
x = \begin{bmatrix} \theta_{\mathcal{A}} \\ |V|_{\mathcal{Q}} \\ q_{\mathcal{S}} \\ s_{\mathcal{C}} \end{bmatrix},
\qquad
F(x) = \begin{bmatrix}
P^{calc}_{\mathcal{A}} - P^{sp}_{\mathcal{A}} \\
Q^{calc}_{\mathcal{Q}} - Q^{sp}_{\mathcal{Q}} - \sum q_s \\
g^{svc}_{\mathcal{S}}(x) \\
g^{ctrl}_{\mathcal{C}}(x)
\end{bmatrix} = 0 .
$$

- $P^{calc}, Q^{calc}$ come from $Y_{bus}$ ([Lines and transformers](/pf-tool/components/branches/)), which includes the current tap shifts.
- $g^{svc}$ is the SVC control law or limit equation for its region ([SVC](/pf-tool/components/svc/)).
- $g^{ctrl}$ is the LTC voltage equation $\kappa_c|V_c| - V_c^{target}$ or the phase-shifter equation $\operatorname{Re}S_{\text{Ext}} - P^{sp}$ ([Tap and phase controls](/pf-tool/components/transformer-controls/)).

The reference bus angle and magnitude are fixed. PV magnitudes are fixed at `DBAR.V`.

## Jacobian

The Jacobian is assembled sparsely in block form:

$$
J = \begin{bmatrix}
J_{P\theta} & J_{PV} & 0 & B_{P s} \\
J_{Q\theta} & J_{QV} & -E & B_{Q s} \\
0 & C_{svc} & D_{svc} & 0 \\
C^{\theta}_{ctrl} & C^{V}_{ctrl} & 0 & D_{ctrl}
\end{bmatrix}
$$

The **network blocks** use the standard complex-derivative form, with $I = Y_{bus}V$ and $\hat V = V/|V|$:

$$
\frac{\partial S}{\partial \theta} = j\,\operatorname{diag}(V)\,\big(\operatorname{diag}(I) - Y_{bus}\operatorname{diag}(V)\big)^*,\qquad
\frac{\partial S}{\partial |V|} = \operatorname{diag}(V)\,\big(Y_{bus}\operatorname{diag}(\hat V)\big)^* + \operatorname{diag}(I)^*\operatorname{diag}(\hat V).
$$

The real parts give the $P$ rows and the imaginary parts give the $Q$ rows.

The remaining blocks are defined as follows:

- **$E$:** places $-1$ in the $Q$ row of each SVC device bus.
- **SVC rows:**
  - regulating mode $P$: $\partial g/\partial|V_c| = \kappa_c$ and $\partial g/\partial q = m$;
  - regulating mode $I$: also $\partial g/\partial |V_d| = -m\,q\,\kappa_d/(\kappa_d|V_d|)^2$, and $\partial g/\partial q = m/(\kappa_d|V_d|)$;
  - saturated: $\partial g/\partial q = 1$ and $\partial g/\partial|V_d| = -2Q_{lim}\kappa_d^2|V_d|$.
- **Tap columns $B$:** for every branch $k$ of a control group, the derivatives are added at its terminals $f, t$. With $X_f = V_f(Y_{ft}V_t)^{\ast}$ and $X_t = V_t(Y_{tf}V_f)^{\ast}$:

$$
\frac{\partial S_f}{\partial a} = -\frac{2|V_f|^2 Y_{ff}^* + X_f}{a},\quad
\frac{\partial S_t}{\partial a} = -\frac{X_t}{a},\quad
\frac{\partial S_f}{\partial \varphi} = -jX_f,\quad
\frac{\partial S_t}{\partial \varphi} = jX_t .
$$

- **Control rows:**
  - LTC: $\kappa_c$ in the $|V_c|$ column, with a zero diagonal;
  - phase shifter: derivatives of $\operatorname{Re}S_{\text{Ext}}$ with respect to the terminal angles and magnitudes, with diagonal $\operatorname{Re}\,\partial S_{\text{Ext}}/\partial\varphi$.

The analytic Jacobian was checked against central finite differences; the maximum error is $4\times10^{-8}$. The linear system $J\,\Delta x = -F$ is solved with SciPy's sparse LU (`spsolve`).

## Step control

The solver applies four safeguards to each Newton step:

1. **Conflict screening:** controls whose step is non-physical are fixed, and the step is recomputed up to five times. The thresholds are $|\Delta s| > 0.2$ for taps and $> 1$ rad for phases.
2. **Control step limit:** if any control asks for more than 0.05 (tap) or 0.1 rad (phase), the whole step is scaled down, preserving the direction.
3. **Backtracking line search:** the step scale is halved up to 16 times. A trial is rejected if any PQ magnitude leaves $[0.4, 2.0]$ pu. It is accepted when either $\lVert F\rVert_\infty$ or $\lVert F\rVert_2$ decreases. As a last resort, a tiny step ($\le 2^{-15}$) is accepted. Each trial re-evaluates SVC regions and rebuilds $Y_{bus}$ for the trial taps.
4. **Control limits:** shifts are clipped to their bounds. A control that keeps pushing against a bound for 3 iterations is fixed there.

The inner solve converges when $\lVert F\rVert_\infty \le \varepsilon = \text{TEPA}/\text{BASE}$. It fails when it reaches the iteration limit `ACIT`, the matrix is singular, or a voltage leaves $[0.4, 2.0]$ pu.

## Outer control passes

```text
reconcile taps → angle warm start
repeat (≤ 50 passes):
    inner Newton solve  (fail → stop)
    store solved SVC outputs
    QLIM  : PV → PQ at violated reactive limit
    VLIM  : Tb = 3 bus → PV at violated voltage limit
    STEP  : switch one shunt unit per out-of-band controlled shunt
    LCC   : recompute converter P/Q/taps from AC voltages
    CTAP  : activate LTC groups whose voltage left Vsp ± TLTC
until nothing changed
```

The result is **converged** only when the final inner solve meets the tolerance and no control changed in the final pass. Typical runs take 11–19 Newton iterations in total over all passes, in about 2.5–3 s, including parsing.
