---
title: Options and constants
description: DOPC execution options and DCTE constants that drive the solvers.
---

## `DOPC` — control options

Each `DOPC` record is `Op E`, where `E = "L"` means the option is on (_ligado_). Options read by the code default to on when they are missing.

| Option                                                                                                                         | ANAREDE meaning                                   | Effect in this code                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `NEWT`                                                                                                                         | Newton method                                     | If off, Newton returns immediately without solving. It also gates the Newton warm start of the optimization model. |
| `QLIM`                                                                                                                         | Generator reactive limits                         | Outer pass: a PV bus that exceeds `Qgn`/`Qgm` by more than 0.1 Mvar becomes PQ at that limit.                      |
| `VLIM`                                                                                                                         | Voltage limits on type-3 buses                    | Outer pass: a `Tb = 3` bus outside `Vmin`/`Vmax` by more than 0.001 pu becomes PV at the violated limit.           |
| `CTAP`                                                                                                                         | LTC voltage control                               | Creates LTC control groups from `DLIN` and solves tap shifts in Newton.                                            |
| `CPHS`                                                                                                                         | Phase-shifter control                             | Creates `DCTR` active-power controls and solves phase angles in Newton.                                            |
| `STEP`                                                                                                                         | Step and discrete controls                        | Enables switched-shunt bank switching in the outer passes.                                                         |
| `CREM`, `RCVG`, `RMON`, `FILE`, `SUBS`, `CONT`, `NOVO`, `CELO`, `MFCT`, `AREG`, `STPO`, `VLCR`, `PMIN`, `INMN`, `IANG`, `UNPG` | Reporting, monitoring, and other ANAREDE features | Not used                                                                                                           |

## `DCTE` — constants

| Constant                                          | Meaning                                          | Value in the cases | Use                                                                        |
| ------------------------------------------------- | ------------------------------------------------ | -----------------: | -------------------------------------------------------------------------- |
| `BASE`                                            | System base power (MVA)                          |                100 | Per-unit conversion                                                        |
| `TEPA`                                            | Active and reactive mismatch tolerance (MW/Mvar) |               0.01 | Convergence tolerance $\varepsilon = \text{TEPA}/\text{BASE} = 10^{-4}$ pu |
| `ACIT`                                            | Maximum AC iterations                            |                100 | Newton iteration limit; Ipopt uses `max(200, 2·ACIT)`                      |
| `TLTC`                                            | LTC voltage tolerance (pu)                       |               0.01 | LTC dead band $\pm$`TLTC` around the set-point                             |
| other (`DASE`, `TEPR`, `QLST`, `ZMIN`, `VFLD`, …) | Other ANAREDE tolerances and limits              |                  — | Not used                                                                   |

:::note
The zero impedance threshold is fixed at $2\times10^{-4}$ pu and is not read from `ZMIN`. Loads are constant power. The `VFLD` constant-impedance switch-over is not modeled.
:::
