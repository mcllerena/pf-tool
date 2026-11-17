<h1 align="center">pf-tool</h1>

<p align="center"><em>AC/DC power flow for the Brazilian Interconnected Power System (BIPS).</em></p>

<p align="center">
  <a href="https://github.com/mcllerena/pf-tool/actions/workflows/ci.yaml"><img src="https://github.com/mcllerena/pf-tool/actions/workflows/ci.yaml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/pf-tool/"><img src="https://img.shields.io/pypi/v/pf-tool?label=PyPI&color=0079c2&logo=pypi" alt="PyPI"></a>
  <a href="https://mcllerena.github.io/pf-tool/"><img src="https://img.shields.io/badge/docs-guides-0079c2?style=flat-square" alt="Docs"></a>
  <a href="https://github.com/mcllerena/pf-tool/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/managed%20with-uv-6f42c1" alt="Managed with uv"></a>
  <a href="https://github.com/mcllerena/pf-tool/blob/main/LICENSE.txt"><img src="https://img.shields.io/badge/license-BSD--3--Clause-blue.svg" alt="BSD 3-Clause"></a>
</p>

`pf-tool` solves ANAREDE-style `.dat` cases of the BIPS, about 12,600 buses
each, with a sparse Newton-Raphson solver, a fast decoupled solver, or an
optimization-based AC power flow (Pyomo NLP solved with Ipopt), and exports
every solved component to an Excel workbook.

## Quick Start

### Install

Use Python 3.12 or newer:

```bash
python -m pip install pf-tool
```

### Ipopt

