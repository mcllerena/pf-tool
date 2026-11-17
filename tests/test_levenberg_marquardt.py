from __future__ import annotations

import unittest
from pathlib import Path

from pf_tool import LevenbergMarquardtPowerFlow


class LevenbergMarquardtTests(unittest.TestCase):
    def test_converges_on_ieee9(self) -> None:
        filename = Path(__file__).parent / "data" / "IEEE9-FLOW.dat"
        solver = LevenbergMarquardtPowerFlow(filename)
        result = solver.solve()

        self.assertTrue(result.converged, result.failure_reason)
        self.assertLessEqual(result.max_mismatch, solver.tolerance)

    def test_rejects_invalid_initial_damping(self) -> None:
        for damping in (0.0, -1.0, float("inf"), float("nan")):
            with (
                self.subTest(damping=damping),
                self.assertRaisesRegex(ValueError, "initial_damping"),
            ):
                LevenbergMarquardtPowerFlow("unused.dat", initial_damping=damping)
