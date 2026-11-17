"""Convert ANAREDE PWF files to the package's block-based DAT format."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from pf_tool.parser._dat import render_dat

_DEFAULT_MAPPING = Path(__file__).parents[1] / "config" / "anarede_mapping.json"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?$")
_PACKED_DBAR = re.compile(r"([+-]?\d{3,4})([+-](?:\d+(?:\.\d*)?|\.\d+))")

_ALIASES: dict[str, dict[str, str]] = {
    "DARE": {
        "area_number": "Are",
        "net_interchange": "Xchg",
        "name": "Name",
        "minimum_net_interchange": "Xmin",
        "maximum_net_interchange": "Xmax",
    },
    "DBAR": {
        "number": "No",
        "operation": "O",
        "state": "E",
        "type": "Tb",
        "voltage_base_group": "Gb",
        "name": "Name",
        "voltage_limit_group": "Gl",
        "voltage": "V",
        "angle": "A0",
        "active_generation": "Pg",
        "reactive_generation": "Qg",
        "min_reactive_generation": "Qgn",
        "max_reactive_generation": "Qgm",
        "controlled_bus": "Bctrl",
        "active_load": "Pl",
        "reactive_load": "Ql",
        "capacitor_reactor": "Sh",
        "area": "Are",
        "voltage_for_load_definition": "Vf",
        "visualization_mode": "M",
    },
    "DBSH": {
        "from_bus": "I",
        "operation": "O",
        "to_bus": "J",
        "dbsh_circuit": "Nc",
        "control_mode": "Ctrl",
        "minimum_voltage": "Vmn",
        "maximum_voltage": "Vmx",
        "controlled_bus": "Bctrl",
        "initial_reactive_injection": "Qini",
        "control_type": "TCtrl",
        "clear_dbar_data": "Clr",
        "extremity_bus": "Extr",
    },
    "DCAI": {
        "bus": "Bus",
        "operation": "O",
        "group": "G",
        "units": "Un",
        "active_power": "Pg",
        "reactive_power": "Qg",
        "voltage_limit": "Vlim",
        "voltage_for_load_definition": "Vf",
    },
    "DCBA": {
        "number": "No",
        "operation": "O",
        "type": "Tb",
        "polarity": "Pol",
        "name": "Name",
        "voltage_limit_group": "Gl",
        "voltage": "V",
        "ground_electrode_resistance": "Rs",
        "dc_link_number": "Elo",
    },
    "DCCV": {
        "number": "No",
        "operation": "O",
        "slack": "F",
        "inverter_control_mode": "MInv",
        "converter_control_type": "TCnv",
        "specified_value": "Vsp",
        "current_margin": "Marg",
        "maximum_overcurrent": "Imax",
        "converter_angle": "Ang",
        "minimum_converter_angle": "AngMn",
        "maximum_converter_angle": "AngMx",
        "minimum_transformer_tap": "Tmn",
        "maximum_transformer_tap": "Tmx",
        "transformer_tap_steps": "S",
        "dc_voltage_minimum_for_power_control": "Vmn",
        "tap_himvar_mode": "Tmh",
        "tap_reduced_voltage_mode": "Ttr",
    },
    "DCER": {
        "bus": "Bus",
        "operation": "O",
        "group": "G",
        "units": "Un",
        "controlled_bus": "Bctrl",
        "slope": "Incl",
        "reactive_generation": "Qg",
        "min_reactive_generation": "Qgn",
        "max_reactive_generation": "Qgm",
        "control_mode": "Ctrl",
        "state": "E",
    },
    "DCLI": {
        "from_bus": "I",
        "operation": "O",
        "to_bus": "J",
        "dcli_circuit": "Nc",
        "resistance": "R",
        "inductance": "L",
        "capacity": "Cap",
    },
    "DCNV": {
        "number": "No",
        "operation": "O",
        "ac_bus": "Bac",
        "dc_bus": "Bdc",
        "neutral_bus": "Bneu",
        "mode": "Modo",
        "current": "Inom",
        "commutation_reactance": "Xc",
        "secondary_voltage": "Vfs",
        "transformer_power": "Snt",
        "reactor_resistance": "Rra",
        "reactor_inductance": "Lra",
        "ccc_capacitance": "CCC",
        "frequency": "Fr",
        "six_pulse_bridges": "P",
    },
    "DCSC": {
        "from_bus": "I",
        "operation": "O",
        "to_bus": "J",
        "dcsc_circuit": "Nc",
        "state": "E",
        "bypass": "Byp",
        "min_reactance": "Xmin",
        "max_reactance": "Xmax",
        "initial_reactance": "Xv",
        "control_mode": "Ctrl",
        "specified_value": "Vsp",
        "measurement_terminal": "Ext",
        "number_of_stages": "Nst",
        "dcsc_capacity": "Cdcsc",
    },
    "DCTE": {"mnemonic": "Mn", "value": "Val"},
    "DCTR": {
        "from_bus": "I",
        "operation": "O",
        "to_bus": "J",
        "dctr_circuit": "Nc",
        "minimum_voltage": "Vmn",
        "maximum_voltage": "Vmx",
        "voltage_control_type": "VCtrl",
        "phase_mode": "M",
        "minimum_phase_shift": "Fmin",
        "maximum_phase_shift": "Fmax",
        "phase_control_type": "PCtrl",
        "specified_value": "Vsp",
        "measurement_terminal": "Ext",
        "number_of_taps": "Ns",
        "curve_id": "IdCurvaPts",
    },
    "DGBT": {"group": "G", "voltage": "V"},
    "DGEI": {
        "bus": "Bus",
        "operation": "O",
        "group": "G",
        "units": "Un",
        "units_in_operation": "UOp",
        "minimum_units_in_operation": "UMin",
        "active_generation": "Pg",
        "reactive_generation": "Qg",
        "minimum_reactive_generation": "Qgn",
        "maximum_reactive_generation": "Qgm",
        "step_up_transformer_reactance": "Xtrf",
        "direct_axis_reactance": "Xd",
        "quadrature_axis_reactance": "Xq",
        "leakage_reactance": "Xl",
        "nominal_power_factor": "FP",
        "nominal_apparent_power": "Snom",
        "mechanical_limit": "Pmax",
        "minimum_power": "Pmin",
        "voltage_droop": "Droop",
        "automatic_mode": "Auto",
        "state": "E",
    },
    "DGER": {
        "number": "No",
        "operation": "O",
        "min_active_generation": "Pgn",
        "max_active_generation": "Pgm",
        "participation_factor": "Part",
        "remote_control_participation_factor": "RPart",
        "nominal_power_factor": "Fp",
        "armature_service_factor": "armature_service_factor",
        "rotor_service_factor": "FRot",
        "load_angle": "Dmax",
        "machine_reactance": "Xmaq",
        "nominal_apparent_power": "Snom",
    },
    "DGLT": {
        "group": "G",
        "minimum_voltage_limit": "Vmn",
        "maximum_voltage_limit": "Vmx",
        "emergency_minimum_voltage_limit": "Vmne",
        "emergency_maximum_voltage_limit": "Vmxe",
    },
    "DLIN": {
        "from_bus": "I",
        "from_bus_opening": "Si",
        "to_bus": "J",
        "to_bus_opening": "Sj",
        "dlin_circuit": "Nc",
        "maneuverable": "Prop",
        "operation": "O",
        "state": "E",
        "resistance": "R",
        "reactance": "X",
        "susceptance": "Bshl",
        "tap": "Tap",
        "tap_minimum": "Tmn",
        "tap_maximum": "Tmx",
        "phase_shift": "Psh",
        "controlled_bus": "Bctrl",
        "normal_capacity": "Cn",
        "emergency_capacity": "Ce",
        "number_of_taps": "Ns",
        "equipment_capacity": "Cq",
    },
    "DELO": {
        "number": "No",
        "operation": "O",
        "voltage": "V",
        "power_base": "Pbase",
        "name": "Name",
        "himvar_mode": "M",
        "state": "E",
    },
    "DSHL": {
        "from_bus": "I",
        "operation": "O",
        "to_bus": "J",
        "dshl_circuit": "Nc",
        "shunt_from": "BshI",
        "shunt_to": "BshJ",
        "state_from": "Si",
        "state_to": "Sj",
    },
    "TITU": {"title_line": "title_line"},
}

_DEFAULT_BLOCKS = frozenset(
    {
        "DARE",
        "DBAR",
        "DBSH",
        "DCAI",
        "DCBA",
        "DCCV",
        "DCER",
        "DCLI",
        "DCNV",
        "DCSC",
        "DCTE",
        "DCTR",
        "DGBT",
        "DGEI",
        "DGER",
        "DGLT",
        "DLIN",
        "DMTE",
        "DTPF",
        "DELO",
        "DSHL",
        "TITU",
        "DMFL_CIRC",
        "DTPF_CIRC",
        "DOPC",
    }
)


class Pwf2DatParser:
    """Parse a PWF file using the configured field map and write a DAT file."""

    def __init__(
        self,
        filename: str | Path,
        mapping_path: str | Path = _DEFAULT_MAPPING,
    ) -> None:
        self.filename = Path(filename)
        self.mapping_path = Path(mapping_path)
        raw_mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        self.mapping = {key: value for key, value in raw_mapping.items() if not key.startswith("_")}
        self._source_encoding = "utf-8"

    def parse(self) -> str:
        """Parse the source file and return its DAT representation."""
        source = self.filename.read_bytes()
        try:
            source.decode("utf-8-sig")
            self._source_encoding = "utf-8"
        except UnicodeDecodeError:
            self._source_encoding = "cp1252"
        tables: dict[str, list[dict[str, object]]] = defaultdict(list)
        dbar_lines: list[bytes] = []
        active: str | None = None
        dbsh_parent: int | None = None
        dbsh_bank_section = False
        base_mva = 100.0

        for line_number, source_line in enumerate(source.splitlines()):
            encoding = (
                "utf-8-sig"
                if line_number == 0 and self._source_encoding == "utf-8"
                else self._source_encoding
            )
            line = source_line.decode(encoding, errors="replace")
            stripped = line.strip()
            if not stripped:
                continue
            block = self._section_name(stripped)
            if block is not None:
                active = block
                dbsh_parent = None
                dbsh_bank_section = False
                continue

            if active is None:
                continue
            upper = stripped.upper()
            if upper == "FIM":
                break
            if upper.startswith("99999"):
                active = None
                dbsh_parent = None
                dbsh_bank_section = False
                continue
            if active == "DBSH" and upper == "FBAN":
                dbsh_parent = None
                dbsh_bank_section = False
                continue
            if stripped.startswith("("):
                if active == "DBSH":
                    dbsh_bank_section = stripped.lstrip("(").strip().upper().startswith("G")
                continue

            if active in {"DOPC", "DCTE"}:
                records = self._parse_pairs(active, stripped)
                tables[active].extend(records)
                if active == "DCTE":
                    for record in records:
                        if record["Mn"].upper() == "BASE":
                            base_mva = float(record["Val"])
                continue

            if active not in self.mapping:
                continue
            if active == "DBSH" and dbsh_bank_section and dbsh_parent is not None:
                bank_fields = self.mapping["DBSH"].get("bank_fields", {})
                bank = self._parse_fixed(source_line, bank_fields)
                tables["DBSH_BANK"].append(
                    {
                        "Parent": dbsh_parent,
                        "G": self._value(bank, "bank_number", 0),
                        "O": self._value(bank, "operation", "A"),
                        "E": self._value(bank, "state", "L"),
                        "U": self._value(bank, "total_units", 0),
                        "UOp": self._value(bank, "units_in_operation", 0),
                        "Sht": self._value(bank, "reactive_power_per_unit", 0),
                    }
                )
                continue

            values = self._parse_fixed(source_line, self.mapping[active].get("fields", {}))
            if active == "DBAR":
                dbar_lines.append(source_line)
            if active == "DGEI":
                record = self._alias_record(active, values)
                tables[active].append(record)
                units = self._number(values.get("units_in_operation"))
                if units == 0:
                    units = self._number(values.get("units"), 1)
                tables["DGER"].append(
                    {
                        "No": self._value(values, "bus", 0),
                        "O": "D" if str(values.get("state", "")).strip().upper() == "D" else "A",
                        "Pgn": self._number(values.get("minimum_power")) * units,
                        "Pgm": self._number(values.get("mechanical_limit")) * units,
                    }
                )
                continue

            record = self._alias_record(active, values)
            if active == "DBAR":
                for field in ("V", "A0", "Pg", "Qg", "Qgm", "Qgn", "Pl", "Ql", "Sh"):
                    if record.get(field) in (None, ""):
                        record[field] = 0.0
            tables[active].append(record)
            if active == "DBSH":
                dbsh_parent = len(tables["DBSH"])

        voltage_groups = {
            str(record.get("G", "")).strip().upper()
            for record in tables.get("DGLT", [])
            if str(record.get("G", "")).strip()
        }
        for line, record in zip(dbar_lines, tables["DBAR"], strict=True):
            values = self._parse_fixed(line, self.mapping["DBAR"]["fields"])
            repaired = self._repair_dbar(line, values, voltage_groups)
            record["Gl"] = repaired["voltage_limit_group"]
            record["V"] = repaired["voltage"]
            record["A0"] = repaired["angle"]

        if not any(record["Mn"].upper() == "BASE" for record in tables["DCTE"]):
            tables["DCTE"].insert(0, {"Mn": "BASE", "Val": base_mva})
        return f"# Generated from {self.filename.name}\n\n" + render_dat(tables, {"BASE": base_mva})

    def convert(self, output_path: str | Path | None = None) -> Path:
        """Write the parsed DAT representation and return its path."""
        destination = (
            Path(output_path) if output_path is not None else self.filename.with_suffix(".dat")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.parse(), encoding="utf-8")
        return destination

    @staticmethod
    def _section_name(line: str) -> str | None:
        upper = line.upper()
        if upper in _DEFAULT_BLOCKS:
            return upper
        tokens = upper.split()
        if len(tokens) == 2 and f"{tokens[0]}_{tokens[1]}" in _DEFAULT_BLOCKS:
            return f"{tokens[0]}_{tokens[1]}"
        if tokens and tokens[0] == "DOPC":
            return tokens[0]
        return None

    def _parse_fixed(self, line: str | bytes, fields: dict[str, Any]) -> dict[str, object]:
        values: dict[str, object] = {}
        for name, spec in fields.items():
            column = spec.get("column", {})
            start = max(0, int(column.get("start", 1)) - 1)
            end = int(column.get("end", start + 1))
            raw_value = line[start:end]
            raw = (
                raw_value.decode(self._source_encoding, errors="replace").strip()
                if isinstance(raw_value, bytes)
                else raw_value.strip()
            )
            if not raw:
                value = spec.get("default", "")
            elif _NUMBER.fullmatch(raw):
                number = float(raw.replace("D", "E").replace("d", "e"))
                match = re.search(
                    r"Implicit decimal point between columns (\d+) and (\d+)",
                    spec.get("description", ""),
                    re.IGNORECASE,
                )
                if match and "." not in raw and "e" not in raw.lower():
                    places = max(0, end - int(match.group(1)))
                    number /= 10**places
                value = int(number) if number.is_integer() and "." not in raw else number
            else:
                value = raw.replace("\ufffd", "?")
            values[name] = value
        return values

    @staticmethod
    def _parse_pairs(block: str, line: str) -> list[dict[str, object]]:
        tokens = line.split()
        left, right = ("Op", "E") if block == "DOPC" else ("Mn", "Val")
        rows: list[dict[str, object]] = []
        for offset in range(0, len(tokens) - 1, 2):
            value: object = tokens[offset + 1]
            if block == "DCTE" and _NUMBER.fullmatch(str(value)):
                value = float(str(value).replace("D", "E").replace("d", "e"))
            rows.append({left: tokens[offset], right: value})
        return rows

    @staticmethod
    def _alias_record(block: str, values: dict[str, object]) -> dict[str, object]:
        aliases = _ALIASES.get(block, {})
        record = {aliases.get(key, key): value for key, value in values.items()}
        if "O" in record and record["O"] in (None, ""):
            record["O"] = "A"
        if "E" in record and record["E"] in (None, ""):
            record["E"] = "L"
        if block == "DBSH":
            record["Bctrl"] = Pwf2DatParser._terminal_bus(
                record.get("Bctrl"), record.get("I"), record.get("J")
            )
        if block == "DCTR":
            record["Ext"] = Pwf2DatParser._terminal_bus(
                record.get("Ext"), record.get("I"), record.get("J")
            )
        if block == "DCCV":
            for field in ("Tmh", "Ttr"):
                if not Pwf2DatParser._is_number(record.get(field)):
                    record[field] = 1.0
        return record

    @staticmethod
    def _terminal_bus(value: object, from_bus: object, to_bus: object) -> object:
        terminal = str(value).strip().upper()
        if terminal in {"", "FROM BUS"}:
            return from_bus
        if terminal == "TO BUS":
            return to_bus
        return value

    @staticmethod
    def _is_number(value: object) -> bool:
        return value is not None and _NUMBER.fullmatch(str(value)) is not None

    @staticmethod
    def _repair_dbar(
        line: str | bytes,
        values: dict[str, object],
        voltage_groups: set[str],
    ) -> dict[str, object]:
        if isinstance(line, bytes):
            line = line.decode("latin-1")
        group = str(values.get("voltage_limit_group", "")).strip().upper()
        if group in voltage_groups:
            return values

        match = _PACKED_DBAR.fullmatch(line[22:30].strip())
        if match is not None:
            voltage = int(match.group(1)) / 1000
            angle = float(match.group(2))
        else:
            current_voltage = values.get("voltage")
            try:
                parsed_voltage = float(str(current_voltage).replace("D", "E").replace("d", "e"))
            except ValueError:
                parsed_voltage = float("nan")
            if 0.4 <= parsed_voltage <= 2.0:
                return values

            raw_voltage = line[22:26].strip()
            raw_angle = line[26:30].strip()
            if not _NUMBER.fullmatch(raw_voltage) or not _NUMBER.fullmatch(raw_angle):
                return values
            voltage = float(raw_voltage.replace("D", "E").replace("d", "e"))
            if "." not in raw_voltage and "e" not in raw_voltage.lower():
                voltage /= 1000
            angle = float(raw_angle.replace("D", "E").replace("d", "e"))

        if not 0.4 <= voltage <= 2.0 or abs(angle) > 360:
            return values

        repaired = dict(values)
        repaired["voltage_limit_group"] = ""
        repaired["voltage"] = voltage
        repaired["angle"] = angle
        return repaired

    @staticmethod
    def _value(values: dict[str, object], key: str, default: object) -> object:
        value = values.get(key)
        return default if value in (None, "") else value

    @staticmethod
    def _number(value: object, default: float = 0.0) -> float:
        if value in (None, ""):
            return default
        try:
            return float(str(value).replace("D", "E").replace("d", "e"))
        except ValueError:
            return default
