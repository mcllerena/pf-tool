---
title: Results and benchmarks
description: Convergence, iterations, and solve times for the 11 cases with each method.
---

The benchmark used these settings:

- **Environment:** Windows, Python 3.12, Pyomo 6.10.1, and Ipopt 3.12.13.
- **Tolerance:** $\varepsilon = \text{TEPA}/\text{BASE} = 10^{-4}$ pu.
- **Timing:** elapsed time includes parsing, case construction, network reduction, and all control passes.
- **Sparsity benchmark:** Newton-Raphson with `sparsity=True` and `lu_decomposition=False`; all other settings are the defaults.
- **Levenberg-Marquardt benchmark:** `initial_damping=1e-3`; sequential controls and all other settings are the defaults.
- **Voltage difference:** "Median / p99 dV" is the absolute difference from the bus voltages stored in the `.dat` file, which are the ANAREDE solution.
- **CLU benchmark:** `linear_solver="clu"` using VeraGridEngine 6.8.4. The first Numba JIT warm-up was excluded; per-case times include parsing, construction, and all control passes.

| Method                                          | Converged | Mean time per case | Total time |
| ----------------------------------------------- | --------: | -----------------: | ---------: |
| Newton-Raphson, simultaneous (`sparsity=False`) |   11 / 11 |              5.0 s |     55.2 s |
| Newton-Raphson, simultaneous (`sparsity=True`)  |   11 / 11 |              6.9 s |     75.4 s |
| Newton-Raphson, sequential                      |   11 / 11 |              3.7 s |     40.6 s |
| Levenberg-Marquardt                             |   10 / 11 |             13.6 s |    149.2 s |
| Fast decoupled, warm start                      |   11 / 11 |              5.8 s |     63.9 s |
| Fast decoupled, cold start                      |   11 / 11 |             13.2 s |    145.0 s |
| Optimization, Newton warm start                 |   11 / 11 |             10.2 s |    112.5 s |
| Optimization, cold start                        |   11 / 11 |             37.6 s |    413.6 s |

## Levenberg-Marquardt

The measured run converged on 10 of the 11 cases. CASO11 did not converge: it
stopped after 59 iterations with a 3.117 pu maximum mismatch because a voltage
magnitude left the solver's allowed range of 0.4–2.0 pu. The other ten cases
met the default $10^{-4}$ pu tolerance. This run was slower than
Newton-Raphson; see [the method description](/pf-tool/methods/levenberg-marquardt/)
for its formulation and limitations.

## Newton-Raphson, simultaneous controls (default, `sparsity=False`)

The baseline simultaneous and sequential tables were measured in one run on the same machine. The `sparsity=True` table below was measured separately on the same machine, so treat its timing comparison as indicative; the optimization times come from an earlier run.

| Case   | Buses | Converged | Iterations | Max mismatch (pu) | Time (s) | Voltage range (pu) | Median / p99 dV (pu) |
| ------ | ----: | --------: | ---------: | ----------------: | -------: | -----------------: | -------------------: |
| CASO01 | 12579 |       Yes |         11 |           2.8e-06 |      5.2 |        0.778–1.174 |      0.0007 / 0.0079 |
| CASO02 | 12579 |       Yes |          9 |           3.0e-05 |      4.6 |        0.785–1.195 |      0.0006 / 0.0048 |
| CASO03 | 12579 |       Yes |          9 |           1.1e-09 |      5.2 |        0.755–1.160 |      0.0011 / 0.0069 |
| CASO04 | 12572 |       Yes |          6 |           2.2e-09 |      4.1 |        0.751–1.143 |      0.0004 / 0.0094 |
| CASO05 | 12578 |       Yes |          5 |           1.4e-06 |      4.1 |        0.760–1.120 |      0.0012 / 0.0100 |
| CASO06 | 12613 |       Yes |          6 |           1.4e-09 |      4.9 |        0.752–1.182 |      0.0008 / 0.0091 |
| CASO07 | 12613 |       Yes |          7 |           3.0e-10 |      6.0 |        0.742–1.167 |      0.0008 / 0.0085 |
| CASO08 | 12613 |       Yes |         13 |           1.0e-08 |      6.3 |        0.746–1.164 |      0.0020 / 0.0081 |
| CASO09 | 12607 |       Yes |          9 |           4.3e-08 |      4.5 |        0.764–1.131 |      0.0005 / 0.0085 |
| CASO10 | 12611 |       Yes |         12 |           4.4e-09 |      5.0 |        0.745–1.267 |      0.0008 / 0.0092 |
| CASO11 | 12613 |       Yes |         13 |           8.6e-07 |      5.5 |        0.827–1.123 |      0.0006 / 0.0090 |

