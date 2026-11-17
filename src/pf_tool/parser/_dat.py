"""Helpers for writing the package's block-based ``.dat`` format."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _format_value(value: object) -> str:
    if not isinstance(value, str):
        return str(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_dat(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
    parameters: Mapping[str, object] | None = None,
) -> str:
    """Render ordered records as AMPL-style parameter blocks."""
    sections: list[str] = []
    for name, value in (parameters or {}).items():
        sections.append(f"param {name} := {_format_value(value)};")

    for name, records in tables.items():
        if not records:
            continue
        columns = list(dict.fromkeys(column for record in records for column in record))
        if not columns:
            continue
        formatted_rows = [
            [_format_value(record.get(column, "")) for column in columns] for record in records
        ]
        widths = [
            max(
                len(column),
                max((len(row[index]) for row in formatted_rows), default=0),
            )
            for index, column in enumerate(columns)
        ]
        prefix = f"param: {name}: "
        header = "  ".join(
            column.ljust(width) for column, width in zip(columns, widths, strict=True)
        )
        rows = []
        for record, formatted in zip(records, formatted_rows, strict=True):
            cells = []
            for index, (column, value) in enumerate(zip(columns, formatted, strict=True)):
                if index == len(columns) - 1:
                    cells.append(value)
                elif isinstance(record.get(column, ""), str):
                    cells.append(value.ljust(widths[index]))
                else:
                    cells.append(value.rjust(widths[index]))
            rows.append(" " * len(prefix) + "  ".join(cells))
        sections.append(prefix + header + " :=\n" + "\n".join(rows) + "\n;")
    return "\n\n".join(sections) + "\n"
