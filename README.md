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
each, with a sparse Newton-Raphson solver or an optimization-based AC power
flow (Pyomo NLP solved with Ipopt), and exports every solved component to an
Excel workbook.

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

`NewtonRaphsonPowerFlow` does not need Ipopt.

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

The eleven sample cases are in
[`tests/data/`](https://github.com/mcllerena/pf-tool/tree/main/tests/data), and
runnable scripts are in
[`examples/`](https://github.com/mcllerena/pf-tool/tree/main/examples).

## Documentation

- [Getting started](https://mcllerena.github.io/pf-tool/getting-started/): installation and first solve.
- [Solution pipeline](https://mcllerena.github.io/pf-tool/pipeline/): parsing, reduction, solve, and write-back.
- [Data file](https://mcllerena.github.io/pf-tool/data/blocks/): every `.dat` block and column.
- [Components](https://mcllerena.github.io/pf-tool/components/buses/): how each power-system component is modeled.
- [Newton-Raphson](https://mcllerena.github.io/pf-tool/methods/newton-raphson/) and [optimization](https://mcllerena.github.io/pf-tool/methods/optimization/): full formulations.
- [Results and benchmarks](https://mcllerena.github.io/pf-tool/methods/results/): convergence on all eleven cases.
- [Exporting results](https://mcllerena.github.io/pf-tool/export/): the Excel workbook layout.
- [Changelog](https://github.com/mcllerena/pf-tool/blob/main/CHANGELOG.md): release history.

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

## License

BSD-3-Clause. See [LICENSE.txt](https://github.com/mcllerena/pf-tool/blob/main/LICENSE.txt).
