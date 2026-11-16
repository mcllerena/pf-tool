from __future__ import annotations

import unittest

from pf_tool.models.branch import Branch
from pf_tool.models.bus import Bus, BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.svc import SVC
from pf_tool.optimization import (
    build_optimization_model,
    solution_metrics,
    solve_optimization_model,
    update_svc_regions,
)


class IpoptExecutableTests(unittest.TestCase):
    def test_missing_executable_path_raises_clear_error(self) -> None:
        import pyomo.environ as pyo

        with self.assertRaisesRegex(RuntimeError, "no-such-ipopt"):
            solve_optimization_model(
                pyo.ConcreteModel(), ipopt_executable="C:/no-such-dir/no-such-ipopt"
            )


class OptimizationPowerFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        from pyomo.environ import SolverFactory

        if not SolverFactory("ipopt").available(exception_flag=False):
            self.skipTest("Ipopt is not installed")

    def test_solves_a_two_bus_load_case(self) -> None:
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(
                    1,
                    "SLACK",
                    BusType.SLACK,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ),
                Bus(
                    2,
                    "LOAD",
                    BusType.PQ,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    50.0,
                    20.0,
                    0.0,
                ),
            ],
            branches=[Branch(1, 2, 1, 0.0, 0.1, 0.0)],
        )
        model = build_optimization_model(case, tolerance=1e-8)

        results, _ = solve_optimization_model(model, max_iterations=100)

        self.assertEqual(str(results.solver.termination_condition), "optimal")
        metrics = solution_metrics(model)
        self.assertTrue(metrics["converged"], metrics)
        self.assertLess(float(metrics["max_p"]), 1e-8)
        self.assertLess(float(metrics["max_q"]), 1e-8)
        self.assertLess(float(model.vm[1].value), 1.0)

    def test_svc_power_and_current_modes_are_feasible(self) -> None:
        for mode in ("P", "I"):
            with self.subTest(mode=mode):
                case = PowerFlowCase(
                    base_mva=100.0,
                    buses=[
                        Bus(
                            1,
                            "SLACK",
                            BusType.SLACK,
                            1.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                            0.0,
                        ),
                        Bus(
                            2,
                            "SVC",
                            BusType.PQ,
                            1.0,
                            0.0,
                            0.0,
                            20.0,
                            0.0,
                            20.0,
                            0.0,
                        ),
                    ],
                    branches=[Branch(1, 2, 1, 0.0, 0.1, 0.0)],
                    svcs=[
                        SVC(
                            bus=2,
                            controlled_bus=2,
                            mode=mode,
                            slope=0.1,
                            q_mvar=20.0,
                            q_min_mvar=-100.0,
                            q_max_mvar=100.0,
                            reference_voltage=1.02,
                        )
                    ],
                )
                model = build_optimization_model(case, tolerance=1e-8)

                results, _ = solve_optimization_model(model, max_iterations=100)

                self.assertEqual(str(results.solver.termination_condition), "optimal")
                metrics = solution_metrics(model)
                self.assertTrue(metrics["converged"], metrics)

    def test_svc_saturates_at_device_voltage_capability(self) -> None:
        from pyomo.environ import value

        # Bus 3 is held near 1.0 pu by the slack; the SVC at bus 2 cannot
        # raise it to 1.05 pu with only 10 Mvar, so it must saturate.
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "SVC", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 30.0, 20.0, 0.0),
                Bus(3, "REMOTE", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            ],
            branches=[
                Branch(1, 2, 1, 0.0, 0.2, 0.0),
                Branch(2, 3, 1, 0.0, 0.05, 0.0),
                Branch(1, 3, 1, 0.0, 0.05, 0.0),
            ],
            svcs=[
                SVC(
                    bus=2,
                    controlled_bus=3,
                    mode="P",
                    slope=0.01,
                    q_mvar=0.0,
                    q_min_mvar=-10.0,
                    q_max_mvar=10.0,
                    reference_voltage=1.05,
                )
            ],
        )
        regions = {0: 0}
        model = build_optimization_model(case, tolerance=1e-8, svc_regions=regions)
        solve_optimization_model(model, max_iterations=100)

        self.assertTrue(update_svc_regions(model, regions))
        self.assertEqual(regions, {0: 1})

        model = build_optimization_model(case, tolerance=1e-8, svc_regions=regions)
        results, _ = solve_optimization_model(model, max_iterations=100)

        self.assertEqual(str(results.solver.termination_condition), "optimal")
        self.assertTrue(solution_metrics(model)["converged"])
        self.assertFalse(update_svc_regions(model, regions))
        device_voltage = value(model.vm[1])
        self.assertLess(device_voltage, 1.0)
        self.assertAlmostEqual(value(model.qsvc[0]), 0.1 * device_voltage**2, places=7)


if __name__ == "__main__":
    unittest.main()
