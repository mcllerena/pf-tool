"""Shared fixtures: a small synthetic ANAREDE case with every component type."""

from __future__ import annotations

from pathlib import Path

import pytest

# Seven buses: reference, generator with reactive limits, loads, a Tb = 3
# voltage-limited bus, an LTC-regulated bus with a switched shunt, and a bus
# behind an active and a bypassed series compensator with an SVC.
SMALL_CASE = """
param: DCTE: Mn Val :=
    "BASE" 100
    "TEPA" 0.01
    "ACIT" 30
    "TLTC" 0.01
;
param: DOPC: Op E :=
    "QLIM" "L"
    "VLIM" "L"
    "CTAP" "L"
    "CPHS" "L"
    "STEP" "L"
    "NEWT" "L"
;
param: DGLT: G Vmn Vmx :=
    "A" 0.95 1.05
;
param: DGBT: G V :=
    "A" 230
;
param: DBAR: No O E Name Tb V A0 Pg Qg Qgn Qgm Pl Ql Sh Gl Gb Are :=
    1 "A" "L" "REF" 2 1.02 0 0 0 -999 999 0 0 0 "A" "A" 1
    2 "A" "L" "GEN" 1 1.01 0 40 10 -30 30 0 0 0 "A" "A" 1
    3 "A" "L" "LOAD" 0 1.0 0 0 0 0 0 60 20 0 "A" "A" 1
    4 "A" "L" "VLIM" 3 1.0 0 0 0 0 0 20 10 0 "A" "A" 1
    5 "A" "L" "LV" 0 1.0 0 0 0 0 0 30 12 0 "A" "A" 1
    6 "A" "L" "SVC" 0 1.0 0 0 0 0 0 10 4 0 "A" "A" 1
    7 "A" "L" "TAIL" 0 1.0 0 0 0 0 0 5 2 0 "A" "A" 1
    8 "A" "D" "OFF" 0 1.0 0 0 0 0 0 0 0 0 "A" "A" 1
;
param: DGER: No O Pgn Pgm :=
    2 "A" 0 80
;
param: DLIN: I J Nc O E R X Bshl Tap Tmn Tmx Psh Cn Bctrl :=
    1 2 1 "A" "L" 0.5 5 2 1 0 0 0 200 ""
    1 3 1 "A" "L" 1 8 3 1 0 0 0 150 ""
    2 3 1 "A" "L" 1 6 2 1 0 0 0 150 ""
    3 4 1 "A" "L" 1 6 1 1 0 0 0 100 ""
    2 4 1 "A" "L" 0.5 6 0 1 0 0 0 100 ""
    3 5 1 "A" "L" 0 8 0 1 0.9 1.1 0 100 5
    4 6 2 "A" "L" 1 5 0 1 0 0 0 80 ""
    3 8 1 "A" "D" 1 5 0 1 0 0 0 80 ""
;
param: DCTR: I J Nc O PCtrl Ext Fmin Fmax Vsp :=
    2 4 1 "A" "P" 2 -30 30 15
;
param: DCSC: I J Nc O E Byp Ctrl Xv Xmin Xmax Cdcsc :=
    4 6 1 "A" "L" "D" "X" -2 -2 -2 80
    6 7 1 "A" "L" "L" "X" -2 -2 -2 80
;
param: DBSH: I J Nc O Ctrl Vmn Vmx Bctrl Qini Extr Clr :=
    5 0 1 "A" "C" 0.99 1.01 5 0 0 "N"
    3 4 1 "A" "F" 0.9 1.1 3 -2 3 "N"
;
param: DBSH_BANK: Parent O E Sht U UOp :=
    1 "A" "L" 5 4 0
;
param: DCER: Bus O E Qg Bctrl Ctrl Incl Qgn Qgm :=
    6 "A" "L" 0 6 "P" 2 -20 20
;
"""


@pytest.fixture
def small_case_file(tmp_path: Path) -> Path:
    path = tmp_path / "small.dat"
    path.write_text(SMALL_CASE, encoding="utf-8")
    return path