`OptimizationPowerFlow` solves its NLP with
[Ipopt](https://coin-or.github.io/Ipopt/). Download a prebuilt binary from the
[Ipopt releases](https://github.com/coin-or/Ipopt/releases) (or run
`conda install -c conda-forge ipopt`), then either add its `bin` folder to `PATH`:

```powershell
# Windows (PowerShell), current session; use System Properties > Environment
# Variables to make it permanent
$env:Path += ";C:\ipopt\bin"
```

```bash
# Linux/macOS; add the line to ~/.bashrc or ~/.zshrc to make it permanent
export PATH="$PATH:/opt/ipopt/bin"
```

Check it with `ipopt --version`. Or skip `PATH` and pass the executable directly:

```python
nlp = OptimizationPowerFlow(
    filename="tests/data/CASO01-FLOW.dat",
    ipopt_executable="path/to/solver",
    warm_start=True,
    print_iterations=True,
).solve()
```

`DCPowerFlow`, `NewtonRaphsonPowerFlow`, and `FastDecoupledPowerFlow` do not
need Ipopt.

### Parsing BIPS files (PWF and NTW)

Convert ANAREDE network (`.ntw`) and power-flow (`.pwf`) files into `.dat`
files, then pass the returned paths directly to a solver:

```python
from pf_tool import NewtonRaphsonPowerFlow
from pf_tool.parser import Ntw2DatParser, Pwf2DatParser

ntw_dat = Ntw2DatParser("tests/data/ntw/9bus.ntw").convert("results/9bus.dat")
pwf_dat = Pwf2DatParser("tests/data/pwf/d_9nodes.pwf").convert("results/9nodes.dat")

result = NewtonRaphsonPowerFlow(filename=pwf_dat, print_iterations=True).solve()
print(result.converged)
```

Use either returned path with `NewtonRaphsonPowerFlow` or
`FastDecoupledPowerFlow`. `parse()` returns the `.dat` text without writing a
file. NTW records without a solver-native `.dat` block are retained in `DNTW_*`
tables. See the [full parsing guide](docs/src/content/docs/data/parsing-bips.md)
for separate PWF and NTW examples.

### Solve a case with Newton-Raphson AC Power Flow

```python
from pf_tool import ExportSolution, NewtonRaphsonPowerFlow

solver = NewtonRaphsonPowerFlow(
    filename="tests/data/CASO01-FLOW.dat", control_method="simultaneous", print_iterations=True
)
result = solver.solve()

ExportSolution(solver, output_path="results/CASO01-FLOW-nr.xlsx")
```

`control_method` chooses how the controls are solved:

| `control_method`           | How controls are solved                                                                                                                                                                                                                                                                                                              | When to use it                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `"simultaneous"` (default) | Generator reactive limits, LTCs, phase shifters, SVCs, switched shunts, and LCC converters are equations of the same Newton system as the network. A generator can reach a reactive limit and come back off it within the iterations. Shunts are solved continuously, rounded to whole bank units, and the case is solved once more. | Fewer iterations, and closer agreement with the ANAREDE solution on the sample cases.   |
| `"sequential"`             | SVCs, LTCs, and phase shifters are solved in Newton. Generator limits (PV→PQ), switched shunts (one unit per pass), LTC activation, and LCC set-points are updated between full Newton solves until nothing changes.                                                                                                                 | The classic outer-loop approach; slightly faster per case, and useful as a cross-check. |

Both methods converge on all eleven sample cases. See the
[Newton-Raphson method page](https://mcllerena.github.io/pf-tool/methods/newton-raphson/)
for the equations, and
[`examples/compare_control_methods.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/compare_control_methods.py)
for a side-by-side run.

### Solve a case with Fast Decoupled AC Power Flow

```python
from pf_tool import ExportSolution, FastDecoupledPowerFlow

solver = FastDecoupledPowerFlow(
    filename="tests/data/CASO05-FLOW.dat",
    enhancement=True,
    warm_start=True,
    print_iterations=True,
)
result = solver.solve()

ExportSolution(solver, output_path="results/CASO05-FLOW-fd.xlsx")
```

Sample output:

```text
Warm start: Newton-Raphson converged in 12 iterations; re-solving with the fast decoupled method
Fast decoupled power flow (constant B'/B''): CASO05-FLOW.dat
  Buses: 12578 (11368 after low-impedance reduction)
  Tolerance: 1.000e-04 pu (0.01 MW/Mvar), up to 100 iterations per control pass

Pass  Iter  Mismatch (pu)   Max dP (MW)   P bus  Max dQ (Mvar)   Q bus   Ctrl (pu)  Max dV (pu)  Max dAng (deg)    Step
-----------------------------------------------------------------------------------------------------------------------
   1     0      2.456e-02         0.000    7055         -2.456    1754   5.440e-10            -               -       -
   1     1      9.397e-03         0.940    1754         -0.026    9605   7.928e-05      0.00159          0.0000  1.0000
   1     2      2.003e-04         0.020    1715         -0.004    9605   1.435e-05      0.00011          0.0216  1.0000
   1     3      1.449e-09         0.000    1718          0.000    1715   2.583e-11      0.00001          0.0016  1.0000
  -- pass 1 converged; updating LTC regulation and re-solving
   2     0      1.206e-05         0.000    1718          0.000    1715   1.206e-05            -               -       -
  -- pass 2 converged; controls are stable
-----------------------------------------------------------------------------------------------------------------------
Converged in 3 iterations over 2 control passes; max mismatch 1.206e-05 pu (1.206e-03 MW/Mvar)
```

The fast decoupled method splits each Newton step into an active-power/angle
(P-θ) half and a reactive-power/voltage (Q-V) half. Mismatches always use the
full AC equations, so a converged solution is as accurate as Newton-Raphson.

| Argument      | Value             | Behavior                                                                                                                                                                                                                                     |
| ------------- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enhancement` | `False` (default) | Decoupled Newton: the Jacobian blocks `J_Pθ` and `J_QV` are evaluated and factored at every iteration, and both halves are solved from the same point.                                                                                       |
| `enhancement` | `True`            | Fast decoupled: constant `B'` (series reactances only) and `B''` (`Im(Ybus)`) matrices, factored once and refactored only when bus types or regulating devices change. The P and Q half-iterations alternate.                                |
| `warm_start`  | `True` (default)  | Solve the case with sequential Newton-Raphson first, then re-solve with the fast decoupled method from that state. Makes the method reliable on cases with series capacitors, very resistive branches, and negative-reactance star branches. |
| `warm_start`  | `False`           | Start from the voltages stored in the case.                                                                                                                                                                                                  |

LTCs and SVCs are solved in the Q-V half, phase shifters in the P-θ half, and
generator limits, switched shunts, and LCC set-points are updated between
passes. When a decoupled iteration does not reduce the mismatch, it is replaced
by a coupling-corrected step on the full Jacobian. See the
[fast decoupled method page](https://mcllerena.github.io/pf-tool/methods/fast-decoupled/)
for the formulation, and
[`examples/run_fd_powerflow.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/run_fd_powerflow.py).

### Solve a case with DC Power Flow

`DCPowerFlow` applies the linear DC approximation and returns bus angles and
active branch flows in one sparse solve:

The model assumes unit voltage magnitudes, zero series resistance, small angle
differences, and ignores reactive power and shunts. It uses a slack bus for
each island; pass `reference_bus` to choose a different reference in one
island. See the [DC power-flow guide](https://mcllerena.github.io/pf-tool/methods/dc-power-flow/)
and [`examples/run_dc_powerflow.py`](https://github.com/mcllerena/pf-tool/blob/main/examples/run_dc_powerflow.py).

The eleven sample cases are in
[`tests/data/`](https://github.com/mcllerena/pf-tool/tree/main/tests/data), and
runnable scripts are in
[`examples/`](https://github.com/mcllerena/pf-tool/tree/main/examples).

## Documentation

- [Getting started](https://mcllerena.github.io/pf-tool/getting-started/): installation and first solve.
- [Solution pipeline](https://mcllerena.github.io/pf-tool/pipeline/): parsing, reduction, solve, and write-back.
- [Data file](https://mcllerena.github.io/pf-tool/data/blocks/): every `.dat` block and column.
- [Components](https://mcllerena.github.io/pf-tool/components/buses/): how each power-system component is modeled.
- [Results and benchmarks](https://mcllerena.github.io/pf-tool/methods/results/): convergence on all eleven cases.
- [Exporting results](https://mcllerena.github.io/pf-tool/export/): the Excel workbook layout.
- [Changelog](https://github.com/mcllerena/pf-tool/blob/main/CHANGELOG.md): release history.

### Power-flow methods

1. [Newton-Raphson](https://mcllerena.github.io/pf-tool/methods/newton-raphson/): nonlinear AC power flow with simultaneous or sequential controls.
2. [Fast decoupled](https://mcllerena.github.io/pf-tool/methods/fast-decoupled/): decoupled AC power flow with Jacobian-block or constant-matrix options.
3. [DC power flow](https://mcllerena.github.io/pf-tool/methods/dc-power-flow/): linear active-power flow using the B-prime matrix.
4. [Optimization power flow](https://mcllerena.github.io/pf-tool/methods/optimization/): constrained nonlinear optimization solved with Ipopt.

## Development

From the repository root, install the development dependencies with `uv`:

```bash
uv sync --locked --all-groups
```

Then run the tests and the formatting and lint hooks:

```bash
uv run pytest
uv run prek run --all-files
```

The tests run on the IEEE 9-bus system in `tests/data/IEEE9-FLOW.dat`, extended
with every device type in `tests/conftest.py`, so the suite takes a few seconds.
Coverage must stay at or above 90%.

## License

BSD-3-Clause. See [LICENSE.txt](https://github.com/mcllerena/pf-tool/blob/main/LICENSE.txt).