## Newton-Raphson, simultaneous controls, `sparsity=True`

This separate run uses `lu_decomposition=False` and `sparsity=True`. All 11 cases converged.

| Case   | Buses | Converged | Iterations | Max mismatch (pu) | Time (s) |
| ------ | ----: | --------: | ---------: | ----------------: | -------: |
| CASO01 | 12579 |       Yes |          6 |           2.8e-06 |     6.15 |
| CASO02 | 12579 |       Yes |          6 |           3.0e-05 |     5.86 |
| CASO03 | 12579 |       Yes |          9 |           1.1e-09 |     7.11 |
| CASO04 | 12572 |       Yes |         12 |           2.2e-09 |     7.77 |
| CASO05 | 12578 |       Yes |          5 |           1.4e-06 |     5.98 |
| CASO06 | 12613 |       Yes |         15 |           1.4e-09 |     9.07 |
| CASO07 | 12613 |       Yes |          7 |           3.0e-10 |     6.20 |
| CASO08 | 12613 |       Yes |         17 |           1.0e-08 |     8.60 |
| CASO09 | 12607 |       Yes |          9 |           4.3e-08 |     5.94 |
| CASO10 | 12611 |       Yes |          6 |           4.4e-09 |     5.34 |
| CASO11 | 12613 |       Yes |         12 |           8.6e-07 |     7.38 |

## Newton-Raphson with the CLU factorizer

The SciPy and CLU runs below use the same cases and default solver options
except for `linear_solver`. Times are one-run measurements on this Windows,
Python 3.12 environment; treat small differences as indicative. CLU's first
Numba compilation is excluded from the per-case times (the CLU warm-up took
31.5 s for simultaneous controls and 11.0 s for sequential controls).

| Control method | Linear solver | Converged | Mean time per case | Total time |
| -------------- | ------------- | --------: | -----------------: | ---------: |
| Simultaneous   | SciPy         |   11 / 11 |              6.0 s |     66.0 s |
| Simultaneous   | CLU           |   10 / 11 |              7.5 s |     82.7 s |
| Sequential     | SciPy         |   11 / 11 |              4.7 s |     51.4 s |
| Sequential     | CLU           |   11 / 11 |              4.1 s |     44.9 s |

With the default simultaneous control method, CLU did not converge on CASO09
within 100 Newton iterations (final mismatch $9.0\times10^{-4}$ pu); SciPy
converged on that case in 9 iterations. Sequential control with CLU converged
on all 11 cases. Use `control_method="sequential"` with CLU when convergence on
the complete BIPS set is required.

### CLU, simultaneous controls

| Case   | Converged | Iterations | Max mismatch (pu) | Time (s) |
| ------ | --------: | ---------: | ----------------: | -------: |
| CASO01 |       Yes |         11 |           2.8e-06 |     6.61 |
| CASO02 |       Yes |          5 |           3.0e-05 |     5.02 |
| CASO03 |       Yes |          9 |           1.1e-09 |     6.17 |
| CASO04 |       Yes |          6 |           2.2e-09 |     4.76 |
| CASO05 |       Yes |          5 |           1.4e-06 |     5.94 |
| CASO06 |       Yes |          6 |           1.4e-09 |     3.16 |
| CASO07 |       Yes |         11 |           3.0e-10 |     4.86 |
| CASO08 |       Yes |          6 |           1.0e-08 |     4.93 |
| CASO09 |        No |        100 |           9.0e-04 |    29.19 |
| CASO10 |       Yes |          6 |           4.4e-09 |     5.56 |
| CASO11 |       Yes |         12 |           8.6e-07 |     6.53 |

