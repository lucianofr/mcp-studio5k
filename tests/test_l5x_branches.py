"""Single-leg branch detector — the RC02a rung-11 defect class.

A branch `[...]` with no top-level comma is one leg, which the SDK import
rejects (aborts the whole import as NO_CHANGES). These lock the detector so a
future L5X can never silently trip that engine abort again.
"""
from __future__ import annotations

from mcp_studio5k.l5x.branches import (
    find_single_leg_branches,
    single_leg_branch_spans,
)
from mcp_studio5k.l5x.parse import parse_l5x


# --- pure text scan -------------------------------------------------------

def test_single_leg_branch_is_flagged():
    # The exact RC02a rung 11 shape: MUL then a one-leg [GRT ... MOV MOV].
    text = "MUL(A.JSP,1.15,G.AUX)[GRT(C.PV,G.AUX)MOV(G.KP,C.JGP)MOV(G.KI,C.KS)];"
    spans = single_leg_branch_spans(text)
    assert len(spans) == 1
    assert spans[0].startswith("[GRT")


def test_two_leg_branch_is_not_flagged():
    # rung 10 shape: two legs separated by a top-level comma.
    text = "[XIC(GI.Cmd.Dir_D)MOV(G.KP_D,C.JGP),XIC(GI.Cmd.Dir_E)MOV(G.KP_E,C.JGP)];"
    assert single_leg_branch_spans(text) == []


def test_instruction_comma_does_not_count_as_leg_separator():
    # The only comma sits inside MOV(...); the branch itself has one leg.
    assert single_leg_branch_spans("[MOV(A,B)];") == ["[MOV(A,B)]"]


def test_series_without_brackets_is_clean():
    text = "MUL(A.JSP,1.15,G.AUX)GRT(C.PV,G.AUX)MOV(G.KP,C.JGP);"
    assert single_leg_branch_spans(text) == []


def test_nested_single_leg_inside_multi_leg_is_flagged():
    # Outer branch has two legs (clean); inner branch in leg 1 has one leg.
    text = "[XIC(A)[MOV(X,Y)],XIC(B)OTE(C)];"
    spans = single_leg_branch_spans(text)
    assert spans == ["[MOV(X,Y)]"]


def test_multiple_single_leg_branches_all_reported():
    text = "[MOV(A,B)]OTE(Z)[XIC(Q)];"
    assert len(single_leg_branch_spans(text)) == 2


# --- L5X-level scan -------------------------------------------------------

_L5X = """<?xml version="1.0"?>
<RSLogix5000Content TargetType="Routine">
<Controller><Programs><Program><Routines>
<Routine Name="R" Type="RLL"><RLLContent>
<Rung Number="0" Type="N"><Text><![CDATA[XIC(A)OTE(B);]]></Text></Rung>
<Rung Number="11" Type="N"><Text><![CDATA[MUL(A.JSP,1.15,G.AUX)[GRT(C.PV,G.AUX)MOV(G.KP,C.JGP)];]]></Text></Rung>
</RLLContent></Routine>
</Routines></Program></Programs></Controller>
</RSLogix5000Content>
"""


def test_find_single_leg_branches_pinpoints_rung_number():
    root = parse_l5x(_L5X, max_bytes=1_000_000)
    offenders = find_single_leg_branches(root)
    assert len(offenders) == 1
    number, snippet = offenders[0]
    assert number == 11
    assert snippet.startswith("[GRT")
