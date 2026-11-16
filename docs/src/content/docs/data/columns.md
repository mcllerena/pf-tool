---
title: Block columns
description: Column-by-column meaning of the data blocks read by the solvers.
---

This page uses three conventions:

- Percent impedances are on the system base `BASE` (100 MVA).
- Mvar values for shunts and SVCs are given at 1 pu voltage.
- The **Use** column states how the code uses each field. A dash (—) means the field is read but not used.

## `DBAR` — buses

| Column       | Meaning                                                          | Use                                                             |
| ------------ | ---------------------------------------------------------------- | --------------------------------------------------------------- |
| `No`         | Bus number                                                       | Bus identifier                                                  |
| `Name`       | Bus name                                                         | Reporting                                                       |
| `Tb`         | Bus type: 0 PQ, 1 PV, 2 reference (Vθ), 3 PQ with voltage limits | Bus type                                                        |
| `Are`        | Area number                                                      | —                                                               |
| `V`          | Voltage (pu): solved value or set-point                          | Initial voltage; PV/reference set-point; SVC and LTC references |
| `A0`         | Angle (degrees)                                                  | Initial angle and reference angle                               |
| `Pg`, `Qg`   | Active (MW) and reactive (Mvar) generation                       | Scheduled injection                                             |
| `Qgn`, `Qgm` | Minimum and maximum reactive generation (Mvar)                   | PV reactive limits (`QLIM`)                                     |
| `Bctrl`      | Remotely controlled bus                                          | — (PV buses hold their own `V`)                                 |
| `Pl`, `Ql`   | Active and reactive load (MW, Mvar)                              | Constant-power load                                             |
| `Sh`         | Bus shunt (Mvar at 1 pu; positive is capacitive)                 | Shunt susceptance `Sh/BASE`                                     |
| `Gb`         | Base-voltage group (→ `DGBT`)                                    | —                                                               |
| `Gl`         | Voltage-limit group (→ `DGLT`)                                   | `Vmin`/`Vmax`                                                   |
| `Vf`         | Load-definition voltage                                          | — (constant-power loads)                                        |
| `M`          | Display mode                                                     | —                                                               |
| `E`, `O`     | State and operation                                              | Bus kept only if `O = "A"` and `E ≠ "D"`                        |

## `DLIN` — lines and transformers

| Column         | Meaning                                                                  | Use                                               |
| -------------- | ------------------------------------------------------------------------ | ------------------------------------------------- |
| `I`, `J`, `Nc` | From bus, to bus, and circuit number                                     | Branch key                                        |
| `Si`, `Sj`     | Terminal switch state at `I` / `J` (`L` on, `D` off)                     | Branch removed if either is `D`                   |
| `O`, `E`       | Operation and state                                                      | Branch removed unless `O = "A"` and `E ≠ "D"`     |
| `Prop`         | Owner flag                                                               | —                                                 |
| `R`, `X`       | Series resistance and reactance (%)                                      | $r = R/100$, $x = X/100$ pu                       |
| `Bshl`         | Total line charging (Mvar at 1 pu)                                       | $b = \text{Bshl}/\text{BASE}$, split half per end |
| `Tap`          | Off-nominal tap at the `I` side; 0 or blank means 1.0                    | Tap ratio $a$                                     |
| `Tmn`, `Tmx`   | Tap limits                                                               | LTC when $0 < T_{mn} < T_{mx}$                    |
| `Psh`          | Phase shift (degrees)                                                    | Phase $\varphi = -\text{Psh}$ (rad)               |
| `Bctrl`        | LTC-controlled bus: `From Bus`, `To Bus`, or a bus number (sign ignored) | LTC target bus                                    |
| `Cn`, `Ce`     | Normal and emergency rating (MVA)                                        | Stored rating                                     |
| `Ns`           | Number of tap steps                                                      | — (taps are continuous)                           |
| `Cq`           | Additional rating                                                        | —                                                 |

## `DCSC` — controllable series compensators

| Column         | Meaning                                                      | Use                                 |
| -------------- | ------------------------------------------------------------ | ----------------------------------- |
| `I`, `J`, `Nc` | Terminals and circuit                                        | Branch key (a standalone circuit)   |
| `O`, `E`       | Operation and state                                          | In-service filter                   |
| `Prop`         | Owner flag                                                   | —                                   |
| `Byp`          | Bypass breaker: `L` closed (bypassed), `D` open              | `L` means a short circuit           |
| `Xmin`, `Xmax` | Reactance limits (%)                                         | Fallback reactance if `Xv` is blank |
| `Xv`           | Present reactance (%), negative for capacitive               | Series reactance                    |
| `Ctrl`         | Control mode: `X` constant reactance, `P` power, `I` current | Treated as constant `Xv`            |
| `Vsp`          | Control set-point                                            | —                                   |
| `Ext`          | Measuring terminal                                           | —                                   |
| `Nst`          | Number of stages                                             | —                                   |
| `Cdcsc`        | Rating (MVA)                                                 | Stored rating                       |

## `DBSH` / `DBSH_BANK` — shunt banks