### Sequential controls, SciPy and CLU

| Case   | SciPy iterations | CLU iterations | SciPy time (s) | CLU time (s) | CLU max mismatch (pu) |
| ------ | ---------------: | -------------: | -------------: | -----------: | --------------------: |
| CASO01 |               19 |             19 |           5.67 |         4.96 |               2.6e-07 |
| CASO02 |               15 |             12 |           6.33 |         4.78 |               1.7e-06 |
| CASO03 |               18 |             18 |           7.21 |         6.02 |               2.5e-07 |
| CASO04 |               10 |             10 |           4.01 |         2.56 |               5.8e-07 |
| CASO05 |               12 |             12 |           2.70 |         2.70 |               1.8e-06 |
| CASO06 |               12 |             12 |           2.66 |         2.75 |               6.0e-07 |
| CASO07 |               12 |             16 |           4.19 |         5.70 |               6.0e-07 |
| CASO08 |               19 |             14 |           5.84 |         4.21 |               9.5e-07 |
| CASO09 |               15 |             18 |           3.72 |         3.79 |               5.9e-07 |
| CASO10 |               17 |             11 |           3.96 |         3.55 |               6.0e-07 |
| CASO11 |               16 |             18 |           5.09 |         3.88 |               6.0e-07 |

## Newton-Raphson, sequential controls

| Case   | Buses | Converged | Iterations | Max mismatch (pu) | Time (s) | Voltage range (pu) | Median / p99 dV (pu) |
| ------ | ----: | --------: | ---------: | ----------------: | -------: | -----------------: | -------------------: |
| CASO01 | 12579 |       Yes |         19 |           2.6e-07 |      3.7 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02 | 12579 |       Yes |         15 |           1.7e-06 |      3.5 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03 | 12579 |       Yes |         18 |           2.5e-07 |      3.8 |        0.755–1.165 |      0.0008 / 0.0077 |
| CASO04 | 12572 |       Yes |         10 |           5.8e-07 |      3.1 |        0.751–1.143 |      0.0005 / 0.0098 |
| CASO05 | 12578 |       Yes |         12 |           1.8e-06 |      4.2 |        0.760–1.120 |      0.0012 / 0.0100 |
| CASO06 | 12613 |       Yes |         12 |           6.0e-07 |      3.7 |        0.752–1.182 |      0.0009 / 0.0100 |
| CASO07 | 12613 |       Yes |         12 |           6.0e-07 |      3.4 |        0.742–1.166 |      0.0010 / 0.0093 |
| CASO08 | 12613 |       Yes |         19 |           9.5e-07 |      4.0 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09 | 12607 |       Yes |         15 |           5.9e-07 |      3.4 |        0.764–1.131 |      0.0007 / 0.0101 |
| CASO10 | 12611 |       Yes |         17 |           6.0e-07 |      3.6 |        0.745–1.267 |      0.0010 / 0.0096 |
| CASO11 | 12613 |       Yes |         16 |           6.0e-07 |      4.1 |        0.827–1.121 |      0.0007 / 0.0098 |

## Fast decoupled

