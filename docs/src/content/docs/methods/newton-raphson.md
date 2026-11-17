---
title: Newton-Raphson
description: Sparse polar Newton-Raphson with SVC, LTC, phase-shifter, generator-limit, switched-shunt, and LCC equations solved simultaneously with the network.
---

Source: `pf_tool/newton_raphson.py` (`NewtonRaphsonPowerFlow`).

The solver has two **control methods**, selected with `control_method`:

- `"simultaneous"` (default): every control is an equation of the same Newton system as the network, as described under [Simultaneous control method](#simultaneous-control-method).
- `"sequential"`: generator limits, switched shunts, LCC set-points, and LTC activation are updated between full Newton solves, as described under [Sequential control method](#sequential-control-method).

Both share the network equations, the SVC and tap/phase-shift states, and the step control below.

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

The analytic Jacobian was checked against central finite differences; the maximum error is $4\times10^{-8}$. It is assembled and stored as a sparse CSC matrix, so zero entries are not stored. Each Newton step solves $J\,\Delta x=-F$ with a sparse direct solver.

The `sparsity` option controls the fill-reducing ordering used by SuperLU:

```python
solver = NewtonRaphsonPowerFlow(
    "CASE-FLOW.dat",
    sparsity=True,
    lu_decomposition=True,
)
result = solver.solve()
```

With `sparsity=True`, the solver requests SuperLU's `MMD_AT_PLUS_A` minimum-degree ordering to reduce fill during factorization. This is a library-backed analogue of the PDF's ordering strategy, not a custom implementation of its exact OOS2 algorithm. The default `sparsity=False` retains SuperLU's `COLAMD` ordering; matrices are still stored and solved sparsely either way.

`sparsity` and `lu_decomposition` are independent and can be combined either way. `lu_decomposition=False` (default) uses SciPy's `spsolve`; `True` uses an explicit `splu` factorization and solve. In both cases the selected ordering is applied, and the Newton equations and iteration strategy are unchanged. The LU option follows the notes' factor-and-substitute workflow without forming $J^{-1}$ or coding a dense Crout/Doolittle algorithm. Since the Jacobian values change at each operating point, Newton refactors it for each step. `DCPowerFlow` factors its reduced B-prime matrix once per solve, while the enhanced fast-decoupled method reuses its constant B-prime/B-double-prime factors until the network structure changes.

## Step control

The solver applies four safeguards to each Newton step:

1. **Conflict screening:** controls whose step is non-physical are fixed, and the step is recomputed up to five times. The thresholds are $|\Delta s| > 0.2$ for taps and $> 1$ rad for phases. The simultaneous method applies this only to phase shifters.
2. **Control step limit:** if any control asks for more than 0.05 (tap) or 0.1 rad (phase), the whole step is scaled down, preserving the direction.
3. **Backtracking line search:** the step scale is halved up to 16 times. A trial is rejected if any PQ magnitude leaves $[0.4, 2.0]$ pu. It is accepted when either $\lVert F\rVert_\infty$ or $\lVert F\rVert_2$ decreases. As a last resort, a tiny step ($\le 2^{-15}$) is accepted. Each trial re-evaluates SVC regions and rebuilds $Y_{bus}$ for the trial taps.
4. **Control limits:** shifts are clipped to their bounds. A control that keeps pushing against a bound for 3 iterations is fixed there. In the simultaneous method, LTC limits are part of their complementarity law instead.

The inner solve converges when $\lVert F\rVert_\infty \le \varepsilon = \text{TEPA}/\text{BASE}$. It fails when it reaches the iteration limit `ACIT`, the matrix is singular, or a voltage leaves $[0.4, 2.0]$ pu.

## Simultaneous control method

The default method adds the remaining controls to the unknowns and equations above, so a single Newton system solves the network and all controls together. Bounds are written as **complementarity conditions** with the median function: for an unknown $x \in [\ell, u]$ and a control error $F$,

$$
\operatorname{mid}(x-\ell,\; x-u,\; F) = 0
\iff
\begin{cases}
F = 0, & \ell < x < u,\\
F \le 0, & x = u,\\
F \ge 0, & x = \ell .
\end{cases}
$$

The median is piecewise linear, so the system is solved by **semismooth Newton**: each Jacobian row is the gradient of the piece that is currently the median. A device can therefore reach a limit and leave it again within the iterations, and no outer loop is needed to detect it.

### Added unknowns and equations

| Device                                    | Unknown                         | Equation                                                                                              | Injection                                     |
| ----------------------------------------- | ------------------------------- | ----------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| Generator with reactive limits (`QLIM`)   | $\lvert V_g\rvert$ and $q_g$    | $\operatorname{mid}(q_g-q^{min}_g,\; q_g-q^{max}_g,\; \lvert V_g\rvert - V^{sp}_g) = 0$               | $+q_g$ in the $Q$ row of bus $g$              |
| Voltage-limited load bus, Tb = 3 (`VLIM`) | $q_i$                           | $\operatorname{mid}(\lvert V_i\rvert-V^{min}_i,\; \lvert V_i\rvert-V^{max}_i,\; q_i) = 0$             | $+q_i$ in the $Q$ row of bus $i$              |
| Switched shunt (`STEP`)                   | susceptance change $b$          | $\operatorname{mid}(b-b^{min},\; b-b^{max},\; d(\kappa\lvert V_c\rvert) + k_b\,b) = 0$                | $+b\,(\kappa_b\lvert V_k\rvert)^2$ at its bus |
| LTC group (`CTAP`)                        | tap shift $s$ (already a state) | $\operatorname{mid}(s-s^{min},\; s-s^{max},\; \sigma\, d(\kappa\lvert V_c\rvert) + k_t\,(s-s_0)) = 0$ | through $Y_{bus}$                             |
| LCC converter                             | none                            | —                                                                                                     | $-Q_{conv}(\lvert V\rvert)$ at each terminal  |

- A generator inside its limits holds $\lvert V_g\rvert = V^{sp}_g$, like a PV bus. At a limit it injects exactly $q^{max}_g$ or $q^{min}_g$, and its voltage is free, like a PQ bus.
- $d(v)$ is the distance of the controlled voltage outside its band, which is zero inside the band. The band is `Vmn`–`Vmx` for shunts and `Vsp ± TLTC` for LTCs. Inside the band the device holds its starting setting; outside it, the device moves until the voltage returns to the band edge.
- The slopes $k_t = 10^{-3}$ and $k_b = 10^{-4}$ pu offset the regulated voltage from the band edge by at most about $10^{-4}$ pu. They also keep the Jacobian nonsingular when several devices regulate the same bus, which replaces the conflict screening that the sequential method needs for LTCs.
- $\sigma = \operatorname{sign}(\partial \lvert V_c\rvert/\partial s)$ orients each LTC law so that it always opposes the voltage error. It is computed once per solve from the network Jacobian, $\partial x/\partial s = -J^{-1}\,\partial F/\partial s$.
- $Q_{conv}$ uses the same relations as the sequential LCC update: the scheduled firing or extinction angle, the converter tap that holds the DC voltage, and the commutation overlap. These are iterated to their fixed point, so the converter reactive power is a function of its AC terminal voltage. Its derivative is added to the diagonal of the $Q$–$\lvert V\rvert$ block. As in [PowerFlows.jl](https://github.com/Sienna-Platform/PowerFlows.jl/blob/main/docs/src/explanation/lcc_model.md), the converter state is solved inside the AC Newton iteration, not between solves.

The line search is the same as above, with one difference: generator, shunt, and LTC unknowns are **not clipped** to their bounds during the search. Clipping would bend the Newton direction at a limit; the median equations bring the unknowns back inside their bounds at convergence.

### Passes

```text
reconcile taps → angle warm start → LTC orientation σ
pass 1: Newton solve with shunts as continuous susceptances
round every moved shunt to the nearest whole number of bank units
pass 2: Newton solve with the shunts fixed (generators, VLIM, LTCs, LCC stay in the system)
write back: generators at a limit become PQ at that limit, VLIM buses at a limit become PV,
            LTC regulating/limited status, LCC reactive power, taps, and overlap
```

When no shunt moves in pass 1, the solve ends after one pass.

### Comparison with the sequential method

Both methods converge on all 11 cases. Measured in one run on the same machine:

| Method                 | Newton iterations | Mean time per case | p99 dV from ANAREDE (pu) |
| ---------------------- | ----------------: | -----------------: | -----------------------: |
| Simultaneous (default) |              5–13 |              5.0 s |            0.0048–0.0100 |
| Sequential             |             10–19 |              3.7 s |            0.0050–0.0101 |

The simultaneous method needs fewer iterations on every case. Its p99 voltage difference from the stored ANAREDE solution is smaller on 10 cases and equal on CASO05. Each iteration costs more, because the Jacobian is larger and the LCC terms are evaluated numerically. See [Results and benchmarks](/pf-tool/methods/results/) for the per-case tables.

## Sequential control method

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

The result is **converged** only when the final inner solve meets the tolerance and no control changed in the final pass. A generator switched to PQ is not switched back. Typical runs take 10–19 Newton iterations in total over all passes.
