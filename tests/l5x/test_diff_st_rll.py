import pytest

from mcp_studio5k.l5x.diff import DiffEntry, RoutineDiff, diff_routines

ST_V1 = (
    '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
    '<Program Name="P"><Routines><Routine Name="R" Type="ST"><STContent>'
    '<Line Number="0"><![CDATA[a := 1;]]></Line>'
    "</STContent></Routine></Routines></Program></Programs></Controller></RSLogix5000Content>"
)


def test_oversized_new_l5x_raises_value_error():
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        diff_routines(None, ST_V1, max_bytes=10)


def test_diff_entry_and_routine_diff_are_frozen():
    e = DiffEntry(kind="add", unit="line", locator="0", detail="a := 1;")
    with pytest.raises(Exception):
        e.kind = "remove"  # type: ignore[misc]
    d = RoutineDiff(routine_type="ST", entries=(e,), referenced_tags=(), written_coils=())
    assert d.entries[0].locator == "0"
    assert d.to_dict()["routine_type"] == "ST"
