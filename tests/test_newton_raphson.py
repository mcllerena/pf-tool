from __future__ import annotations

import unittest

import numpy as np

from pf_tool.models.branch import Branch
from pf_tool.models.bus import Bus, BusType
from pf_tool.models.case import PowerFlowCase
from pf_tool.models.hvdc_lcc import build_lcc_links, update_lcc_from_dc_solution
from pf_tool.models.svc import SVC, SVCState, refresh_svc_limits
from pf_tool.newton_raphson import NewtonRaphsonPowerFlow
from pf_tool.utils.dat_parser import parse_dat_text


class DatParserTests(unittest.TestCase):
    def test_reads_quoted_fields_and_repeated_blocks(self) -> None:
        text = """
param: DCTE: Mn Val :=
    "BASE" 100
;
param: DBAR: No Name :=
    1 "BUS WITH SPACES"
;
param: DCTE: Mn Val :=
    "TEPA" 0.01
;
"""
        parsed = parse_dat_text(text)
        self.assertEqual(parsed["DBAR"][0]["Name"], "BUS WITH SPACES")
        self.assertEqual([row["Mn"] for row in parsed["DCTE"]], ["BASE", "TEPA"])


class NewtonRaphsonTests(unittest.TestCase):
    def test_solves_a_two_bus_load_case(self) -> None:
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "LOAD", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 50.0, 20.0, 0.0),
            ],
            branches=[
                Branch(1, 2, 1, 0.0, 0.1, 0.0),
            ],
        )
        solver = object.__new__(NewtonRaphsonPowerFlow)
        solver.case = case
        solver.tolerance = 1e-9
        solver.max_iterations = 20
        result = solver.solve()
        self.assertTrue(result.converged, result.failure_reason)
        self.assertLess(result.max_mismatch, 1e-9)
        self.assertLess(abs(result.voltage[1]), 1.0)
        self.assertTrue(np.isfinite(result.voltage).all())

    def test_print_iterations_prints_one_row_per_iteration_and_a_summary(self) -> None:
        import contextlib
        import io

        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "LOAD", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 50.0, 20.0, 0.0),
            ],
            branches=[Branch(1, 2, 1, 0.0, 0.1, 0.0)],
        )
        solver = object.__new__(NewtonRaphsonPowerFlow)
        solver.case = case
        solver.tolerance = 1e-9
        solver.max_iterations = 20
        solver.print_iterations = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = solver.solve()

        lines = output.getvalue().splitlines()
        header = next(i for i, line in enumerate(lines) if line.lstrip().startswith("Pass"))
        rows = [line.split() for line in lines[header + 2 :] if line[:4].strip().isdigit()]
        self.assertEqual([int(row[1]) for row in rows], list(range(result.iterations + 1)))
        self.assertEqual(rows[0][-1], "-")
        # The only PQ bus carries the initial Q mismatch.
        self.assertEqual(rows[0][6], "2")
        self.assertGreater(abs(float(rows[0][5])), 1.0)
        self.assertTrue(lines[-1].startswith(f"Converged in {result.iterations} iterations"))
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
                solver = object.__new__(NewtonRaphsonPowerFlow)
                solver.case = case
                solver.tolerance = 1e-9
                solver.max_iterations = 20

                result = solver.solve()

                self.assertTrue(result.converged, result.failure_reason)
                self.assertEqual(result.iterations, 0)
                self.assertLess(result.max_mismatch, 1e-9)


