---
title: Optimization power flow
description: The Pyomo nonlinear program — sets, parameters, variables, expressions, constraints, objective, and solution algorithm.
---

Source: `optimization.py` (`build_optimization_model`, `OptimizationPowerFlow`).

The AC power flow is posed as a **nonlinear program (NLP)**, following the o-grid optimization ACPF paper. Power-balance mismatches are explicit **signed residual variables**. Each residual is bounded by the convergence tolerance at every bus, and their sum is bounded across the system. A feasible point is therefore a converged power flow. The objective drives the residuals toward zero and lightly regularizes the state toward the initial point.

The model is built on the [reduced network](/components/network/), solved with **Ipopt**, and re-solved between discrete control updates.

## Sets

**Network and voltage sets**

| Set                                   | Pyomo  | Definition                                                                |
| ------------------------------------- | ------ | ------------------------------------------------------------------------- |
| $\mathcal{N}$                         | `BUS`  | All buses of the reduced network                                          |
| $\mathcal{N}^P \subseteq \mathcal{N}$ | `PBUS` | Buses with an active-power balance: every bus except reference buses      |
| $\mathcal{N}^Q \subseteq \mathcal{N}$ | `QBUS` | Buses with a reactive-power balance: PQ buses                             |
| $\mathcal{N}^{PV}$                    | —      | PV buses, whose magnitudes are fixed at `DBAR.V`                          |
| $\mathcal{N}^{ref}$                   | —      | Reference buses, one or more per island, with fixed magnitude and angle   |
| $\mathcal{K}_i$                       | —      | Buses $k$ with a nonzero $Y_{ik}$: neighbors of $i$, including $i$ itself |

**Device sets.** Only SVCs are indexed by Pyomo sets and own NLP variables. The other device sets define the **fixed parameters** of each NLP solve.

| Set                                                   | Pyomo | Definition                                                                                                                                   |
| ----------------------------------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| $\mathcal{S}$                                         | `SVC` | Active SVCs (device bus is PQ)                                                                                                               |
| $\mathcal{S}^0, \mathcal{S}^-, \mathcal{S}^+$         | —     | Partition of $\mathcal{S}$ by region: regulating, saturated at $Q_{\min}$, saturated at $Q_{\max}$                                           |
| $\mathcal{S}_i \subseteq \mathcal{S}$                 | —     | SVCs whose device bus is $i$                                                                                                                 |
| $\mathcal{B}$                                         | —     | AC branches: `DLIN` lines and transformers plus standalone `DCSC` circuits not contracted by the reduction                                   |
| $\mathcal{B}^{csc} \subseteq \mathcal{B}$             | —     | Series-compensator circuits (`DCSC`)                                                                                                         |
| $\mathcal{T}$                                         | —     | LTC control groups (`DLIN` with $0<T_{mn}<T_{mx}$, `DOPC CTAP`); each $\tau\in\mathcal T$ owns branches $\mathcal B_\tau\subseteq\mathcal B$ |
| $\mathcal{F}$                                         | —     | Phase-shifter flow controls (`DCTR` with `PCtrl = P`, `DOPC CPHS`), one branch each                                                          |
| $\mathcal{H}$                                         | —     | Shunt banks: bus shunts (`DBSH`, `J = 0`) and line shunts at terminal `Extr`                                                                 |
| $\mathcal{H}_i,\ \mathcal{H}^{sw}\subseteq\mathcal H$ | —     | Shunts at bus $i$; voltage-controlled, switchable shunts (`Ctrl` = `D`/`C`)                                                                  |
| $\mathcal{L}$                                         | —     | HVDC LCC links: in-service `DCLI` lines whose two converters (`DCNV`) and controls (`DCCV`) form a power-controlled rectifier–inverter pair  |
| $\mathcal{L}^R_i,\ \mathcal{L}^I_i$                   | —     | Links whose rectifier or inverter AC bus (`DCNV.Bac`) is $i$                                                                                 |
| $\mathcal{G}^{Q}$                                     | —     | PV buses with reactive limits `Qgn`/`Qgm`, checked between solves (`DOPC QLIM`)                                                              |