| Column             | Meaning                                                              | Use                         |
| ------------------ | -------------------------------------------------------------------- | --------------------------- |
| `I`                | Bus                                                                  | Bus shunt location          |
| `J`, `Nc`          | 0 for a bus shunt; otherwise the line `I–J–Nc` carrying a line shunt | Line shunt circuit          |
| `Extr`             | Line terminal where a line shunt is connected                        | Line shunt bus              |
| `O`                | Operation                                                            | In-service filter           |
| `Ctrl`             | `F` fixed, `D` discrete, `C` continuous                              | Fixed or voltage-controlled |
| `Vmn`, `Vmx`       | Voltage band of the controlled bus (pu)                              | Switching band              |
| `Bctrl`            | Controlled bus                                                       | Control target              |
| `Qini`             | Initial injection (Mvar) when no bank groups exist                   | Single-stage shunt          |
| `TCtrl`            | Control type                                                         | —                           |
| `Clr`              | `S` clears the `DBAR` `Sh` value of the bus                          | Applied to bus shunts       |
| `DBSH_BANK.Parent` | Index of the owning `DBSH` record, counting from 1                   | Group ownership             |
| `DBSH_BANK.G`      | Group number                                                         | —                           |
| `DBSH_BANK.O`, `E` | Operation and state                                                  | In-service filter           |
| `DBSH_BANK.U`      | Units installed                                                      | Maximum units               |
| `DBSH_BANK.UOp`    | Units in operation                                                   | Initial units               |
| `DBSH_BANK.Sht`    | Mvar per unit; positive is capacitor, negative is reactor            | Unit size                   |

## `DCER` — static VAR compensators

| Column       | Meaning                                        | Use                    |
| ------------ | ---------------------------------------------- | ---------------------- |
| `Bus`        | SVC terminal bus                               | Device bus             |
| `O`, `E`     | Operation and state                            | In-service filter      |
| `G`, `Un`    | Group and number of units                      | —                      |
| `Bctrl`      | Controlled bus                                 | Control target         |
| `Incl`       | Droop slope (%)                                | $m = \text{Incl}/100$  |
| `Qg`         | Present output (Mvar)                          | Initial $q$            |
| `Qgn`, `Qgm` | Inductive and capacitive limits at 1 pu (Mvar) | $Q_{\min}$, $Q_{\max}$ |
| `Ctrl`       | `P` reactive-power droop, `I` current droop    | Control law            |

## `DCTR` — transformer controls

| Column                | Meaning                                        | Use                  |
| --------------------- | ---------------------------------------------- | -------------------- |
| `I`, `J`, `Nc`        | Transformer circuit (must exist in `DLIN`)     | Branch key           |
| `O`                   | Operation                                      | In-service filter    |
| `Vmn`, `Vmx`, `VCtrl` | Voltage-band control data                      | —                    |
| `M`                   | Control mode                                   | —                    |
| `Fmin`, `Fmax`        | Phase-angle limits (degrees, `Psh` convention) | Phase limits         |
| `PCtrl`               | `P` active-power control                       | Enables flow control |
| `Vsp`                 | Scheduled active power (MW)                    | Flow set-point       |
| `Ext`                 | Terminal where the flow is measured            | Measuring terminal   |
| `Ns`, `IdCurvaPts`    | Steps and curve id                             | —                    |

## `DGER` — generators

| Column       | Meaning                                       | Use                                          |
| ------------ | --------------------------------------------- | -------------------------------------------- |
| `No`         | Bus                                           | Generator bus                                |
| `O`          | Operation                                     | Availability                                 |
| `Pgn`, `Pgm` | Minimum and maximum active power (MW)         | Stored on the bus; not added to the schedule |
| other        | Participation, power factor, and machine data | —                                            |

## `DGLT` — voltage limit groups

| Column         | Meaning                      | Use               |
| -------------- | ---------------------------- | ----------------- |
| `G`            | Group code (`DBAR.Gl`)       | Lookup key        |
| `Vmn`, `Vmx`   | Normal operating limits (pu) | Bus `Vmin`/`Vmax` |
| `Vmne`, `Vmxe` | Emergency limits             | —                 |

## HVDC blocks

| Block.Column                       | Meaning                                                              | Use                                     |
| ---------------------------------- | -------------------------------------------------------------------- | --------------------------------------- |
| `DCLI.I`, `DCLI.J`                 | DC buses at the line ends                                            | Pairs converters into a link            |
| `DCLI.R`                           | DC line resistance (Ω)                                               | Losses $I^2R$                           |
| `DCLI.O`                           | Operation                                                            | In-service filter                       |
| `DCNV.No`                          | Converter number (→ `DCCV.No`)                                       | Control lookup                          |
| `DCNV.Bac`, `DCNV.Bdc`             | AC bus and DC bus                                                    | Terminal buses                          |
| `DCNV.Modo`                        | `R` rectifier or `I` inverter                                        | Converter role                          |
| `DCNV.P`                           | Number of bridges                                                    | Bridge voltage multiplier               |
| `DCNV.Xc`                          | Commutation reactance (% on `Snt`)                                   | Overlap angle                           |
| `DCNV.Vfs`                         | Converter transformer secondary voltage (kV)                         | Overlap angle and tap                   |
| `DCNV.Snt`                         | Converter transformer rating (MVA)                                   | Reactance in Ω                          |
| `DCCV.F`                           | `F` marks the DC slack converter, which absorbs the line losses      | Loss allocation                         |
| `DCCV.TCnv`                        | `P` constant power                                                   | Only power-controlled links are modeled |
| `DCCV.Vsp`                         | Power order (MW)                                                     | $P_{dc}$                                |
| `DCCV.Ang`                         | Firing angle α (rectifier) or extinction angle γ (inverter), degrees | Reactive consumption                    |
| `DCCV.Tmn`, `DCCV.Tmx`, `DCCV.Ttr` | Converter tap limits and present tap                                 | Tap control                             |
| `DCBA.V`                           | DC bus voltage (kV)                                                  | $V_{dc}$                                |
| `DCBA.Elo`                         | Owning link (→ `DELO.No`)                                            | Link lookup                             |
| `DELO.V`                           | Link base voltage (kV)                                               | Loss treatment and tap model            |
| `DELO.Name`                        | Link name                                                            | Reporting                               |
