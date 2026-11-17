from pathlib import Path

from pf_tool import NewtonRaphsonPowerFlow
from pf_tool.parser import Ntw2DatParser, Pwf2DatParser
from pf_tool.utils.dat_parser import parse_dat_file
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


def test_pwf_conversion_handles_compact_fixed_width_records(tmp_path: Path) -> None:
    source = DATA / "pwf" / "d_9nodes.pwf"

    output = Pwf2DatParser(source).convert(tmp_path / "9nodes.dat")
    rendered = output.read_text(encoding="utf-8")
    tables = parse_dat_file(output)
    case = build_power_flow_case(tables)

    assert rendered.startswith("# Generated from d_9nodes.pwf\n\n")
    assert "param: DOPC: Op      E   :=" in rendered
    assert '             "QLIM"  "L"' in rendered
    assert len(case.buses) == 9
    assert len(case.branches) == 10
    assert case.buses[0].number == 1
    assert case.buses[0].voltage_pu == 1.0
    assert NewtonRaphsonPowerFlow(filename=str(output)).solve().converged


def test_pwf_conversion_decodes_utf8_titles(tmp_path: Path) -> None:
    source = DATA / "pwf" / "d_9nodes.pwf"
    lines = source.read_text(encoding="ascii").splitlines()
    title = "Sistema-Teste de 9 Barras - Ver\u00e3o"
    lines[1] = title
    text = "\n".join(lines) + "\n"
    utf8_source = tmp_path / "utf8-title.pwf"
    utf8_source.write_text(text, encoding="utf-8")

    rendered = Pwf2DatParser(utf8_source).parse()

    assert f'"{title}"' in rendered