class ComponentControlTests(unittest.TestCase):
    def test_svc_saturates_from_requested_droop_output(self) -> None:
        state = SVCState(
            bus_index=0,
            control_bus_index=0,
            mode="I",
            slope=0.1,
            q_pu=0.0,
            q_min_pu=-1.0,
            q_max_pu=1.0,
            reference_voltage=1.0,
            active=True,
            device_voltage_scale=0.9,
            control_voltage_scale=1.1,
        )

        refresh_svc_limits([state], np.array([0.8]))

        # Capability is B·Vterminal²: the device voltage is 0.8 · 0.9 = 0.72 pu.
        self.assertEqual(state.limit_state, 1)
        self.assertAlmostEqual(state.q_pu, 0.72**2)

    def test_lcc_reactive_power_includes_commutation_overlap(self) -> None:
        tables = {
            "DCLI": [{"I": "10", "J": "20", "O": "A", "R": "10.47"}],
            "DCNV": [
                {
                    "No": "1",
                    "O": "A",
                    "Bac": "11",
                    "Bdc": "10",
                    "Modo": "R",
                    "P": "4",
                    "Xc": "17.8",
                    "Vfs": "127.4",
                    "Snt": "471",
                },
                {
                    "No": "2",
                    "O": "A",
                    "Bac": "22",
                    "Bdc": "20",
                    "Modo": "I",
                    "P": "4",
                    "Xc": "17.2",
                    "Vfs": "122",
                    "Snt": "450",
                },
            ],
            "DCCV": [
                {
                    "No": "1",
                    "O": "A",
                    "F": "N",
                    "TCnv": "P",
                    "Vsp": "1250",
                    "Ang": "15",
                    "Ttr": "1",
                    "Tmn": "0.925",
                    "Tmx": "1.25",
                },
                {
                    "No": "2",
                    "O": "A",
                    "F": "F",
                    "TCnv": "P",
                    "Vsp": "1205",
                    "Ang": "17",
                    "Ttr": "1",
                    "Tmn": "0.925",
                    "Tmx": "1.25",
                },
            ],
            "DCBA": [
                {"No": "10", "O": "A", "V": "600", "Elo": "1"},
                {"No": "20", "O": "A", "V": "578.2", "Elo": "1"},
            ],
            "DELO": [{"No": "1", "O": "A", "V": "600", "Name": "LINK"}],
        }

        link = build_lcc_links(tables, {11: 0.98, 22: 0.97})[0]
        current = 1250.0 / 600.0
        x_ohm = 17.8 * 0.01 * 127.4**2 / 471.0
        argument = np.cos(np.deg2rad(15.0)) - np.sqrt(2.0) * x_ohm * current / (127.4 * 0.98)
        overlap = np.rad2deg(np.arccos(argument)) - 15.0
        expected_q = (1250.0 + current**2 * 10.47) * np.tan(np.deg2rad(15.0 + 0.5 * overlap))
        inverter_x_ohm = 17.2 * 0.01 * 122.0**2 / 450.0
        inverter_argument = np.cos(np.deg2rad(17.0)) - np.sqrt(2.0) * inverter_x_ohm * current / (
            122.0 * 0.97
        )
        inverter_overlap = np.rad2deg(np.arccos(inverter_argument)) - 17.0
        expected_inverter_q = 1250.0 * np.tan(np.deg2rad(17.0 + 0.5 * inverter_overlap))

        self.assertAlmostEqual(link.current_ka, current)
        self.assertAlmostEqual(link.mu_rectifier_deg, overlap)
        self.assertAlmostEqual(link.rectifier_reactive_mvar, expected_q)
        self.assertAlmostEqual(link.mu_inverter_deg, inverter_overlap)
        self.assertAlmostEqual(link.inverter_reactive_mvar, expected_inverter_q)
        self.assertGreater(link.rectifier_reactive_mvar, 1250.0 * np.tan(np.deg2rad(15)))

    def test_lcc_control_update_tracks_ac_voltage_and_bus_injections(self) -> None:
        tables = {
            "DCLI": [{"I": "10", "J": "20", "O": "A", "R": "10.47"}],
            "DCNV": [
                {
                    "No": "1",
                    "O": "A",
                    "Bac": "11",
                    "Bdc": "10",
                    "Modo": "R",
                    "P": "4",
                    "Xc": "17.8",
                    "Vfs": "127.4",
                    "Snt": "471",
                },
                {
                    "No": "2",
                    "O": "A",
                    "Bac": "22",
                    "Bdc": "20",
                    "Modo": "I",
                    "P": "4",
                    "Xc": "17.2",
                    "Vfs": "122",
                    "Snt": "450",
                },
            ],
            "DCCV": [
                {
                    "No": "1",
                    "O": "A",
                    "F": "N",
                    "TCnv": "P",
                    "Vsp": "1250",
                    "Ang": "15",
                    "Ttr": "1",
                    "Tmn": "0.925",
                    "Tmx": "1.25",
                },
                {
                    "No": "2",
                    "O": "A",
                    "F": "F",
                    "TCnv": "P",
                    "Vsp": "1205",
                    "Ang": "17",
                    "Ttr": "1",
                    "Tmn": "0.925",
                    "Tmx": "1.25",
                },
            ],
            "DCBA": [
                {"No": "10", "O": "A", "V": "600", "Elo": "1"},
                {"No": "20", "O": "A", "V": "578.2", "Elo": "1"},
            ],
            "DELO": [{"No": "1", "O": "A", "V": "600", "Name": "LINK"}],
        }
        link = build_lcc_links(tables, {11: 0.98, 22: 0.97})[0]
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(
                    11,
                    "RECT",
                    BusType.PQ,
                    0.98,
                    0.0,
                    0.0,
                    0.0,
                    link.rectifier_active_mw,
                    link.rectifier_reactive_mvar,
                    0.0,
                ),
                Bus(
                    22,
                    "INV",
                    BusType.PQ,
                    0.97,
                    0.0,
                    link.inverter_active_mw,
                    0.0,
                    0.0,
                    link.inverter_reactive_mvar,
                    0.0,
                ),
            ],
            branches=[],
            hvdc_links=[link],
        )

        initial_reactive_power = link.rectifier_reactive_mvar
        changed = update_lcc_from_dc_solution(case, np.array([0.95 + 0j, 0.93 + 0j]))

        # A lower AC voltage widens the overlap angle at once and raises Q.
        self.assertTrue(changed)
        self.assertGreater(link.rectifier_reactive_mvar, initial_reactive_power)
        self.assertAlmostEqual(case.buses[0].q_load_mvar, link.rectifier_reactive_mvar)

        for _ in range(20):
            settled = (link.rectifier_reactive_mvar, link.tap_rectifier, case.buses[0].q_load_mvar)
            if not update_lcc_from_dc_solution(case, np.array([0.95 + 0j, 0.93 + 0j])):
                break
        # A pass within tolerance leaves the state the AC solution used untouched.
        self.assertEqual(
            (link.rectifier_reactive_mvar, link.tap_rectifier, case.buses[0].q_load_mvar),
            settled,
        )


