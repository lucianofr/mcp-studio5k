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


def _st(lines: list[str]) -> str:
    body = "".join(
        f'<Line Number="{i}"><![CDATA[{t}]]></Line>' for i, t in enumerate(lines)
    )
    return (
        '<RSLogix5000Content SchemaRevision="1.0"><Controller Name="C"><Programs>'
        '<Program Name="P"><Routines><Routine Name="R" Type="ST"><STContent>'
        f"{body}</STContent></Routine></Routines></Program></Programs></Controller></RSLogix5000Content>"
    )


def test_st_old_none_all_lines_added():
    d = diff_routines(None, _st(["a := 1;", "b := MyTag;"]), max_bytes=100_000)
    assert d.routine_type == "ST"
    assert [e.kind for e in d.entries] == ["add", "add"]
    assert {e.locator for e in d.entries} == {"0", "1"}
    assert d.written_coils == ()


def test_st_add_remove_alter_lines():
    old = _st(["a := 1;", "b := 2;"])
    new = _st(["a := 1;", "b := 3;", "c := 4;"])
    d = diff_routines(old, new, max_bytes=100_000)
    kinds = {(e.kind, e.locator) for e in d.entries}
    assert ("alter", "1") in kinds
    assert ("add", "2") in kinds
    assert ("add", "0") not in kinds


def test_st_referenced_tags_from_new_content():
    d = diff_routines(None, _st(["Level := FlowIntoTank + Offset;"]), max_bytes=100_000)
    assert "FlowIntoTank" in d.referenced_tags
    assert "Offset" in d.referenced_tags
