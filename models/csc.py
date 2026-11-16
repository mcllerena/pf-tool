"""Controllable-series-compensator (DCSC) branch models."""

from __future__ import annotations

from models.branch import Branch

# Series reactance (pu) used for a bypassed compensator; network reduction
# contracts it into a single electrical node.
BYPASS_REACTANCE_PU = 1.0e-6


def apply_csc_records(branches: list[Branch], records: list[dict[str, str]]) -> None:
    """Stamp each in-service DCSC as a series branch.

    ANAREDE DCSC records describe their own circuit between buses ``I`` and
    ``J``.  The present reactance ``Xv`` (percent) is used as a fixed series
    reactance.  A bypassed compensator (``Byp = "L"``) is a closed short, so
    it connects both terminals with negligible impedance.  If a DLIN circuit
    with the same key exists, the compensator reactance is added in series.
    """
    lookup = {(branch.from_bus, branch.to_bus, branch.circuit): branch for branch in branches}
    for record in records:
        if record.get("O", "A").upper() != "A" or record.get("E", "L").upper() == "D":
            continue
        start = int(float(record["I"]))
        end = int(float(record["J"]))
        circuit = int(float(record.get("Nc", 1)))
        bypassed = record.get("Byp", "D").upper() == "L"
        reactance = 0.0 if bypassed else _reactance_percent(record) * 0.01
        branch = lookup.get((start, end, circuit)) or lookup.get((end, start, circuit))
        if branch is not None:
            branch.reactance_pu += reactance
            continue
        if bypassed or abs(reactance) < BYPASS_REACTANCE_PU:
            reactance = BYPASS_REACTANCE_PU
        csc_branch = Branch(
            from_bus=start,
            to_bus=end,
            circuit=circuit,
            resistance_pu=0.0,
            reactance_pu=reactance,
            charging_pu=0.0,
            rating_mva=_number(record.get("Cdcsc")),
            is_csc=True,
            csc_mode=record.get("Ctrl", "X").upper(),
            csc_x_min_pu=_number(record.get("Xmin")) * 0.01,
            csc_x_max_pu=_number(record.get("Xmax")) * 0.01,
            bypassed=bypassed,
        )
        branches.append(csc_branch)
        lookup[(start, end, circuit)] = csc_branch


def _reactance_percent(record: dict[str, str]) -> float:
    for field in ("Xv", "Xmin"):
        value = record.get(field)
        if value not in (None, ""):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _number(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0
