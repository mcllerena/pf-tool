"""Convert ANAREDE NTW files to the package's block-based DAT format."""

from __future__ import annotations

import csv
import json
import re
import shlex
from collections import defaultdict
from pathlib import Path

from pf_tool.parser._dat import render_dat

_SECTION_END = re.compile(
    r"END OF .*? DATA,\s*BEGIN\s+(?:OF\s+)?(.+?)\s+DATA",
    re.IGNORECASE,
)


class Ntw2DatParser:
    """Parse NTW sections and translate their network data into DAT blocks."""

    def __init__(self, filename: str | Path, base_mva: float | None = None) -> None:
        self.filename = Path(filename)
        self.base_mva = base_mva

    def parse(self) -> str:
        """Parse the source file and return its DAT representation."""
        text = self.filename.read_bytes().decode("cp1252", errors="replace")
        sections, raw_records = self._read_sections(text)
        base_mva = self._read_base_mva(text) if self.base_mva is None else self.base_mva
        if base_mva <= 0:
            raise ValueError(f"Invalid system base power: {base_mva}")

        tables: dict[str, list[dict[str, object]]] = defaultdict(list)
        tables["DCTE"].append({"Mn": "BASE", "Val": base_mva})
        buses = sections.get("BUS", [])
        loads = sections.get("LOAD", [])
        generators = sections.get("GENERATOR", [])
        bus_loads: dict[int, tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
        bus_generators: dict[int, list[list[str]]] = defaultdict(list)
        for row in loads:
            if len(row) >= 5:
                bus = int(self._number(row, 0))
                in_service = self._number(row, 2) != 0
                if in_service:
                    p, q = bus_loads[bus]
                    bus_loads[bus] = (p + self._number(row, 3), q + self._number(row, 4))
                tables["DCAI"].append(
                    {
                        "Bus": row[0],
                        "Id": row[1],
                        "O": "A",
                        "E": "L" if in_service else "D",
                        "Pg": row[3],
                        "Qg": row[4],
                        "Ipg": self._value(row, 5),
                        "Iqg": self._value(row, 6),
                        "Zpg": self._value(row, 7),
                        "Zqg": self._value(row, 8),
                        "Owner": self._value(row, 9),
                        "R0": self._value(row, 10),
                        "X0": self._value(row, 11),
                        "Name": self._value(row, 12),
                    }
                )
        for row in generators:
            if len(row) < 16:
                continue
            bus = int(self._number(row, 0))
            if self._number(row, 12) != 0:
                bus_generators[bus].append(row)
            tables["DGER"].append(
                {
                    "No": row[0],
                    "O": "A" if self._number(row, 12) != 0 else "D",
                    "Pgn": row[15],
                    "Pgm": row[14],
                    "Part": self._value(row, 13),
                    "Fp": self._value(row, 30),
                }
            )

        base_groups = self._base_voltage_groups(buses)
        limit_groups = self._voltage_limit_groups(buses)
        areas: dict[int, dict[str, object]] = {}
        for row in sections.get("AREA", []):
            if len(row) >= 4:
                number = int(self._number(row, 0))
                areas[number] = {
                    "Are": number,
                    "Xchg": self._number(row, 2),
                    "Name": row[3],
                    "Xmin": 0.0,
                    "Xmax": 0.0,
                }

        emitted_base_groups: set[str] = set()
        emitted_limit_groups: set[str] = set()
        for index, row in enumerate(buses):
            if len(row) < 15:
                continue
            number = int(self._number(row, 0))
            area = int(self._number(row, 7, 1))
            gens = bus_generators.get(number, [])
            p_gen = sum(self._number(gen, 2) for gen in gens)
            q_gen = sum(self._number(gen, 3) for gen in gens)
            q_min = sum(self._number(gen, 5) for gen in gens)
            q_max = sum(self._number(gen, 4) for gen in gens)
            raw_type = int(self._number(row, 3))
            bus_type = {3: 2, 2: 1}.get(raw_type, 0)
            p_load, q_load = bus_loads[number]
            voltage_group, base_voltage = base_groups[index]
            limit_group = limit_groups[index]
            controlled_bus = next(
                (
                    int(self._number(generator, 7))
                    for generator in gens
                    if self._number(generator, 7) not in (0, number)
                ),
                "",
            )
            tables["DBAR"].append(
                {
                    "No": number,
                    "Name": row[1],
                    "Tb": bus_type,
                    "Are": area,
                    "V": self._number(row, 9, 1.0),
                    "A0": self._number(row, 10),
                    "Pg": p_gen,
                    "Qg": q_gen,
                    "Qgm": q_max,
                    "Qgn": q_min,
                    "Bctrl": controlled_bus,
                    "Pl": p_load,
                    "Ql": q_load,
                    "Sh": self._number(row, 6),
                    "Gb": voltage_group,
                    "Gl": limit_group,
                    "Vf": 1.0,
                    "M": 0,
                    "E": "L",
                    "O": "A",
                }
            )
            areas.setdefault(
                area,
                {"Are": area, "Xchg": 0.0, "Name": f"Area_{area}", "Xmin": 0.0, "Xmax": 0.0},
            )
            if voltage_group not in emitted_base_groups:
                tables["DGBT"].append({"G": voltage_group, "V": base_voltage})
                emitted_base_groups.add(voltage_group)
            if limit_group not in emitted_limit_groups:
                vmin, vmax = self._number(row, 12, 0.9), self._number(row, 11, 1.1)
                tables["DGLT"].append(
                    {"G": limit_group, "Vmn": vmin, "Vmx": vmax, "Vmne": vmin, "Vmxe": vmax}
                )
                emitted_limit_groups.add(limit_group)

        tables["DARE"].extend(areas[number] for number in sorted(areas))
        self._add_lines(tables, sections.get("TRANSMISSION LINE", []))
        self._add_transformers(tables, sections.get("TRANSFORMER", []))
        self._add_shunts(tables, sections.get("SHUNT", []))
        self._add_series_capacitors(tables, sections.get("SERIES CAPACITOR", []))
        self._add_named_source_tables(tables, sections)
        tables["DNTW_RAW"].extend(raw_records)
        return render_dat(tables, {"BASE": base_mva})

    def convert(self, output_path: str | Path | None = None) -> Path:
        """Write the parsed DAT representation and return its path."""
        destination = (
            Path(output_path) if output_path is not None else self.filename.with_suffix(".dat")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.parse(), encoding="utf-8")
        return destination

    @staticmethod
    def _read_sections(text: str) -> tuple[dict[str, list[list[str]]], list[dict[str, object]]]:
        sections: dict[str, list[list[str]]] = defaultdict(list)
        raw_records: list[dict[str, object]] = []
        current = "BUS"
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = _SECTION_END.search(line)
            if match:
                current = Ntw2DatParser._normalize_section(match.group(1))
                continue
            if re.search(r"\bEND OF .* DATA\b", line, re.IGNORECASE):
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith(("(", "!")):
                continue
            values = Ntw2DatParser._tokens(line)
            if not values or not Ntw2DatParser._is_number(values[0]):
                continue
            if current == "BUS" and len(values) < 15:
                continue
            sections[current].append(values)
            raw_records.append(
                {
                    "Section": current,
                    "Line": line_number,
                    "Values": json.dumps(values, ensure_ascii=True, separators=(",", ":")),
                }
            )
        return sections, raw_records

    @staticmethod
    def _normalize_section(value: str) -> str:
        section = re.sub(r"\s+", " ", value.strip().upper()).removeprefix("OF ")
        return {
            "SWITCHED SHUNT": "SHUNT",
            "TRANSFORMER IMPEDANCE CORRECTION": "IMPEDANCE CORRECTION",
        }.get(section, section)

    @staticmethod
    def _tokens(line: str) -> list[str]:
        if "," in line:
            values = next(csv.reader([line], skipinitialspace=True))
        else:
            values = shlex.split(line, comments=False, posix=True)
        return [value.strip().rstrip("/").strip().strip("'").strip() for value in values]

    @staticmethod
    def _is_number(value: str) -> bool:
        try:
            float(value.replace("D", "E").replace("d", "e"))
            return True
        except ValueError:
            return False

    @staticmethod
    def _number(row: list[str], index: int, default: float = 0.0) -> float:
        if index >= len(row) or not row[index]:
            return default
        try:
            return float(row[index].replace("D", "E").replace("d", "e"))
        except ValueError:
            return default

    @staticmethod
    def _value(row: list[str], index: int, default: str = "") -> str:
        return row[index] if index < len(row) else default

    @classmethod
    def _read_base_mva(cls, text: str) -> float:
        lines = text.splitlines()
        if len(lines) < 2:
            return 100.0
        values = re.findall(r"[-+]?\d+(?:\.\d+)?", lines[1])
        return float(values[-1]) if values else 100.0

    @classmethod
    def _base_voltage_groups(cls, buses: list[list[str]]) -> list[tuple[str, float]]:
        values = sorted({cls._number(row, 2) for row in buses if len(row) > 2})
        groups = {value: str(index + 1) for index, value in enumerate(values)}
        return [(groups.get(cls._number(row, 2), "1"), cls._number(row, 2)) for row in buses]

    @classmethod
    def _voltage_limit_groups(cls, buses: list[list[str]]) -> list[str]:
        pairs = sorted(
            {
                (cls._number(row, 12, 0.9), cls._number(row, 11, 1.1))
                for row in buses
                if len(row) > 12
            }
        )
        groups = {pair: str(index + 1) for index, pair in enumerate(pairs)}
        return [
            groups.get((cls._number(row, 12, 0.9), cls._number(row, 11, 1.1)), "1") for row in buses
        ]

    @classmethod
    def _add_lines(cls, tables: dict[str, list[dict[str, object]]], rows: list[list[str]]) -> None:
        for row in rows:
            if len(row) < 7:
                continue
            tables["DLIN"].append(
                {
                    "I": row[0],
                    "J": row[1],
                    "Nc": row[2],
                    "Si": "L" if cls._number(row, 9, 1) else "D",
                    "Sj": "L" if cls._number(row, 10, 1) else "D",
                    "O": "A",
                    "E": "L",
                    "Prop": "N",
                    "R": cls._number(row, 3) * 100,
                    "X": cls._number(row, 4) * 100,
                    "Bshl": cls._number(row, 5),
                    "Tap": 1.0,
                    "Tmn": 0.0,
                    "Tmx": 0.0,
                    "Psh": 0.0,
                    "Bctrl": row[0],
                    "Cn": cls._number(row, 6, 9999),
                    "Ce": cls._number(row, 7, cls._number(row, 6, 9999)),
                    "Ns": 0,
                    "Cq": cls._number(row, 8, cls._number(row, 6, 9999)),
                }
            )

    @classmethod
    def _add_transformers(
        cls, tables: dict[str, list[dict[str, object]]], rows: list[list[str]]
    ) -> None:
        index = 0
        while index < len(rows):
            row = rows[index]
            if len(row) < 14:
                index += 1
                continue
            continuations: list[list[str]] = []
            cursor = index + 1
            while cursor < len(rows) and len(rows[cursor]) >= 20:
                continuations.append(rows[cursor])
                cursor += 1
            if continuations and int(cls._number(row, 2)) == 0:
                continuation = continuations[0]
                tables["DLIN"].append(
                    {
                        "I": row[0],
                        "J": row[1],
                        "Nc": row[12],
                        "Si": "L" if cls._number(row, 6, 1) else "D",
                        "Sj": "L" if cls._number(row, 7, 1) else "D",
                        "O": "A",
                        "E": "L",
                        "Prop": "N",
                        "R": cls._number(continuation, 1) * 100,
                        "X": cls._number(continuation, 2) * 100,
                        "Bshl": 0.0,
                        "Tap": cls._number(continuation, 3, 1.0),
                        "Tmn": 0.0,
                        "Tmx": 0.0,
                        "Psh": cls._number(continuation, 4),
                        "Bctrl": row[1],
                        "Cn": cls._number(continuation, 5, 9999),
                        "Ce": cls._number(continuation, 6, cls._number(continuation, 5, 9999)),
                        "Ns": 0,
                        "Cq": cls._number(continuation, 7, cls._number(continuation, 5, 9999)),
                    }
                )
            tables["DNTW_TRANSFORMER"].append(
                {
                    "FromBus": row[0],
                    "ToBus": row[1],
                    "ThirdBus": row[2],
                    "Circuit": row[3],
                    "Primary": json.dumps(row, ensure_ascii=True, separators=(",", ":")),
                    "Continuations": json.dumps(
                        continuations, ensure_ascii=True, separators=(",", ":")
                    ),
                }
            )
            index = cursor

    @classmethod
    def _add_shunts(cls, tables: dict[str, list[dict[str, object]]], rows: list[list[str]]) -> None:
        for parent, row in enumerate(rows, start=1):
            if len(row) < 6:
                continue
            tables["DBSH"].append(
                {
                    "I": row[0],
                    "O": "A",
                    "J": "",
                    "Nc": 1,
                    "Ctrl": "F" if cls._number(row, 1) == 0 else "D",
                    "Vmn": cls._number(row, 3, 0.9),
                    "Vmx": cls._number(row, 2, 1.1),
                    "Bctrl": cls._value(row, 4, row[0]),
                    "Qini": cls._number(row, 5) if cls._number(row, 6, 1) else 0.0,
                    "TCtrl": "C",
                    "Clr": "N",
                    "Extr": row[0],
                }
            )
            for stage in range(3):
                offset = 7 + stage * 4
                if len(row) <= offset + 2:
                    continue
                tables["DBSH_BANK"].append(
                    {
                        "Parent": parent,
                        "G": stage + 1,
                        "O": "A",
                        "E": "L",
                        "U": cls._number(row, offset + 1),
                        "UOp": cls._number(row, offset + 1) if cls._number(row, offset) else 0,
                        "Sht": cls._number(row, offset + 2),
                    }
                )

    @classmethod
    def _add_series_capacitors(
        cls, tables: dict[str, list[dict[str, object]]], rows: list[list[str]]
    ) -> None:
        for row in rows:
            if len(row) < 18:
                continue
            tables["DCSC"].append(
                {
                    "I": row[0],
                    "O": "A",
                    "J": row[1],
                    "Nc": row[2],
                    "E": "L" if cls._number(row, 14, 1) and cls._number(row, 15, 1) else "D",
                    "Byp": "D",
                    "Xmin": cls._number(row, 4) * 100,
                    "Xmax": cls._number(row, 4) * 100,
                    "Xv": cls._number(row, 4) * 100,
                    "Ctrl": "X",
                    "Vsp": 0.0,
                    "Ext": row[0],
                    "Nst": 0,
                    "Cdcsc": cls._number(row, 5),
                }
            )

    @staticmethod
    def _add_named_source_tables(
        tables: dict[str, list[dict[str, object]]], sections: dict[str, list[list[str]]]
    ) -> None:
        names = {
            "AREA": "DNTW_AREA",
            "DCLINK": "DNTW_DCLINK",
            "VSC": "DNTW_VSC",
            "ZONE": "DNTW_ZONE",
            "OWNER": "DNTW_OWNER",
            "SUBSTATION": "DNTW_SUBSTATION",
            "IMPEDANCE CORRECTION": "DNTW_IMPEDANCE_CORRECTION",
            "LINE MUTUAL IMPEDANCE": "DNTW_LINE_MUTUAL_IMPEDANCE",
            "INDUCTION MOTOR": "DNTW_INDUCTION_MOTOR",
            "BREAKER CONFIGURATION": "DNTW_BREAKER_CONFIGURATION",
            "FACTS": "DNTW_FACTS",
        }
        for section, block in names.items():
            for row in sections.get(section, []):
                tables[block].append(
                    {f"Field{index + 1}": value for index, value in enumerate(row)}
                )
