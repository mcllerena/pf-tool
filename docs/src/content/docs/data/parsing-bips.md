---
title: Parsing BIPS files (PWF and NTW)
description: Convert ANAREDE PWF and NTW network files into DAT files and solve them with pf-tool.
---

`pf-tool` can convert ANAREDE power-flow (`.pwf`) and network (`.ntw`) files
into the block-based `.dat` format used by its solvers. The converter returns
the output path, which can be passed directly to a solver.

## Convert a PWF file

```python
from pf_tool.parser import Pwf2DatParser

pwf_dat = Pwf2DatParser("tests/data/pwf/d_9nodes.pwf").convert(
    "results/9nodes.dat"
)
print(pwf_dat)
```

`Pwf2DatParser` uses the package's ANAREDE field mapping by default, so no
additional mapping file is required.

## Convert an NTW file

```python
from pf_tool.parser import Ntw2DatParser

ntw_dat = Ntw2DatParser("tests/data/ntw/9bus.ntw").convert(
    "results/9bus.dat"
)
print(ntw_dat)
```

NTW records that do not have a solver-native `.dat` block are retained in
`DNTW_*` tables in the converted file.

## Solve a converted case

Use the returned path with either AC power-flow solver:

```python
from pf_tool import FastDecoupledPowerFlow
from pf_tool.parser import Pwf2DatParser

pwf_dat = Pwf2DatParser("tests/data/pwf/d_9nodes.pwf").convert(
    "results/9nodes.dat"
)

result = FastDecoupledPowerFlow(
    filename=pwf_dat,
    print_iterations=True,
).solve()
print(f"Converged: {result.converged}")
```

To work with the generated text without writing a file, use `parse()` instead
of `convert()`:

```python
from pf_tool.parser import Ntw2DatParser

dat_text = Ntw2DatParser("tests/data/ntw/9bus.ntw").parse()
print(dat_text[:200])
```
