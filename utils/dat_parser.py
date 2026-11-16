"""Parser for the AMPL-style, block-based ANAREDE ``.dat`` exports."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_HEADER = re.compile(
    r"^param:\s*(?P<name>[^:\s]+)\s*:\s*(?P<columns>.*?)\s*:=",
    re.MULTILINE,
)


def parse_dat_file(path: str | Path) -> dict[str, list[dict[str, str]]]:
    """Read a power-system data file and return records grouped by block."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = source.read_text(encoding="latin-1")
    return parse_dat_text(text)


def parse_dat_text(text: str) -> dict[str, list[dict[str, str]]]:
    """Parse repeated ``param: BLOCK: columns :=`` sections into row mappings."""
    matches = list(_HEADER.finditer(text))
    if not matches:
        raise ValueError("No AMPL-style 'param: BLOCK: ... :=' data blocks found")

    tables: dict[str, list[dict[str, str]]] = {}
    schemas: dict[str, list[str]] = {}
    for match in matches:
        name = match.group("name").upper()
        columns = shlex.split(match.group("columns"))
        if not columns:
            raise ValueError(f"Data block {name} has no column names")
        if name in schemas and schemas[name] != columns:
            raise ValueError(f"Repeated data block {name} has inconsistent columns")
        schemas[name] = columns

        terminator = text.find(";", match.end())
        next_header = _HEADER.search(text, match.end())
        if terminator < 0 or (next_header is not None and next_header.start() < terminator):
            raise ValueError(f"Data block {name} is missing its ';' terminator")
        tokens = shlex.split(text[match.end() : terminator])
        if len(tokens) % len(columns):
            raise ValueError(
                f"Data block {name} has {len(tokens)} values for {len(columns)} columns"
            )
        records = tables.setdefault(name, [])
        width = len(columns)
        records.extend(
            dict(zip(columns, tokens[offset : offset + width]))
            for offset in range(0, len(tokens), width)
        )
    return tables
