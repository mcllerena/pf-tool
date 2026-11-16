"""Solve one case with both Newton-Raphson control methods and compare them.

"simultaneous" (default) solves generator reactive limits, LTCs, switched
shunts, and LCC converters in the same Newton system as the network.
"sequential" updates them between full Newton solves.
"""

import time

import numpy as np

from pf_tool import NewtonRaphsonPowerFlow
from pf_tool.models.bus import BusType

filename = "tests/data/CASO01-FLOW.dat"

for control_method in ("simultaneous", "sequential"):
    solver = NewtonRaphsonPowerFlow(filename=filename, control_method=control_method)
    # The .dat file stores the ANAREDE solution as the starting voltages.
    stored = np.array([bus.voltage_pu for bus in solver.case.buses])
    start = time.perf_counter()
    result = solver.solve()
    elapsed = time.perf_counter() - start

    deviation = np.abs(np.abs(result.voltage) - stored)
    at_q_limit = sum(
        bus.dbar_type in (1, 2)
        and bus.bus_type == BusType.PQ
        and bus.q_min_mvar is not None
        and bus.q_max_mvar is not None
        and bus.q_max_mvar > bus.q_min_mvar  # zero-range generators are PQ from the start
        for bus in solver.case.buses
    )
    print(f"{control_method}:")
    print(
        f"  converged: {result.converged}, iterations: {result.iterations}, time: {elapsed:.1f} s"
    )
    print(f"  max mismatch: {result.max_mismatch:.2e} pu")
    print(
        f"  |V - V_ANAREDE|: median {np.median(deviation):.4f} pu, "
        f"p99 {np.percentile(deviation, 99):.4f} pu"
    )
    print(f"  generator buses solved as PQ (at a reactive limit): {at_q_limit}")
