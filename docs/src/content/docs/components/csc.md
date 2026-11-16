---
title: Series compensators (CSC)
description: DCSC records as standalone series circuits, including bypassed compensators.
---

Source: `models/csc.py`.

## Model

Each in-service `DCSC` record, with `O = "A"` and `E ≠ "D"`, is its **own circuit** between `I` and `J`. In the cases, none of the 126 records duplicates a `DLIN` circuit. The series capacitors on the Itaipu 765 kV and Mato Grosso 500 kV corridors are examples.

| Condition                                        | Branch added                                                                                          |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `Byp = "D"`: bypass open, compensator in service | $r = 0$, $x = X_v/100$ pu, with $X_v$ = `Xv` (`Xmin` if blank). Usually negative, meaning capacitive. |
| `Byp = "L"`: bypass closed                       | Short circuit, $x = 10^{-6}$ pu, contracted by the [network reduction](/components/network/)          |
| A `DLIN` circuit with the same `I–J–Nc` exists   | The compensator reactance is added to that circuit in series                                          |

The stored voltages confirm the bypass interpretation: both terminals of a bypassed compensator have identical voltage and angle.

The control mode `Ctrl` (`X` constant reactance, `P` power, `I` current) is not solved. The present reactance `Xv` is used as a fixed series reactance; every compensator in the cases uses `Ctrl = X`.

## Why it matters

Before this model, every `DCSC` record was dropped because no matching `DLIN` circuit was found. That removed major corridors. Newton's first step asked for angle changes of about −14 rad across the weakly connected Mato Grosso region, and no case converged.
