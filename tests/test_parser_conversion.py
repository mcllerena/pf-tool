from pathlib import Path

from pf_tool import NewtonRaphsonPowerFlow
from pf_tool.parser import Ntw2DatParser, Pwf2DatParser
from pf_tool.utils.dat_parser import parse_dat_file, parse_dat_text
from pf_tool.utils.utils import build_power_flow_case

DATA = Path(__file__).parent / "data"


def test_ntw_conversion_builds_a_complete_ac_case(tmp_path: Path) -> None:
    output = Ntw2DatParser(DATA / "ntw" / "9bus.ntw").convert(tmp_path / "9bus.dat")

    tables = parse_dat_file(output)
    case = build_power_flow_case(tables)

    assert len(case.buses) == 9
    assert len(case.branches) == 9
    assert len(tables["DCAI"]) == 3
    assert len(tables["DGER"]) == 3
    assert tables["DCTE"][0]["Val"] == "100.0"
    assert len(tables["DNTW_TRANSFORMER"]) == 3
    assert {row["Section"] for row in tables["DNTW_RAW"]} >= {
        "BUS",
        "LOAD",
        "GENERATOR",
        "TRANSMISSION LINE",
        "TRANSFORMER",
    }
    assert NewtonRaphsonPowerFlow(filename=str(output)).solve().converged


def test_pwf_conversion_builds_svc_and_csc_case(tmp_path: Path) -> None:
    source = DATA / "pwf" / "d_33nodes_dcer_dcsc.pwf"
    output = Pwf2DatParser(source).convert(tmp_path / "case.dat")

    tables = parse_dat_file(output)
    case = build_power_flow_case(tables)

    assert len(case.buses) == 33
    assert len(case.svcs) == 8
    assert len(tables["DCSC"]) == 1
    assert any(branch.is_csc for branch in case.branches)


def test_pwf_conversion_handles_compact_fixed_width_records(tmp_path: Path) -> None:
    source = DATA / "pwf" / "d_9nodes.pwf"

    output = Pwf2DatParser(source).convert(tmp_path / "9nodes.dat")
    tables = parse_dat_file(output)
    case = build_power_flow_case(tables)

    assert len(case.buses) == 9
    assert len(case.branches) == 10
    assert case.buses[0].number == 1
    assert case.buses[0].voltage_pu == 1.0
    assert NewtonRaphsonPowerFlow(filename=str(output)).solve().converged


def test_large_ntw_conversion_preserves_shunts_and_series_devices() -> None:
    source = DATA / "ntw" / "CASO01.NTW"

    tables = parse_dat_text(Ntw2DatParser(source).parse())
    case = build_power_flow_case(tables)

    assert len(case.buses) == 10_730
    assert len(tables["DBSH_BANK"]) > 0
    assert len(tables["DCSC"]) > 0
    assert len(tables["DNTW_DCLINK"]) > 0


def test_large_pwf_conversion_preserves_lcc_and_shunt_bank_data() -> None:
    source = DATA / "pwf" / "20240820_C_00-00.pwf"

    tables = parse_dat_text(Pwf2DatParser(source).parse())
    case = build_power_flow_case(tables)

    assert len(case.buses) > 10_000
    assert len(case.svcs) == 44
    assert len(case.switched_shunts) > 0
    assert len(case.hvdc_links) == 14
    assert len(tables["DBSH_BANK"]) > 0
