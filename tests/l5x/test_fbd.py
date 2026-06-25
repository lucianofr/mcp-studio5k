"""Tests for FBD graph validation — spec §11."""
from pathlib import Path

import pytest

from mcp_studio5k.l5x.fbd import fbd_block_pins, validate_fbd
from mcp_studio5k.l5x.parse import parse_l5x

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _routine_el(filename: str):
    """Parse an L5X fixture and extract the FBD routine."""
    root = parse_l5x((FIXTURES / filename).read_bytes())
    return root.find(".//Routine[@Type='FBD']")


def _make_fbd(sheet_inner: str, content_attrs: str = 'SheetSize="A" SheetOrientation="Landscape"'):
    """Create a minimal FBD routine from sheet content."""
    xml = (
        '<RSLogix5000Content SchemaRevision="1.0">'
        '<Controller Name="C"><Programs><Program Name="P"><Routines>'
        '<Routine Name="R" Type="FBD">'
        f"<FBDContent {content_attrs}>"
        f'<Sheet Number="1">{sheet_inner}</Sheet>'
        "</FBDContent></Routine>"
        "</Routines></Program></Programs></Controller></RSLogix5000Content>"
    )
    return parse_l5x(xml.encode()).find(".//Routine[@Type='FBD']")


# Cycle 9.1: fbd_block_pins known/unknown pin sets


def test_fbd_block_pins_arithmetic_blocks_have_source_dest():
    assert fbd_block_pins("ADD") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("SUB") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("MUL") == frozenset({"SourceA", "SourceB", "Dest"})
    assert fbd_block_pins("DIV") == frozenset({"SourceA", "SourceB", "Dest"})


def test_fbd_block_pins_scl_has_scaling_pins():
    assert fbd_block_pins("SCL") == frozenset(
        {"In", "InRawMax", "InRawMin", "InEUMax", "InEUMin", "Out"}
    )


def test_fbd_block_pins_unknown_type_returns_empty():
    assert fbd_block_pins("WIDGET_9000") == frozenset()


# Cycle 9.2: valid FBD fixture parses clean


def test_valid_fbd_sample_passes_clean():
    issues = validate_fbd(_routine_el("FBDLevelControlSimulation.L5X"))
    assert issues == ()


# Cycle 9.3: each §11 rule flags its violation


def test_rule1_duplicate_id_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<IRef ID="0" X="2" Y="2" Operand="B"/>'
    )
    issues = validate_fbd(el)
    assert any(i.severity == "error" and "duplicate ID '0'" in i.message for i in issues)


def test_rule2_dangling_wire_toid_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Wire FromID="0" ToID="99" ToParam="SourceA"/>'
    )
    issues = validate_fbd(el)
    assert any("ToID='99' does not resolve" in i.message for i in issues)


def test_rule3_toparam_not_in_visiblepins_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="SourceA Dest"/>'
        '<Wire FromID="0" ToID="2" ToParam="SourceB"/>'
    )
    issues = validate_fbd(el)
    assert any("not listed in VisiblePins" in i.message for i in issues)


def test_rule3_invalid_pin_for_type_is_error():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="Bogus"/>'
        '<Wire FromID="0" ToID="2" ToParam="Bogus"/>'
    )
    issues = validate_fbd(el)
    assert any("not valid for Block Type 'ADD'" in i.message for i in issues)


def test_rule3_unknown_block_type_warns_and_skips():
    el = _make_fbd(
        '<IRef ID="0" X="1" Y="1" Operand="A"/>'
        '<Block Type="WIDGET" ID="2" X="3" Y="3" Operand="W_01" VisiblePins="Foo"/>'
        '<Wire FromID="0" ToID="2" ToParam="Foo"/>'
    )
    issues = validate_fbd(el)
    assert any(i.severity == "warning" and "unknown Block Type 'WIDGET'" in i.message for i in issues)
    assert not any(i.severity == "error" for i in issues)


def test_rule5_missing_required_attr_is_error():
    el = _make_fbd('<Block Type="ADD" ID="2" X="3" Y="3" VisiblePins="Dest"/>')  # no Operand
    issues = validate_fbd(el)
    assert any("missing required attribute 'Operand'" in i.message for i in issues)


def test_rule5_non_integer_x_is_error():
    el = _make_fbd('<IRef ID="0" X="left" Y="1" Operand="A"/>')
    issues = validate_fbd(el)
    assert any("'X' must be an integer" in i.message for i in issues)


def test_rule6_missing_sheetsize_is_error():
    el = _make_fbd('<IRef ID="0" X="1" Y="1" Operand="A"/>', content_attrs='SheetOrientation="Landscape"')
    issues = validate_fbd(el)
    assert any("missing required attribute 'SheetSize'" in i.message for i in issues)
