from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from pf_tool.exporter import SHEET_HEADERS, ExportSolution
from pf_tool.models.branch import Branch
from pf_tool.models.bus import Bus, BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.newton_raphson import NewtonRaphsonPowerFlow


class ExportSolutionTests(unittest.TestCase):
    def test_exports_every_sheet_and_recovers_switch_flow(self) -> None:
        # Bus 3 hangs on bus 2 through a switch that the reduction contracts,
        # so the switch flow must come from the KCL recovery: the bus 3 load.
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "MID", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 10.0, 5.0, 0.0),
                Bus(3, "LOAD", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 40.0, 15.0, 0.0),
            ],
            branches=[
                Branch(1, 2, 1, 0.01, 0.1, 0.0),
                Branch(2, 3, 1, 0.0, 1e-5, 0.0),
            ],
        )
        solver = object.__new__(NewtonRaphsonPowerFlow)
        solver.case = case
        solver.tolerance = 1e-9
        solver.max_iterations = 20
        solver.data_file = Path("synthetic.dat")
        self.assertTrue(solver.solve().converged)

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "solution.xlsx"
            ExportSolution(solver, output_path=output)
            workbook = load_workbook(output, read_only=True)
            self.assertEqual(workbook.sheetnames, list(SHEET_HEADERS))
            rows = list(workbook["Lines"].iter_rows(min_row=2, values_only=True))
            headers = SHEET_HEADERS["Lines"]
            switch = dict(zip(headers, next(row for row in rows if row[2] == 3)))
            workbook.close()

        self.assertTrue(switch["Collapsed"])
        self.assertAlmostEqual(switch["MW:From-To"], 40.0, places=6)
        self.assertAlmostEqual(switch["Mvar:From-To"], 15.0, places=6)


if __name__ == "__main__":
    unittest.main()