def _dat_tables(dbsh: list[dict[str, str]], dcsc: list[dict[str, str]]) -> dict:
    bus = {
        "Tb": "0",
        "V": "1",
        "A0": "0",
        "Pg": "0",
        "Qg": "0",
        "Qgm": "0",
        "Qgn": "0",
        "Pl": "0",
        "Ql": "0",
        "Sh": "0",
        "Gl": "",
        "E": "L",
        "O": "A",
    }
    line = {
        "Si": "L",
        "Sj": "L",
        "O": "A",
        "E": "L",
        "R": "0",
        "Bshl": "0",
        "Tap": "1",
        "Tmn": "0",
        "Tmx": "0",
        "Psh": "0",
        "Bctrl": "From Bus",
    }
    return {
        "DCTE": [{"Mn": "BASE", "Val": "100"}],
        "DBAR": [
            {**bus, "No": "1", "Tb": "2"},
            {**bus, "No": "2"},
            {**bus, "No": "3"},
        ],
        "DLIN": [{**line, "I": "1", "J": "2", "Nc": "1", "X": "10"}],
        "DBSH": dbsh,
        "DCSC": dcsc,
    }


class CaseConstructionTests(unittest.TestCase):
    def test_dcsc_records_are_series_branches_or_bypass_shorts(self) -> None:
        from pf_tool.utils.utils import build_power_flow_case

        csc = {"O": "A", "E": "L", "Nc": "1", "Xmin": "-5", "Xmax": "-5", "Cdcsc": "0"}
        case = build_power_flow_case(
            _dat_tables(
                [],
                [
                    {**csc, "I": "2", "J": "3", "Byp": "D", "Xv": "-5"},
                    {**csc, "I": "1", "J": "3", "Byp": "L", "Xv": "-5"},
                ],
            )
        )

        reactances = {(b.from_bus, b.to_bus): b.reactance_pu for b in case.branches}
        self.assertAlmostEqual(reactances[(2, 3)], -0.05)
        self.assertLess(abs(reactances[(1, 3)]), 1e-5)

    def test_line_shunt_sits_at_its_terminal_and_needs_its_line(self) -> None:
        from pf_tool.utils.utils import build_power_flow_case

        shunt = {
            "O": "A",
            "Nc": "1",
            "Ctrl": "F",
            "Vmn": "0.9",
            "Vmx": "1.1",
            "Bctrl": "1",
            "TCtrl": "C",
            "Clr": "N",
        }
        case = build_power_flow_case(
            _dat_tables(
                [
                    {**shunt, "I": "1", "J": "2", "Qini": "-50", "Extr": "2"},
                    {**shunt, "I": "1", "J": "3", "Qini": "-30", "Extr": "1"},
                ],
                [],
            )
        )

        susceptance = {bus.number: bus.shunt_susceptance_pu for bus in case.buses}
        self.assertAlmostEqual(susceptance[1], 0.0)
        self.assertAlmostEqual(susceptance[2], -0.5)


