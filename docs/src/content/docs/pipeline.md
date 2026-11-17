---
title: Solution pipeline
description: What happens between reading a .dat file and returning bus voltages.
---

Both solvers follow the same pipeline. Only the numerical core differs. Module paths are relative to the `pf_tool` package.

```text
.dat file
  │  utils/dat_parser.py      parse every "param: BLOCK: … :=" section into rows
  ▼
tables {block → [records]}
  │  utils/utils.py           build buses, branches, CSCs, shunts, SVCs, LCCs, tap controls
  ▼
PowerFlowCase (original buses)
  │  models/network_reduction.py   contract |z| ≤ 2e-4 pu links (exact tap scaling)
  ▼
PowerFlowCase (reduced) + base Ybus
  │  island references, tap reconciliation with the stored voltages
  ▼
Newton-Raphson  ──or──  Optimization / ACOPF (Pyomo + Ipopt)
  │  simultaneous control equations (Newton) or outer control passes
  ▼
reduced voltages ── expand ──► original bus voltages, injections, solved taps
```

## 1. Parsing

`parse_dat_file` reads each block header, such as `param: DBAR: No Name Tb … :=`. It then splits the tokens up to the terminating `;` with shell-style quoting, so names like `"ANGRA1UNE001"` remain single tokens. Records become dictionaries keyed by column name. Repeated blocks are concatenated, and their columns must be identical.

## 2. Case assembly

`build_power_flow_case` turns records into model objects. Only records in service are used:

- **Records filtered out:**
  - records whose operation flag `O` is not `"A"`;
  - records whose state `E` is `"D"`;
  - `DLIN` circuits with either terminal switch `Si` or `Sj` set to `"D"`.
- **Bus types:**
  - `Tb = 0` is PQ;
  - `Tb = 1` is PV;
  - `Tb = 2` is the reference bus;
  - `Tb = 3` is a PQ bus with voltage-limit control.

The component pages explain how each block contributes to the network and injections.

## 3. Network reduction

Very low-impedance links create ill-conditioned Jacobians, so they are contracted. These links include bus-section switches, bypassed series compensators, and ideal ties. Each island receives a reference bus. Transformer taps that contradict the stored operating point are corrected. See [Network reduction](/pf-tool/components/network/).

## 4. Solve

- [Newton-Raphson](/pf-tool/methods/newton-raphson/) solves the mismatch equations together with SVC, LTC, and phase-shifter control equations. By default it also solves generator reactive limits, switched shunts, voltage-limited buses, and LCC converter reactive power in the same Newton system. Its only extra pass rounds switched shunts to whole bank units.
- [Optimization power flow](/pf-tool/methods/optimization/) minimizes bounded residuals in a nonlinear program.
- [AC optimal power flow](/pf-tool/methods/acopf/) enforces AC power balance while minimizing voltage deviation or real losses.

The optimization model, and the Newton-Raphson solver with `control_method="sequential"`, run **outer control passes** for discrete controls instead:

- generator reactive limits;
- switched shunt banks;
- LCC converter set-points;
- LTC activation in Newton;
- SVC operating regions in the optimization model.

The solve repeats until no control changes.

## 5. Results

The reduced solution is expanded back to every original bus as $V_i = s_i\,V_{\text{rep}(i)}$. In this expression, $s_i$ is the complex scale of the contraction. All solved control states are written back to the original devices:

- transformer taps and phase shifts;
- shunt steps;
- SVC outputs;
- LCC set-points;
- bus types.

Reported injections therefore use the final settings. `ExportSolution` writes everything to an Excel workbook; see [Exporting results](/pf-tool/export/).
