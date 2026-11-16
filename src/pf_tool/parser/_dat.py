"""Helpers for writing the package's block-based ``.dat`` format."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence


def render_dat(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
    parameters: Mapping[str, object] | None = None,
) -> str:
    """Render ordered records as AMPL-style parameter blocks."""
    sections: list[str] = []
    for name, value in (parameters or {}).items():
        sections.append(f"param {name} := {shlex.quote(str(value))};")

    for name, records in tables.items():
        if not records:
            continue
        columns = list(dict.fromkeys(column for record in records for column in record))
        if not columns:
            continue
        rows = [
            " ".join(shlex.quote(str(record.get(column, ""))) for column in columns)
            for record in records
        ]
        sections.append(
            f"param: {name}: {' '.join(columns)} :=\n"
            + "\n".join(rows)
            + "\n;"
        )
    return "\n\n".join(sections) + "\n"