Per-case tables for both variants (`enhancement=False` and `True`), with warm and cold start, are on the [Fast decoupled](/pf-tool/methods/fast-decoupled/#results) page. All 44 runs converge. The summary rows above use `enhancement=False`.

## Optimization, Newton warm start

| Case   | Converged | NLP solves | Ipopt iterations | Max residual (pu) | Time (s) | Voltage range (pu) | Median / p99 dV (pu) |
| ------ | --------: | ---------: | ---------------: | ----------------: | -------: | -----------------: | -------------------: |
| CASO01 |       Yes |          1 |   7 (+19 Newton) |           4.5e-12 |      9.5 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02 |       Yes |          1 |   8 (+15 Newton) |           3.4e-12 |     10.2 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03 |       Yes |          1 |   7 (+18 Newton) |           9.6e-11 |     11.0 |        0.755–1.165 |      0.0008 / 0.0077 |
| CASO04 |       Yes |          1 |   7 (+11 Newton) |           4.5e-12 |      9.9 |        0.751–1.143 |      0.0005 / 0.0098 |
| CASO05 |       Yes |          1 |   8 (+12 Newton) |           5.5e-12 |     10.5 |        0.760–1.120 |      0.0012 / 0.0100 |
| CASO06 |       Yes |          1 |   7 (+12 Newton) |           1.2e-11 |      9.8 |        0.752–1.182 |      0.0009 / 0.0100 |
| CASO07 |       Yes |          1 |   7 (+12 Newton) |           1.3e-11 |      9.8 |        0.742–1.166 |      0.0010 / 0.0093 |
| CASO08 |       Yes |          1 |   8 (+19 Newton) |           3.3e-12 |     10.6 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09 |       Yes |          1 |   8 (+16 Newton) |           1.9e-12 |     10.3 |        0.764–1.131 |      0.0007 / 0.0101 |
| CASO10 |       Yes |          1 |   7 (+17 Newton) |           3.2e-12 |     10.2 |        0.745–1.267 |      0.0010 / 0.0096 |
| CASO11 |       Yes |          1 |   7 (+16 Newton) |           4.3e-12 |     10.7 |        0.827–1.121 |      0.0007 / 0.0098 |

## Optimization, cold start

| Case   | Converged | NLP solves | Ipopt iterations | Max residual (pu) | Time (s) | Voltage range (pu) | Median / p99 dV (pu) |
| ------ | --------: | ---------: | ---------------: | ----------------: | -------: | -----------------: | -------------------: |
| CASO01 |       Yes |          6 |               49 |           7.6e-12 |     43.9 |        0.777–1.175 |      0.0008 / 0.0083 |
| CASO02 |       Yes |          6 |               47 |           4.0e-12 |     42.5 |        0.785–1.198 |      0.0008 / 0.0050 |
| CASO03 |       Yes |          6 |               48 |           5.1e-11 |     42.9 |        0.755–1.165 |      0.0008 / 0.0078 |
| CASO04 |       Yes |          5 |               40 |           5.2e-12 |     35.4 |        0.751–1.143 |      0.0005 / 0.0112 |
| CASO05 |       Yes |          5 |               42 |           3.9e-12 |     35.9 |        0.760–1.120 |      0.0012 / 0.0161 |
| CASO06 |       Yes |          5 |               44 |           7.8e-12 |     35.6 |        0.752–1.182 |      0.0009 / 0.0110 |
| CASO07 |       Yes |          5 |               40 |           8.3e-12 |     35.3 |        0.742–1.164 |      0.0010 / 0.0102 |
| CASO08 |       Yes |          5 |               44 |           1.0e-11 |     36.0 |        0.746–1.165 |      0.0016 / 0.0090 |
| CASO09 |       Yes |          5 |               42 |           6.1e-11 |     35.5 |        0.764–1.133 |      0.0007 / 0.0116 |
| CASO10 |       Yes |          5 |               39 |           1.3e-11 |     35.2 |        0.745–1.267 |      0.0009 / 0.0109 |
| CASO11 |       Yes |          5 |               40 |           4.0e-12 |     35.5 |        0.827–1.204 |      0.0008 / 0.0109 |

## Reading the results

- **Low voltages:** the lowest values, about 0.74–0.83 pu, are at low-voltage SVC terminal buses. ANAREDE's stored voltages are similarly low there; for example, bus 7035 is stored at 0.734 pu.
- **Agreement with ANAREDE:** the median voltage difference is about 0.001 pu, and at least 99 % of buses are within about 0.01 pu. The remaining differences come mostly from model simplifications that ANAREDE does not make:
  - continuous rather than stepped taps;
  - constant-power loads;
  - CSCs at fixed reactance;
  - the simplified LCC model.
- **Warm start:** the optimization is solved from an operating point that already satisfies the balance, so Ipopt mainly tightens the residuals and confirms feasibility in about 7 iterations.
