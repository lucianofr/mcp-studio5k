from mcp_studio5k.l5x.diff import diff_routines


def _fbd(nodes: str) -> str:
    return (
        '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
        '<Program Name="P"><Routines><Routine Name="R" Type="FBD">'
        '<FBDContent SheetSize="A" SheetOrientation="Landscape"><Sheet Number="1">'
        f"{nodes}</Sheet></FBDContent></Routine></Routines></Program></Programs>"
        "</Controller></RSLogix5000Content>"
    )


B_ADD = '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_01" VisiblePins="SourceA SourceB Dest"/>'
IREF_A = '<IRef ID="0" X="1" Y="1" Operand="FlowIntoTank"/>'
OCON = '<OCon ID="1" X="5" Y="5" Name="TankLevel"/>'


def test_fbd_old_none_all_blocks_and_wires_added():
    d = diff_routines(
        None,
        _fbd(IREF_A + OCON + B_ADD + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>'),
        max_bytes=100_000,
    )
    assert d.routine_type == "FBD"
    assert all(e.kind == "add" for e in d.entries)
    assert any(e.unit == "block" for e in d.entries)
    assert any(e.unit == "wire" for e in d.entries)
    assert d.written_coils == ()


def test_fbd_referenced_tags_from_operands():
    d = diff_routines(None, _fbd(IREF_A + B_ADD), max_bytes=100_000)
    assert "FlowIntoTank" in d.referenced_tags
    assert "ADD_01" in d.referenced_tags


def test_fbd_block_alter_when_operand_changes():
    old = _fbd(B_ADD)
    new = _fbd('<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_99" VisiblePins="SourceA SourceB Dest"/>')
    d = diff_routines(old, new, max_bytes=100_000)
    assert ("alter", "block") in {(e.kind, e.unit) for e in d.entries}
    assert "ADD_99" in d.referenced_tags


def test_fbd_block_remove_and_wire_add():
    old = _fbd(IREF_A + B_ADD)
    new = _fbd(IREF_A + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>')
    d = diff_routines(old, new, max_bytes=100_000)
    pairs = {(e.kind, e.unit) for e in d.entries}
    assert ("remove", "block") in pairs
    assert ("add", "wire") in pairs


def test_fbd_two_versions_mixed_changes():
    old = _fbd(
        IREF_A + B_ADD
        + '<Wire FromID="0" ToID="2" ToParam="SourceA"/>'
        + '<Wire FromID="2" FromParam="Dest" ToID="1"/>'
        + OCON
    )
    new = _fbd(
        IREF_A
        + '<Block Type="ADD" ID="2" X="3" Y="3" Operand="ADD_02" VisiblePins="SourceA SourceB Dest"/>'
        + '<Wire FromID="0" ToID="2" ToParam="SourceB"/>'
        + '<Wire FromID="2" FromParam="Dest" ToID="1"/>'
        + OCON
    )
    d = diff_routines(old, new, max_bytes=100_000)
    pairs = {(e.kind, e.unit, e.locator) for e in d.entries}
    assert ("alter", "block", "2") in pairs
    assert ("add", "wire", "Wire:0.->2.SourceB") in pairs
    assert ("remove", "wire", "Wire:0.->2.SourceA") in pairs
    assert not any(
        e.unit == "wire" and e.locator == "Wire:2.Dest->1." for e in d.entries
    )
    assert "ADD_02" in d.referenced_tags
    assert d.written_coils == ()
