---
title: Data blocks
description: Every block of the .dat file, its ANAREDE name, and how the solvers use it.
---

The `.dat` files are AMPL-style exports of ANAREDE `.pwf` cases. Each block is a table:

```text
param: DBAR:    No  Name            Tb  Are      V     A0      Pg  …   E    O   :=
                10  "ANGRA1UNE001"   1  251  0.994    -16     520  …  "L"  "A"
                …
;
```

The block code follows ANAREDE's naming convention: `D` + an abbreviation of the Portuguese name. Most records contain the same two status fields:

- **`O` (operation):** `"A"` means the record is active.
- **`E` (state):** `"L"` means on (_ligado_), and `"D"` means off (_desligado_).

## Block reference

| Block       | ANAREDE name                                   | Meaning                                                                       | Used by the solvers                                                                   |
| ----------- | ---------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `TITU`      | Título                                         | Case title line                                                               | No                                                                                    |
| `DOPC`      | Dados de Opções de Controle                    | Execution and control options (`QLIM`, `CTAP`, …)                             | Yes: enables controls ([options](/pf-tool/data/options/))                             |
| `DCTE`      | Dados de Constantes                            | Numerical constants: base MVA, tolerances, and iteration limits               | Yes: `BASE`, `TEPA`, `ACIT`, and `TLTC`                                               |
| `DBAR`      | Dados de Barras                                | AC buses: type, voltage, angle, generation, load, shunt, and reactive limits  | Yes: [buses](/pf-tool/components/buses/)                                              |
| `DGBT`      | Dados de Grupos de Base de Tensão              | Base-voltage groups, kV per group code                                        | No; voltages are handled in pu                                                        |
| `DGLT`      | Dados de Grupos de Limites de Tensão           | Operating voltage limits per group code                                       | Yes: bus `Vmin`/`Vmax` for `VLIM`                                                     |
| `DARE`      | Dados de Áreas                                 | Areas and scheduled interchange                                               | No; area interchange control is not modeled                                           |
| `DGER`      | Dados de Geradores                             | Generator data: active limits and participation factors                       | Partly: active-power limits are stored but do not change the schedule                 |
| `DLIN`      | Dados de Linhas                                | AC circuits: lines and transformers, including LTC data                       | Yes: [lines and transformers](/pf-tool/components/branches/)                          |
| `DCTR`      | Dados de Controle de Transformadores           | Transformer control data: phase-shifter flow control and voltage bands        | Yes: phase-shifter MW control ([controls](/pf-tool/components/transformer-controls/)) |
| `DCSC`      | Dados de Compensadores Série Controláveis      | Controllable series compensators: TCSC or fixed series capacitors with bypass | Yes: [series compensators](/pf-tool/components/csc/)                                  |
| `DBSH`      | Dados de Bancos Shunt                          | Bus and line shunt banks with voltage-control data                            | Yes: [shunt banks](/pf-tool/components/shunts/)                                       |
| `DBSH_BANK` | Grupos de banco (`FBAN` sub-records of `DBSH`) | Unit groups belonging to a `DBSH` record                                      | Yes: stage sizes and units in service                                                 |
| `DCER`      | Dados de Compensadores Estáticos de Reativos   | Static VAR compensators                                                       | Yes: [SVCs](/pf-tool/components/svc/)                                                 |
| `DELO`      | Dados de Elos CC                               | HVDC links: base voltage and name                                             | Yes: [HVDC LCC](/pf-tool/components/hvdc-lcc/)                                        |
| `DCBA`      | Dados de Barras CC                             | DC buses: voltage and link membership                                         | Yes                                                                                   |
| `DCLI`      | Dados de Linhas CC                             | DC lines: resistance                                                          | Yes                                                                                   |
| `DCNV`      | Dados de Conversores CA-CC                     | AC/DC converters: AC and DC buses, mode, and transformer data                 | Yes                                                                                   |
| `DCCV`      | Dados de Controle de Conversores               | Converter controls: power order, firing angle, and taps                       | Yes                                                                                   |
| `DTPF_CIRC` | Circuitos para totalização de fluxo            | Circuit lists for flow summation and monitoring                               | No                                                                                    |

The column meanings are listed in [Block columns](/pf-tool/data/columns/).

## Case size

The cases describe the Brazilian Interconnected Power System. The figures below are for `CASO01`, as assembled by the current code:

| Item                                                                                |           Count |
| ----------------------------------------------------------------------------------- | --------------: |
| `DBAR` records / buses in service                                                   | 12,617 / 12,579 |
| `DLIN` records / AC branches after assembly, including standalone CSC circuits      | 16,460 / 15,798 |
| `DCSC` series compensators                                                          |             126 |
| `DCER` records / active SVCs                                                        |         48 / 47 |
| `DBSH` records / modeled shunts (663 line shunts)                                   |   1,679 / 1,676 |
| LTC transformers / LTC control groups                                               |   2,232 / 1,133 |
| `DCTR` phase shifters                                                               |               8 |
| `DBAR` `Tb = 1` buses / PV buses kept after zero-range conversion / reference buses |   530 / 291 / 6 |
| HVDC LCC links (`DCLI` lines)                                                       |              17 |
| Buses after low-impedance reduction                                                 |          11,369 |
