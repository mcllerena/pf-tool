<h1 align="center">pf-tool</h1>

<p align="center">
  <a href="https://mcllerena.github.io/pf-tool/"><img src="https://img.shields.io/badge/docs-guides-0079c2?style=flat-square" alt="Docs"></a>
  <a href="https://github.com/mcllerena/pf-tool/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://github.com/mcllerena/pf-tool/blob/main/LICENSE.txt"><img src="https://img.shields.io/badge/license-BSD--3--Clause-blue.svg" alt="BSD 3-Clause"></a>
</p>

AC/DC power-flow solver for the Brazilian Interconnected Power System (BIPS). It
solves ANAREDE-style `.dat` cases, about 12,600 buses each, with two methods:

- `NewtonRaphsonPowerFlow`: a sparse polar Newton-Raphson solver.
- `OptimizationPowerFlow`: an optimization-based AC power flow (Pyomo NLP
  solved with Ipopt).

The [documentation](https://mcllerena.github.io/pf-tool/) covers the data
format, component models, both formulations, and benchmark results.

## Installation

```
git clone https://github.com/mcllerena/pf-tool.git
cd pf-tool
uv sync
```

The optimization solver also needs an Ipopt executable on `PATH`.

## Usage

```python
from exporter import ExportSolution
from newton_raphson import NewtonRaphsonPowerFlow
from optimization import OptimizationPowerFlow

newton = NewtonRaphsonPowerFlow("tests/data/CASO01-FLOW.dat")
newton.solve()
ExportSolution(newton, output_path="results/CASO01-FLOW-nr.xlsx")

nlp = OptimizationPowerFlow("tests/data/CASO01-FLOW.dat", warm_start=True).solve()
```

Runnable scripts are in
[`examples/`](https://github.com/mcllerena/pf-tool/tree/main/examples).

## Development

```
uv run pytest                # tests
uv run prek run --all-files  # lint and format hooks
```

## License

[BSD 3-Clause](https://github.com/mcllerena/pf-tool/blob/main/LICENSE.txt)