## Parameters

**Network.** $Y_{bus}$ is assembled from every branch and shunt; see [Admittance and injection parameters](#admittance-and-injection-parameters).

| Symbol             | Meaning                                                        | Source                                              |
| ------------------ | -------------------------------------------------------------- | --------------------------------------------------- |
| $G_{ik} + jB_{ik}$ | Entries of $Y_{bus}$                                           | `build_ybus(case)`                                  |
| $r_k, x_k, b_k$    | Branch series resistance, reactance, and total charging (pu)   | `DLIN` `R`, `X`, `Bshl`; `DCSC` `Xv`                |
| $a_k, \varphi_k$   | Tap ratio and phase shift of branch $k$, on the from side      | `DLIN` `Tap`, `Psh`; LTC and phase-shifter solution |
| $b^{sh}_i$         | Total shunt susceptance at bus $i$ (pu at 1 pu voltage)        | `DBAR.Sh`, `DBSH`, `DBSH_BANK`                      |
| $\sigma_i$         | Complex voltage scale of an original bus inside a reduced node | Network reduction                                   |

**Injections**

| Symbol               | Meaning                                                                                                                        | Source         |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------- |
| $P^g_i, Q^g_i$       | Scheduled generation (MW, Mvar); $Q^g_i$ includes the initial SVC output and, for PV buses switched by `QLIM`, the limit value | `DBAR`, `DCER` |
| $P^l_i, Q^l_i$       | Constant-power load (MW, Mvar)                                                                                                 | `DBAR`         |
| $P_i^{sp}, Q_i^{sp}$ | Specified net injections (pu), defined [below](#admittance-and-injection-parameters)                                           | Derived        |
| $q^0_s$              | Initial SVC output, removed from $Q^{sp}$ because $q_s$ is a variable                                                          | `DCER.Qg`      |

**SVCs**

| Symbol                          | Meaning                                                                                                                                                                                                                         | Source                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| $V_s^{ref}, m_s$                | Voltage reference (`DBAR.V` of the controlled bus) and droop slope `Incl/100`                                                                                                                                                   | `DCER`, `DBAR`        |
| $Q_{\min,s}, Q_{\max,s}$        | Capability at 1 pu                                                                                                                                                                                                              | `DCER` `Qgn`, `Qgm`   |
| mode$_s$                        | `P` (reactive-power droop) or `I` (current droop)                                                                                                                                                                               | `DCER.Ctrl`           |
| $c_s, d_s$                      | Controlled bus and device bus                                                                                                                                                                                                   | `DCER` `Bctrl`, `Bus` |
| $\kappa^c_s, \kappa^d_s$        | Voltage scales of the controlled and device buses                                                                                                                                                                               | Network reduction     |
| $\underline q_s, \overline q_s$ | Static output bounds: $\underline q_s = \min(Q_{\min,s}(\kappa^d_s\underline v)^2,\, Q_{\min,s}(\kappa^d_s\overline v)^2)$, $\overline q_s = \max(Q_{\max,s}(\kappa^d_s\underline v)^2,\, Q_{\max,s}(\kappa^d_s\overline v)^2)$ | Derived               |

**HVDC LCC links.** These are fixed in each solve and recomputed between solves. For each link $\ell\in\mathcal L$:

| Symbol                                                         | Meaning                                                                 | Source                            |
| -------------------------------------------------------------- | ----------------------------------------------------------------------- | --------------------------------- |
| $\rho_\ell, \iota_\ell$                                        | Rectifier and inverter AC buses                                         | `DCNV.Bac`, `DCNV.Modo`           |
| $P^{dc}_\ell$                                                  | Power order (MW) of the power-controlled converter                      | `DCCV.Vsp` with `TCnv = P`        |
| $\text{slack}_\ell$                                            | Converter that absorbs the DC losses (`F = "F"`)                        | `DCCV.F`                          |
| $V^{dc,R}_\ell, V^{dc,I}_\ell$                                 | DC voltages at the rectifier and inverter (kV)                          | `DCBA.V`                          |
| $V^{base}_\ell$                                                | Link base voltage (kV)                                                  | `DELO.V`                          |
| $R_\ell$                                                       | DC line resistance (Ω)                                                  | `DCLI.R`                          |
| $\alpha_\ell, \gamma_\ell$                                     | Rectifier firing angle and inverter extinction angle (degrees)          | `DCCV.Ang`                        |
| $X^{c}_{\ell,\cdot}$                                           | Commutation reactance (% on $S^{nt}$)                                   | `DCNV.Xc`                         |
| $V^{fs}_{\ell,\cdot}, S^{nt}_{\ell,\cdot}, n^{b}_{\ell,\cdot}$ | Converter-transformer secondary voltage (kV), rating (MVA), and bridges | `DCNV` `Vfs`, `Snt`, `P`          |
| $a^{R}_\ell, a^{I}_\ell$                                       | Converter-transformer taps, limited to `Tmn`–`Tmx`                      | `DCCV` `Ttr`, `Tmn`, `Tmx`        |
| $\mu^R_\ell, \mu^I_\ell$                                       | Commutation overlap angles                                              | Derived from the last AC solution |
| $P^R_\ell, Q^R_\ell, P^I_\ell, Q^I_\ell$                       | Converter AC-side powers (MW, Mvar)                                     | Derived, see below                |

**Algorithm constants**

| Symbol                      | Meaning                                                                                        | Value           |
| --------------------------- | ---------------------------------------------------------------------------------------------- | --------------- |
| $\varepsilon$               | Convergence tolerance, `TEPA/BASE`                                                             | $10^{-4}$ pu    |
| $v_i^0, \theta_i^0$         | Initial magnitude and angle: Newton solution (warm start) or stored `DBAR.V`/`A0` (cold start) | —               |
| $\underline v, \overline v$ | Numerical magnitude bounds                                                                     | $0.4$, $2.0$ pu |
| $w$                         | Regularization weight                                                                          | $10^{-3}$       |

### Admittance and injection parameters

**Admittance matrix.** Every branch $k\in\mathcal B$, from $f$ to $t$ with $y_k = 1/(r_k+jx_k)$, contributes its π-model stamps. A CSC circuit has $r_k=0$, $x_k=X_v/100$, and $b_k=0$. A bypassed CSC is contracted by the reduction. Shunts add to the diagonal:

$$
Y_{bus} \;=\; \sum_{k\in\mathcal B}
\begin{bmatrix}
\dfrac{y_k + j b_k/2}{a_k^2} & -\dfrac{y_k}{a_k e^{-j\varphi_k}}\\[8pt]
-\dfrac{y_k}{a_k e^{j\varphi_k}} & y_k + j b_k/2
\end{bmatrix}_{\{f,t\}}
\;+\; \operatorname{diag}\!\big(j\,b^{sh}\big),
\qquad
b^{sh}_i = \sum_{n\in i}|\sigma_n|^2\Big(\frac{\text{Sh}_n}{S_{base}} + \sum_{h\in\mathcal H_n}\frac{Q_h}{S_{base}}\Big).
$$

- $Q_h = \sum_g \text{Sht}_g\cdot\text{UOp}_g$ is the injection of the bank units in service.
- $n\in i$ ranges over the original buses merged into reduced node $i$.
- On the reduced network, each stamp is transformed with the terminal scales $\sigma_f, \sigma_t$, as $T^H Y T$.

**HVDC link powers.** For each link $\ell\in\mathcal L$:

$$
I^{dc}_\ell = \frac{P^{dc}_\ell}{V^{dc}_{\ell,P}},\qquad
P^{loss}_\ell = (I^{dc}_\ell)^2 R_\ell,
$$

where $V^{dc}_{\ell,P}$ is the DC voltage of the power-controlled converter.

$$
(P^R_\ell,\,P^I_\ell) =
\begin{cases}
(P^{dc}_\ell + P^{loss}_\ell,\; P^{dc}_\ell) & \text{slack at the rectifier}\\
(P^{dc}_\ell,\; P^{dc}_\ell - P^{loss}_\ell) & \text{slack at the inverter}
\end{cases}
$$

$$
\cos(\alpha_\ell + \mu^R_\ell) = \cos\alpha_\ell - \frac{\sqrt2\,X^{\Omega}_{\ell,R}\,I^{dc}_\ell}{a^R_\ell\,V^{fs}_{\ell,R}\,|V_{\rho_\ell}|},\qquad
X^{\Omega} = \frac{X^c}{100}\,\frac{(V^{fs})^2}{S^{nt}},
$$

The inverter uses the same overlap formula with $\gamma_\ell$, $a^I_\ell$, and $|V_{\iota_\ell}|$:

$$
Q^R_\ell = \tilde P^R_\ell\,\tan\!\big(\alpha_\ell + \tfrac{\mu^R_\ell}{2}\big),\qquad
Q^I_\ell = P^{dc}_\ell\,\tan\!\big(\gamma_\ell + \tfrac{\mu^I_\ell}{2}\big),
$$

- $\tilde P^R_\ell = P^{dc}_\ell + P^{loss}_\ell$ when $200 \le V^{base}_\ell < 750$ kV, and $P^{dc}_\ell$ otherwise.
- $|V_{\rho_\ell}|$ and $|V_{\iota_\ell}|$ are the AC terminal voltages of the **previous** solution, or the initial point for the first solve.
- For links with $V^{base}_\ell \le 10$ kV, $Q^R_\ell$ and $Q^I_\ell$ keep their initial values.

**Specified injections**

$$
P_i^{sp} = \frac{1}{S_{base}}\Big[P^g_i - P^l_i
+ \sum_{\ell\in\mathcal L^I_i} P^I_\ell - \sum_{\ell\in\mathcal L^R_i} P^R_\ell\Big],
$$

$$
Q_i^{sp} = \frac{1}{S_{base}}\Big[Q^g_i - Q^l_i
- \sum_{\ell\in\mathcal L^R_i} Q^R_\ell - \sum_{\ell\in\mathcal L^I_i} Q^I_\ell\Big]
- \sum_{s\in\mathcal S_i} q^0_s .
$$

- The rectifier is an active and reactive **load**.
- The inverter is an active **source** and a reactive **load**.
- Both converters consume reactive power.

### What is fixed in each NLP and how it is updated

During an Ipopt solve, the only free quantities are $v$, $\theta$, the residuals, and the SVC outputs. All other controls are fixed parameters, updated **between** NLP solves:

| Component                                    | Fixed in the NLP as      | Set before the first solve                                                                                                                                                  | Updated between solves                                                        |
| -------------------------------------------- | ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Lines, transformer impedances, CSC reactance | $Y_{bus}$ entries        | Data (`DLIN`, `DCSC`)                                                                                                                                                       | Never                                                                         |
| LTC taps $a_k$, $k\in\mathcal B_\tau$        | $Y_{bus}$ entries        | Warm start: Newton-regulated taps (`CTAP`). Cold start: stored taps after [reconciliation](/components/transformer-controls/#reconciling-settings-with-the-stored-voltages) | Not updated                                                                   |
| Phase shifts $\varphi_k$, $k\in\mathcal F$   | $Y_{bus}$ entries        | Warm start: Newton-regulated angles (`CPHS`). Cold start: stored `Psh` after reconciliation                                                                                 | Not updated                                                                   |
| Switched shunts $h\in\mathcal H^{sw}$        | $b^{sh}$                 | Data or Newton switching                                                                                                                                                    | One unit per out-of-band shunt, with anti-hunting (`STEP`)                    |
| Generator reactive limits, $\mathcal G^Q$    | Bus type and $Q^g$       | Data or Newton switching                                                                                                                                                    | PV → PQ at the violated limit (`QLIM`)                                        |
| HVDC LCC powers                              | $P^{sp}$, $Q^{sp}$       | Data or Newton solution                                                                                                                                                     | $\mu$, $Q$, converter taps, and losses recomputed from the solved AC voltages |
| SVC regions                                  | Choice of SVC constraint | Control law at the initial point                                                                                                                                            | Saturate or release, with no jump across limits                               |

The NLP solves repeat until no update changes anything. Converged LCC set-points are therefore consistent with the AC voltages of the final NLP.

## Variables

The NLP has variables only for the network state, the balance residuals, and the SVCs. HVDC converter powers, transformer taps and phase shifts, CSC reactances, and shunt steps are parameters within a solve; see [the table above](#what-is-fixed-in-each-nlp-and-how-it-is-updated).

| Variable                     | Pyomo          | Domain                                    | Notes                                                            |
| ---------------------------- | -------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| $v_i$, $i\in\mathcal N$      | `vm`           | $[\underline v, \overline v]$             | Fixed at $v_i^0$ for $i \in \mathcal N^{PV}\cup\mathcal N^{ref}$ |
| $\theta_i$, $i\in\mathcal N$ | `va`           | $[-\pi, \pi]$                             | Fixed at $\theta_i^0$ for $i\in\mathcal N^{ref}$                 |
| $r^P_i$, $i\in\mathcal N^P$  | `p_residual`   | $[-\varepsilon, \varepsilon]$             | Signed active-power residual                                     |
| $r^Q_i$, $i\in\mathcal N^Q$  | `q_residual`   | $[-\varepsilon, \varepsilon]$             | Signed reactive-power residual                                   |
| $q_s$, $s\in\mathcal S$      | `qsvc`         | $[\underline q_s, \overline q_s]$         | SVC reactive output (pu)                                         |
| $r^c_s$, $s\in\mathcal S$    | `svc_residual` | free; fixed at 0 for $s\in\mathcal S^\pm$ | SVC control-law residual                                         |

The variables are initialized as follows:

- $v$ and $\theta$ start from the initial point.
- The residual variables start from the residuals at that point, clipped to $[-\varepsilon,\varepsilon]$.
- $q_s$ starts from the present SVC output, clamped to its region.

## Expressions

The calculated bus injections in polar form are `p_calculated` and `q_calculated`, with $\theta_{ik} = \theta_i - \theta_k$:

$$
P_i(v,\theta) = v_i \sum_{k\in\mathcal K_i} v_k\big(G_{ik}\cos\theta_{ik} + B_{ik}\sin\theta_{ik}\big),
$$

$$
Q_i(v,\theta) = v_i \sum_{k\in\mathcal K_i} v_k\big(G_{ik}\sin\theta_{ik} - B_{ik}\cos\theta_{ik}\big).
$$

The SVC device and controlled voltages are $V^d_s = \kappa^d_s v_{d_s}$ and $V^c_s = \kappa^c_s v_{c_s}$.

## Constraints

**Active-power balance** (`p_balance`):

$$
P_i(v,\theta) - P_i^{sp} = r^P_i \qquad \forall i\in\mathcal N^P
$$

**Reactive-power balance** (`q_balance`):

$$
Q_i(v,\theta) - Q_i^{sp} - \sum_{s\in\mathcal S_i} q_s = r^Q_i \qquad \forall i\in\mathcal N^Q
$$

**Aggregate residual bounds** (`aggregate_p`, `aggregate_q`) keep many small residuals from adding up to a large system-wide imbalance:

$$
-\varepsilon \le \sum_{i\in\mathcal N^P} r^P_i \le \varepsilon,\qquad
-\varepsilon \le \sum_{i\in\mathcal N^Q} r^Q_i \le \varepsilon
$$

**SVC control law**, regulating region (`svc_control`, $s\in\mathcal S^0$):

$$
V^c_s - V^{ref}_s + m_s\,q_s = r^c_s \quad(\text{mode } P),\qquad
V^c_s - V^{ref}_s + m_s\,\frac{q_s}{V^d_s} = r^c_s \quad(\text{mode } I)
$$

**SVC capability**, saturated regions (`svc_control`, $s\in\mathcal S^\pm$):

$$
q_s = Q_{\min,s}\,(V^d_s)^2 \quad (s\in\mathcal S^-),\qquad
q_s = Q_{\max,s}\,(V^d_s)^2 \quad (s\in\mathcal S^+)
$$

The bounds on $r^P$, $r^Q$, $v$, $\theta$, and $q$ are variable bounds. The SVC residual $r^c$ is deliberately **unbounded**. An SVC that cannot meet its law within its output range then keeps the NLP feasible, and the next region update saturates it.

## Objective function

The objective minimizes the residuals normalized by the tolerance, plus a small regularization toward the initial state:

$$
\min\;
\sum_{i\in\mathcal N^P}\Big(\frac{r^P_i}{\varepsilon}\Big)^2
+ \sum_{i\in\mathcal N^Q}\Big(\frac{r^Q_i}{\varepsilon}\Big)^2
+ \sum_{s\in\mathcal S}\Big(\frac{r^c_s}{\varepsilon}\Big)^2
+ w\sum_{i\in\mathcal N}\Big[(v_i - v_i^0)^2 + (\theta_i - \theta_i^0)^2\Big]
$$

Dividing by $\varepsilon$ puts residuals at the tolerance on the order of 1. With the weight $w = 10^{-3}$, the regularization only selects among near-solutions and does not trade against the balance. Scheduled active generation is fixed, so there are no redispatch terms.

## Solver settings (Ipopt)

| Option                       | Value                 | Why                                                                                                           |
| ---------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------- |
| `tol` / `acceptable_tol`     | $10^{-8}$ / $10^{-6}$ | Overall NLP convergence                                                                                       |
| `constr_viol_tol`            | $10^{-9}$             | Balance equations must hold far below $\varepsilon$; Ipopt's default of $10^{-4}$ equals the tolerance itself |
| `acceptable_constr_viol_tol` | $10^{-8}$             | Same reason                                                                                                   |
| `mu_strategy`                | `adaptive`            | Robust barrier update                                                                                         |
| `max_iter`                   | `max(200, 2·ACIT)`    | Per NLP solve                                                                                                 |
| `max_cpu_time`               | 900 s                 | Per NLP solve                                                                                                 |

## Solution algorithm

```text
reduce network, freeze branch matrix, assign island references
reconcile transformer settings with the stored voltages
if warm_start:   run Newton-Raphson (taps, PST angles, Q limits, shunts, SVCs, LCC)
                 and take its voltages and control state as the initial point
initial SVC regions from the control law at the initial point
repeat (≤ max_control_passes + 1 NLP solves):
    build NLP with the current regions and case;  solve with Ipopt
    update SVC regions (saturate / release, never jump across)
    store solved SVC outputs in the case
    QLIM : PV → PQ at a violated reactive limit
    STEP : switch one unit per out-of-band controlled shunt
    LCC  : recompute converter P/Q/taps from the solved voltages
    seed the next NLP with the solved voltages
until nothing changed
```

## Convergence test

After the last NLP solve, `solution_metrics` **recomputes** every residual from $Y_{bus}$ and the solved voltages, instead of reading the residual variables. The run is reported as converged only if all of these hold:

1. Ipopt terminated `optimal`, `locallyOptimal`, or `feasible`.
2. $\max_i |P_i - P_i^{sp}| \le \varepsilon$ over $\mathcal N^P$, and $\max_i |Q_i - Q_i^{sp} - \sum q_s| \le \varepsilon$ over $\mathcal N^Q$.
3. The aggregate P and Q mismatches are at most $\varepsilon$.
4. Every SVC equation for its region holds within $\varepsilon$.
5. The last control pass produced no pending changes.

Voltage magnitudes are bounded only numerically. Operating limits from `DGLT` are a separate acceptability check, not part of convergence.

## Warm and cold start

|                          | Warm start (default)                   | Cold start (`warm_start=False`)       |
| ------------------------ | -------------------------------------- | ------------------------------------- |
| Initial point            | Newton solution                        | Stored `DBAR` voltages                |
| Transformer settings     | Newton-regulated taps and phase angles | Stored settings, after reconciliation |
| Typical NLP solves       | 1                                      | 5–6                                   |
| Typical Ipopt iterations | 7–8                                    | 39–49                                 |
| Typical time             | ~10 s                                  | ~35–44 s                              |

The cold-start NLP does not include LTC voltage regulation. Its solutions are therefore slightly farther from ANAREDE: the 99th-percentile voltage difference is up to 0.016 pu instead of about 0.010 pu.