class TransformerControlTests(unittest.TestCase):
    @staticmethod
    def _solver(case: PowerFlowCase) -> NewtonRaphsonPowerFlow:
        solver = object.__new__(NewtonRaphsonPowerFlow)
        solver.case = case
        solver.tolerance = 1e-9
        solver.max_iterations = 30
        return solver

    def test_ltc_acts_only_outside_its_voltage_band(self) -> None:
        from pf_tool.models.transformer import build_tap_controls

        branches = [
            Branch(1, 2, 1, 0.0, 0.05, 0.0),
            Branch(2, 3, 1, 0.0, 0.1, 0.0, tap_min=0.8, tap_max=1.2, controlled_bus=3),
        ]
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "HV", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(3, "LV", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 80.0, 40.0, 0.0),
            ],
            branches=branches,
            tap_controls=build_tap_controls(branches, [], {1: 1.0, 2: 1.0, 3: 1.0}, 100.0),
        )

        result = self._solver(case).solve()

        self.assertTrue(result.converged, result.failure_reason)
        # The load drags bus 3 below 0.99 pu, so the LTC holds the band edge.
        self.assertAlmostEqual(abs(result.voltage[2]), 0.99, places=7)
        self.assertLess(case.branches[1].tap_ratio, 1.0)

    def test_phase_shifter_holds_its_scheduled_flow(self) -> None:
        from pf_tool.models.transformer import branch_stamps, build_tap_controls

        branches = [
            Branch(1, 2, 1, 0.0, 0.1, 0.0),
            Branch(1, 2, 2, 0.0, 0.1, 0.0),
        ]
        dctr = [
            {
                "I": "1",
                "J": "2",
                "Nc": "2",
                "O": "A",
                "PCtrl": "P",
                "Vsp": "20",
                "Ext": "1",
                "Fmin": "-30",
                "Fmax": "30",
            }
        ]
        case = PowerFlowCase(
            base_mva=100.0,
            buses=[
                Bus(1, "SLACK", BusType.SLACK, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                Bus(2, "LOAD", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0),
            ],
            branches=branches,
            tap_controls=build_tap_controls(branches, dctr, {1: 1.0, 2: 1.0}, 100.0),
        )

        result = self._solver(case).solve()

        self.assertTrue(result.converged, result.failure_reason)
        shifter = case.branches[1]
        yff, yft, _, _ = branch_stamps(
            1.0 / complex(0.0, 0.1), 0.0, shifter.tap_ratio, shifter.phase_shift_radians
        )
        v_from, v_to = result.voltage
        flow = v_from * np.conj(yff * v_from + yft * v_to)
        self.assertAlmostEqual(flow.real, 0.2, places=7)

    def test_switched_shunt_freezes_instead_of_hunting(self) -> None:
        from pf_tool.models.shunt import ShuntStage, SwitchedShunt, adjust_controlled_shunts

        case = PowerFlowCase(
            base_mva=100.0,
            buses=[Bus(1, "B", BusType.PQ, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)],
            branches=[],
            switched_shunts=[
                SwitchedShunt(
                    bus=1,
                    controlled_bus=1,
                    initial_q_mvar=0.0,
                    minimum_q_mvar=0.0,
                    maximum_q_mvar=200.0,
                    minimum_voltage_pu=1.0,
                    maximum_voltage_pu=1.005,
                    fixed=False,
                    current_q_mvar=0.0,
                    stages=[ShuntStage(unit_mvar=100.0, units=2, in_service=0)],
                )
            ],
        )

        self.assertTrue(adjust_controlled_shunts(case, np.array([0.99 + 0j])))
        self.assertFalse(adjust_controlled_shunts(case, np.array([1.02 + 0j])))
        self.assertFalse(adjust_controlled_shunts(case, np.array([0.99 + 0j])))
        self.assertAlmostEqual(case.buses[0].shunt_susceptance_pu, 1.0)


if __name__ == "__main__":
    unittest.main()
