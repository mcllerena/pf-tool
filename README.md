# pf-tool

**pf-tool** is an AC/DC power-flow package for the Brazilian Interconnected
Power System (BIPS). It solves the ANAREDE-style `.dat` cases in `data/`, which
are BIPS snapshots of about 12,600 buses each, with two AC power-flow methods:

- [`newton_raphson.py`](newton_raphson.py): `NewtonRaphsonPowerFlow`, a sparse
  polar Newton-Raphson solver.
- [`optimization.py`](optimization.py): `OptimizationPowerFlow`, an
  optimization-based AC power flow (Pyomo NLP solved with Ipopt), following the
  formulation of the o-grid optimization ACPF paper.

Component models live in [`models/`](models/), and the `.dat` parser and case
assembly live in [`utils/`](utils/).

```python
from newton_raphson import NewtonRaphsonPowerFlow
from optimization import OptimizationPowerFlow

newton = NewtonRaphsonPowerFlow("data/CASO01-FLOW.dat").solve()
nlp = OptimizationPowerFlow("data/CASO01-FLOW.dat", warm_start=True).solve()
```

The optimization solver requires `pyomo` (see `requirements.txt`) and an Ipopt
executable on `PATH`.

### Documentation

[`docs/`](docs/) is an [Astro Starlight](https://starlight.astro.build/) site.
It documents:

- every data block and column;
- how each power-system component is modeled;
- the full Newton-Raphson algorithm;
- the full optimization formulation: sets, parameters, variables, expressions,
  constraints, and objective.

```
cd docs
npm install
npm run build    # static site in docs/dist
npm run dev      # local preview at http://localhost:4321
```

### Example scripts

[`examples/`](examples/) contains one minimal script per solver class. Each
script sets `data_file`, solves the case, prints the result, and exports the
solution with `ExportSolution` to `examples/results/`. To run another case,
change `data_file`.

```
python examples/run_nr_powerflow.py
python examples/run_opt_powerflow.py
```

### Exporting results

`ExportSolution` (in [`exporter.py`](exporter.py)) writes every solved
component to an Excel workbook. It uses one sheet per component: Summary,
Buses, Generators, Loads, Lines, Transformers, LTC, PST, HVDC, SVC, CSC, and
Shunts.

```python
from exporter import ExportSolution

solver = NewtonRaphsonPowerFlow("data/CASO01-FLOW.dat")
solver.solve()
ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

Flows on branches collapsed by the zero-impedance reduction, such as switches
and bypassed CSCs, are recovered from Kirchhoff's current law in each reduced
node. In the exported solution, every bus balances to within 3e-5 MVA.

## Convergence summary

All 11 cases converge with every method.

| Method                          | Cases converged | Mean time per case (s) | Total time (s) |
| ------------------------------- | --------------: | ---------------------: | -------------: |
| Newton-Raphson                  |         11 / 11 |                    2.8 |           31.0 |
| Optimization, Newton warm start |         11 / 11 |                   10.2 |          112.5 |
| Optimization, cold start        |         11 / 11 |                   37.6 |          413.6 |

Measurement details:

- Tolerance is `TEPA / BASE = 1e-4 pu` from each case's `DCTE` block.
- Newton's iteration limit is `ACIT`.
- Elapsed time includes parsing, case construction, network reduction, and all
  control passes.
- Runs used Windows, Python 3.12, Pyomo 6.10.1, and Ipopt 3.12.13.
- `Median / p99 dV` is the median and 99th-percentile absolute difference from
  the bus voltages stored in each `.dat` file, which are the ANAREDE solution.

The lowest voltages, about 0.74–0.83 pu, occur at low-voltage SVC terminal
buses. ANAREDE's stored voltages are similarly low there; for example, bus
7035 is stored at 0.734 pu.

### Newton-Raphson

| Case            | Buses | Converged | Iterations | Final max mismatch (pu) | Elapsed (s) | Voltage range (pu) | Median / p99 dV (pu) |
| --------------- | ----: | --------: | ---------: | ----------------------: | ----------: | -----------------: | -------------------: |
| CASO01-FLOW.dat | 12579 |       Yes |         19 |                 9.7e-07 |         2.7 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02-FLOW.dat | 12579 |       Yes |         15 |                 6.0e-06 |         2.8 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03-FLOW.dat | 12579 |       Yes |         18 |                 9.4e-07 |         3.2 |        0.755–1.165 |      0.0008 / 0.0077 |
| CASO04-FLOW.dat | 12572 |       Yes |         11 |                 2.6e-07 |         2.5 |        0.751–1.143 |      0.0005 / 0.0098 |
| CASO05-FLOW.dat | 12578 |       Yes |         12 |                 5.9e-06 |         2.7 |        0.760–1.120 |      0.0012 / 0.0100 |
| CASO06-FLOW.dat | 12613 |       Yes |         12 |                 2.0e-06 |         2.7 |        0.752–1.182 |      0.0009 / 0.0100 |
| CASO07-FLOW.dat | 12613 |       Yes |         12 |                 2.0e-06 |         2.7 |        0.742–1.166 |      0.0010 / 0.0093 |
| CASO08-FLOW.dat | 12613 |       Yes |         19 |                 3.2e-06 |         2.9 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09-FLOW.dat | 12607 |       Yes |         16 |                 2.7e-07 |         2.7 |        0.764–1.131 |      0.0007 / 0.0101 |
| CASO10-FLOW.dat | 12611 |       Yes |         17 |                 2.0e-06 |         2.8 |        0.745–1.267 |      0.0010 / 0.0096 |
| CASO11-FLOW.dat | 12613 |       Yes |         16 |                 2.0e-06 |         3.1 |        0.827–1.121 |      0.0007 / 0.0098 |

Iterations are totals across all outer control passes.

### Optimization AC power flow (Newton warm start)

| Case            | Converged | NLP solves | Ipopt iterations | Final max residual (pu) | Elapsed (s) | Voltage range (pu) | Median / p99 dV (pu) |
| --------------- | --------: | ---------: | ---------------: | ----------------------: | ----------: | -----------------: | -------------------: |
| CASO01-FLOW.dat |       Yes |          1 |   7 (+19 Newton) |                 4.5e-12 |         9.5 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02-FLOW.dat |       Yes |          1 |   8 (+15 Newton) |                 3.4e-12 |        10.2 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03-FLOW.dat |       Yes |          1 |   7 (+18 Newton) |                 9.6e-11 |        11.0 |        0.755–1.165 |      0.0008 / 0.0077 |
| CASO04-FLOW.dat |       Yes |          1 |   7 (+11 Newton) |                 4.5e-12 |         9.9 |        0.751–1.143 |      0.0005 / 0.0098 |
| CASO05-FLOW.dat |       Yes |          1 |   8 (+12 Newton) |                 5.5e-12 |        10.5 |        0.760–1.120 |      0.0012 / 0.0100 |
| CASO06-FLOW.dat |       Yes |          1 |   7 (+12 Newton) |                 1.2e-11 |         9.8 |        0.752–1.182 |      0.0009 / 0.0100 |
| CASO07-FLOW.dat |       Yes |          1 |   7 (+12 Newton) |                 1.3e-11 |         9.8 |        0.742–1.166 |      0.0010 / 0.0093 |
| CASO08-FLOW.dat |       Yes |          1 |   8 (+19 Newton) |                 3.3e-12 |        10.6 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09-FLOW.dat |       Yes |          1 |   8 (+16 Newton) |                 1.9e-12 |        10.3 |        0.764–1.131 |      0.0007 / 0.0101 |
| CASO10-FLOW.dat |       Yes |          1 |   7 (+17 Newton) |                 3.2e-12 |        10.2 |        0.745–1.267 |      0.0010 / 0.0096 |
| CASO11-FLOW.dat |       Yes |          1 |   7 (+16 Newton) |                 4.3e-12 |        10.7 |        0.827–1.121 |      0.0007 / 0.0098 |

### Optimization AC power flow (cold start)

`warm_start=False` starts the NLP from the stored voltages. Before the first
solve, it reconciles transformer settings with those voltages; see
"Transformers" below.

| Case            | Converged | NLP solves | Ipopt iterations | Final max residual (pu) | Elapsed (s) | Voltage range (pu) | Median / p99 dV (pu) |
| --------------- | --------: | ---------: | ---------------: | ----------------------: | ----------: | -----------------: | -------------------: |
| CASO01-FLOW.dat |       Yes |          6 |               49 |                 7.6e-12 |        43.9 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02-FLOW.dat |       Yes |          6 |               47 |                 4.0e-12 |        42.5 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03-FLOW.dat |       Yes |          6 |               48 |                 5.1e-11 |        42.9 |        0.755–1.165 |      0.0008 / 0.0078 |
| CASO04-FLOW.dat |       Yes |          5 |               40 |                 5.2e-12 |        35.4 |        0.751–1.143 |      0.0005 / 0.0112 |
| CASO05-FLOW.dat |       Yes |          5 |               42 |                 3.9e-12 |        35.9 |        0.760–1.120 |      0.0012 / 0.0161 |
| CASO06-FLOW.dat |       Yes |          5 |               44 |                 7.8e-12 |        35.6 |        0.752–1.182 |      0.0009 / 0.0110 |
| CASO07-FLOW.dat |       Yes |          5 |               40 |                 8.3e-12 |        35.3 |        0.742–1.164 |      0.0010 / 0.0102 |
| CASO08-FLOW.dat |       Yes |          5 |               44 |                 1.0e-11 |        36.0 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09-FLOW.dat |       Yes |          5 |               42 |                 6.1e-11 |        35.5 |        0.764–1.133 |      0.0007 / 0.0116 |
| CASO10-FLOW.dat |       Yes |          5 |               39 |                 1.3e-11 |        35.2 |        0.745–1.267 |      0.0009 / 0.0109 |
| CASO11-FLOW.dat |       Yes |          5 |               40 |                 4.0e-12 |        35.5 |        0.827–1.204 |      0.0008 / 0.0109 |

The cold-start NLP does not include LTC voltage regulation. Its solutions are
therefore slightly farther from ANAREDE than the Newton and warm-start results,
with a larger p99 voltage difference.

## What made the cases converge

The earlier non-convergence came mainly from network-data errors, not from
solver tuning. With the corrected model, both methods reach the ANAREDE
operating point.

1. **`DCSC` series compensators are standalone circuits.** All 126 `DCSC`
   records describe their own `I–J` circuit, and none duplicates a `DLIN` line.
   The previous builder dropped them, removing series-compensated corridors such
   as Itaipu 765 kV and the Mato Grosso 500 kV lines. Active compensators now
   use reactance `Xv` (%), and bypassed ones (`Byp = "L"`) are shorts that the
   network reduction contracts.
2. **Line shunts are placed at terminal `Extr`.** They are included only when
   their circuit is in service. Previously every `DBSH` record was assigned to
   bus `I`.
3. **Zero-range generators are fixed-Q buses.** PV buses with
   `Qgn = Qgm = 0` are blank ANAREDE limits written as zero, and their stored
   `Qg` is 0. They are modeled as PQ buses at that output.
4. **`DGER Pgn` is minimum generation.** It is no longer added to the
   scheduled generation in `DBAR`.
5. **`VLIM` applies only to ANAREDE type-3 buses.** These are PQ buses with
   voltage limits. Converting every out-of-range PQ bus to PV conflicted with
   generator Q limits.
6. **Transformer settings are reconciled with stored voltages.** Saved cases
   contain solved voltages, but some LTCs keep pre-solution taps. For example,
   the Sapeaçu 230/500 kV units around bus 6368 would otherwise create a
   357 pu initial mismatch across a 0.05 % reactance. Each tap or phase-shift
   group receives a one-variable least-squares correction against the P/Q
   mismatch at its terminals. The solver applies it only when it removes most
   of a significant mismatch.

## Newton-Raphson details

- **Low-impedance contraction:** Branches with `|z| <= 2e-4 pu` are contracted
  with exact complex tap scaling.
- **Islands:** Every island without a reference bus receives one.
- **State variables:** The Newton state includes:
  - bus angles and PQ voltage magnitudes;
  - SVC outputs;
  - LTC tap shifts (`DOPC CTAP`);
  - phase-shifter angles (`DOPC CPHS`, `DCTR` records).
- **LTCs:** An LTC group regulates its controlled bus only after that voltage
  leaves `Vsp ± TLTC` (`DCTE TLTC`). It then regulates to the band edge.
  Groups that keep pushing against `Tmn/Tmx` are fixed at that limit.
  Groups whose Newton step is non-physical (`|Δtap| > 0.2`) are fixed because
  they lack an independent effect on their target, such as two LTCs regulating
  electrically identical buses.
- **Phase shifters:** A phase shifter holds the active power leaving the
  `Ext` terminal at `Vsp` MW, within `Fmin/Fmax`.
- **Step control:** Large control moves shorten the whole Newton step, which
  keeps the search direction intact. The line search accepts a step that reduces
  either the max-norm or the 2-norm of the mismatch.
- **Outer control passes:** These handle generator Q limits (`QLIM`), type-3
  voltage limits (`VLIM`), LTC activation, LCC set-points, and switched shunts
  (`STEP`).
- **Switched shunts:** These switch one bank unit per pass. A shunt that would
  reverse direction is frozen to prevent hunting.

## Optimization AC power flow details

The NLP follows the o-grid paper's formulation:

- **Residuals:** Signed P/Q residual variables are bounded per bus and in
  aggregate by the tolerance.
- **Objective:** It minimizes tolerance-normalized squared residuals and
  includes a small regularization toward the initial state.
- **Fixed schedule:** Scheduled active generation remains fixed.
- **Voltage bounds:** Voltage magnitudes have only broad numerical bounds,
  `[0.4, 2.0]` pu. Operating limits are separate from convergence.
- **Convergence check:** A run converges only when Ipopt terminates
  acceptably and every P/Q mismatch and SVC control error recomputed from
  `Ybus` and the solved voltages is within tolerance. Aggregate P and Q
  mismatches must also be within tolerance. Ipopt's constraint tolerance is set
  to `1e-9`.
- **SVC regions:** SVCs are modeled by operating region. A regulating SVC
  follows its `P` or `I` droop law. A saturated SVC injects `Qlim·Vterminal²`.
  After each NLP solve, region changes, generator Q limits, switched shunts,
  and LCC set-points are updated, and the NLP is solved again. A saturated SVC
  returns to regulation before it can switch to the opposite limit.
- **Warm start:** The default `warm_start=True` uses the Newton solution and
  control state as the initial point, as recommended by the paper.

## SVC control modes

The `.dat` `DCER` records use `P` for reactive-power control and `I` for
current control:

- `P`: `Vcontrolled - Vreference + slope * Q = 0`
- `I`: `Vcontrolled - Vreference + slope * Q / Vdevice = 0`

`Vreference` is the `DBAR` voltage of the controlled bus. Capability limits
scale with the square of the SVC terminal voltage. Both solvers handle
saturation by switching SVC operating regions.

## Tests

```
python -m unittest discover -s tests
```

The test suite covers parser behavior, SVC `P/I` modes and saturation, and LCC
commutation overlap. It also covers `DCSC` series and bypass branches,
line-shunt placement, the LTC dead band, phase-shifter flow control, shunt
anti-hunting, and SVC region switching in the NLP.

## Formatting and linting

[prek](https://github.com/j178/prek) runs the hooks in
`.pre-commit-config.yaml`:

- **Builtin file checks:** trailing whitespace, end-of-file, LF line endings,
  TOML/YAML/JSON validity, JSON formatting, merge conflicts, private keys, and
  large files.
- **Python:** `ruff check --fix` and `ruff format`, configured in
  `pyproject.toml`.
- **TOML:** `taplo` format and lint.
- **Docs and configs:** `prettier` for Markdown, MDX, CSS, JS/TS, and YAML.
- **Spelling:** `typos`.

The `data/` cases are excluded, so they are never rewritten.

`prek run --all-files` only checks files that git already tracks. To run every
hook on all non-ignored files, tracked or not, use:

```
./scripts/prek-all.ps1
```

The script runs `prek run --files` on every file from
`git ls-files --cached --others --exclude-standard`. Extra arguments are passed
to prek; for example, `./scripts/prek-all.ps1 ruff-format` runs only that hook.
