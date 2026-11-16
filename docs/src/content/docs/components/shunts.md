---
title: Shunt banks
description: Bus shunts, line shunts, bank stages, and discrete voltage-controlled switching.
---

Source: `pf_tool/models/shunt.py` and `pf_tool/utils/utils.py`.

## Bus and line shunts

| Source                           | Location                            | Susceptance added                                            |
| -------------------------------- | ----------------------------------- | ------------------------------------------------------------ |
| `DBAR.Sh`                        | The bus                             | $\text{Sh}/S_{base}$                                         |
| `DBSH` with `J = 0` (bus shunt)  | Bus `I`                             | Banks in operation, see below                                |
| `DBSH` with `J ≠ 0` (line shunt) | Terminal `Extr` of circuit `I–J–Nc` | Banks in operation; **only when that circuit is in service** |

A line shunt is connected at the line end named by `Extr`. A line may therefore carry one reactor at each end, each with its own record. If `Extr` is not one of the two terminals, bus `I` is used. Line shunts are always treated as fixed.

`Clr = "S"` on a bus shunt clears the `DBAR` `Sh` value of that bus before the bank is added.

## Bank stages

A `DBSH` record owns the `DBSH_BANK` groups whose `Parent` equals its 1-based record index. Each group is a `ShuntStage` with:

- `unit_mvar` = `Sht`;
- `units` = `U`;
- `in_service` = `UOp`.

The initial injection is:

$$
Q^{0} = \sum_{g} \text{Sht}_g \cdot \text{UOp}_g \qquad (\text{Mvar at } 1\text{ pu}),
$$

The installed range is $[\sum_g \min(0, \text{Sht}_g U_g),\; \sum_g \max(0, \text{Sht}_g U_g)]$. A record without groups becomes one stage of size `Qini`.

The shunt is linear in $|V|^2$. It enters $Y_{bus}$ as $j\,Q/S_{base}$ on the diagonal, scaled by $|s_i|^2 when the bus was merged by the [network reduction](/pf-tool/components/network/).

## Discrete voltage control (`DOPC STEP`)

Records with `Ctrl` `D` or `C` regulate the voltage of `Bctrl` inside `[Vmn, Vmx]`. After each converged inner solve, `adjust_controlled_shunts` checks the controlled voltage:

1. **Within the band:** it does nothing.
2. **Below `Vmn`:** it raises the voltage by one unit. It first removes one reactor unit. If no reactor unit is in service, it inserts one capacitor unit.
3. **Above `Vmx`:** it lowers the voltage by one unit. It first removes one capacitor unit. If no capacitor unit is in service, it inserts one reactor unit.
4. **Anti-hunting:** if the shunt would now switch opposite to its previous move, it is **frozen**. One unit swings the voltage across a narrow band; for example, bus 180 has a 0.005 pu band with 100 Mvar units.

Each pass switches at most one unit per shunt. The solve is then repeated.
