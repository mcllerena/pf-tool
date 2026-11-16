"""Series branch model shared by lines and transformer branches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Branch:
    from_bus: int
    to_bus: int
    circuit: int
    resistance_pu: float
    reactance_pu: float
    charging_pu: float
    tap_ratio: float = 1.0
    phase_shift_radians: float = 0.0
    rating_mva: float = 0.0
    is_transformer: bool = False
    # Under-load tap changer data (DLIN Tmn/Tmx/Bctrl); zero limits = fixed tap.
    tap_min: float = 0.0
    tap_max: float = 0.0
    controlled_bus: int | None = None
    # Controllable series compensator (DCSC) data.
    is_csc: bool = False
    csc_mode: str = ""
    csc_x_min_pu: float = 0.0
    csc_x_max_pu: float = 0.0
    bypassed: bool = False
