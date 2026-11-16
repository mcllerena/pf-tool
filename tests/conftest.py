"""Shared fixtures: the IEEE 9-bus system, plain and with every component type."""

from __future__ import annotations

from pathlib import Path

import pytest

IEEE9 = Path(__file__).parent / "data" / "IEEE9-FLOW.dat"

# Devices added to the IEEE 9-bus system so every model is exercised:
# an out-of-service bus and branch, an LTC regulating a new load bus 11 that
# also has a switched shunt bank, a bus 12 tied to bus 8 by a bypassed series
# compensator, a phase shifter on circuit 7-8, a fixed line shunt on circuit
# 9-6, generator active limits and an LCC HVDC link from bus 4 to bus 6.
COMPONENTS = """
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh Gl Gb Are :=
    10 "A" "D" "OFF" 0 1.0 0 0 0 0 0 0 0 0 "A" "A" 1
    11 "A" "L" "LTC" 0 1.0 0 0 0 0 0 30 12 0 "A" "A" 1
    12 "A" "L" "TAIL" 0 1.0 0 0 0 0 0 5 2 0 "A" "A" 1
;
param: DGER: No O Pgn Pgm :=
    2 "A" 0 250
;
param: DLIN: I J Nc O E R X Bshl Tap Tmn Tmx Psh Cn Bctrl :=
    4 11 1 "A" "L" 0 8 0 1 0.9 1.1 0 100 11
    8 10 1 "A" "D" 1 5 0 1 0 0 0 80 ""
;
param: DCTR: I J Nc O PCtrl Ext Fmin Fmax Vsp :=
    7 8 1 "A" "P" 7 -30 30 40
;
param: DCSC: I J Nc O E Byp Ctrl Xv Xmin Xmax Cdcsc :=
    8 12 1 "A" "L" "L" "X" -2 -2 -2 80
;
param: DBSH: I J Nc O Ctrl Vmn Vmx Bctrl Qini Extr Clr :=
    11 0 1 "A" "C" 0.99 1.01 11 0 0 "N"
    9 6 1 "A" "F" 0.9 1.1 9 -2 9 "N"
;
param: DBSH_BANK: Parent O E Sht U UOp :=
    1 "A" "L" 5 4 0
;
param: DELO: No O V Pbase Name M E :=
    1 "A" 100 100 "LINK" "N" "L"
;
param: DCBA: No O Tb Pol Name Gl V Rs Elo :=
    10 "A" 1 "+" "RECT" 0 100 1 1
    20 "A" 0 "+" "INV" 0 98 1 1
    30 "A" 0 0 "NEUR" 0 0 1 1
    40 "A" 0 0 "NEUI" 0 0 1 1
;
param: DCLI: I O J Nc Prop R L Cap :=
    10 "A" 20 1 "F" 4 0 0
;
param: DCNV: No O Bac Bdc Bneu Modo P Inom Xc Vfs Snt Rra Lra CCC Fr :=
    1 "A" 4 10 30 "R" 1 600 17.8 85 70 0 0 0 60
    2 "A" 6 20 40 "I" 1 600 17.2 82 70 0 0 0 60
;
param: DCCV: No O F MInv TCnv Vsp Marg Imax Ang AngMn AngMx Tmn Tmx S Vmn Tmh Ttr :=
    1 "A" "N" 0 "P" 30 10 9999 15 5 85 0.9 1.2 "Infinity" 0.975 1.237 1
    2 "A" "F" 0 "P" 30 10 40 17 17 73 0.9 1.2 "Infinity" 0.975 1.237 1
;
"""


@pytest.fixture
def ieee9_file(tmp_path: Path) -> Path:
    """A writable copy of the IEEE 9-bus case."""
    path = tmp_path / "ieee9.dat"
    path.write_text(IEEE9.read_text(encoding="utf-8"), encoding="utf-8")
    return path


@pytest.fixture
def components_file(tmp_path: Path) -> Path:
    """The IEEE 9-bus case with every device type added."""
    path = tmp_path / "ieee9_components.dat"
    path.write_text(IEEE9.read_text(encoding="utf-8") + COMPONENTS, encoding="utf-8")
    return path
