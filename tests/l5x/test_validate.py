from mcp_studio5k.l5x.errors import ValidationResult
from mcp_studio5k.l5x.validate import validate_l5x


def test_parse_error_returns_stable_result():
    bad = '<!DOCTYPE x><RSLogix5000Content/>'
    result = validate_l5x(bad, max_bytes=5_000_000)
    assert isinstance(result, ValidationResult)
    assert result.ok is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "error"
    assert issue.path == "/"


def test_oversize_returns_stable_result():
    result = validate_l5x("<a></a> padding", max_bytes=5)
    assert result.ok is False
    assert result.issues[0].path == "/"


import mcp_studio5k.l5x.validate as validate_mod
from mcp_studio5k.l5x.errors import ValidationIssue

ST_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="ST">
  <STContent><Line Number="0"><![CDATA[x := 1;]]></Line></STContent>
</Routine></Controller></RSLogix5000Content>"""

RLL_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="RLL">
  <RLLContent><Rung Number="0" Type="N"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung></RLLContent>
</Routine></Controller></RSLogix5000Content>"""

FBD_DOC = """<RSLogix5000Content><Controller><Routine Name="R" Type="FBD">
  <FBDContent SheetSize="A" SheetOrientation="Landscape"><Sheet Number="1"/></FBDContent>
</Routine></Controller></RSLogix5000Content>"""


def test_routes_st_to_validate_st():
    assert validate_l5x(ST_DOC, max_bytes=5_000_000).ok is True


def test_routes_rll_to_validate_rll():
    assert validate_l5x(RLL_DOC, max_bytes=5_000_000).ok is True


def test_routes_fbd_to_validate_fbd(monkeypatch):
    sentinel = (ValidationIssue(severity="error", path="/fbd", message="from-fbd"),)
    monkeypatch.setitem(validate_mod._DISPATCH, "FBD", lambda routine_el: sentinel)
    result = validate_l5x(FBD_DOC, max_bytes=5_000_000)
    assert result.ok is False
    assert result.issues == sentinel
