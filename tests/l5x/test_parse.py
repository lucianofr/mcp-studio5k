import pytest
from mcp_studio5k.l5x.errors import ValidationIssue, ValidationResult
from mcp_studio5k.l5x.parse import L5xParseError, parse_l5x, routine_type


def test_validation_issue_is_frozen_with_defaults():
    issue = ValidationIssue(severity="error", path="/Controller", message="boom")
    assert issue.severity == "error"
    assert issue.path == "/Controller"
    assert issue.message == "boom"
    assert issue.line is None
    with pytest.raises(Exception):
        issue.severity = "warning"  # frozen


def test_validation_result_holds_issue_tuple():
    issue = ValidationIssue(severity="warning", path="/", message="x", line=3)
    result = ValidationResult(ok=False, issues=(issue,))
    assert result.ok is False
    assert result.issues == (issue,)
    assert result.issues[0].line == 3


def test_parse_rejects_content_over_max_bytes_before_parsing():
    payload = "<a></a> xyz"
    assert len(payload.encode("utf-8")) > 10
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(payload, max_bytes=10)
    assert "max_bytes" in str(exc.value)


BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<RSLogix5000Content><Controller><Routine Type="ST">&lol3;</Routine></Controller></RSLogix5000Content>"""

XXE_FILE_READ = """<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<RSLogix5000Content><Controller><Routine Type="ST">&xxe;</Routine></Controller></RSLogix5000Content>"""


def test_parse_rejects_billion_laughs_doctype():
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(BILLION_LAUGHS, max_bytes=5_000_000)
    assert "DOCTYPE" in str(exc.value)


def test_parse_rejects_xxe_external_entity_doctype():
    with pytest.raises(L5xParseError) as exc:
        parse_l5x(XXE_FILE_READ, max_bytes=5_000_000)
    assert "DOCTYPE" in str(exc.value)


def test_parse_rejects_malformed_xml():
    with pytest.raises(L5xParseError):
        parse_l5x("<RSLogix5000Content><Controller>", max_bytes=5_000_000)


ST_DOC = """<RSLogix5000Content SchemaRevision="1.0">
  <Controller Name="C"><Programs><Program Name="Main"><Routines>
    <Routine Name="R" Type="ST"><STContent>
      <Line Number="0"><![CDATA[x := 1;]]></Line>
    </STContent></Routine>
  </Routines></Program></Programs></Controller>
</RSLogix5000Content>"""

RLL_DOC = ST_DOC.replace('Type="ST"', 'Type="RLL"')
FBD_DOC = ST_DOC.replace('Type="ST"', 'Type="FBD"')


def test_routine_type_returns_st():
    assert routine_type(parse_l5x(ST_DOC, max_bytes=5_000_000)) == "ST"


def test_routine_type_returns_rll():
    assert routine_type(parse_l5x(RLL_DOC, max_bytes=5_000_000)) == "RLL"


def test_routine_type_returns_fbd():
    assert routine_type(parse_l5x(FBD_DOC, max_bytes=5_000_000)) == "FBD"


def test_routine_type_raises_when_no_routine():
    root = parse_l5x("<RSLogix5000Content><Controller/></RSLogix5000Content>", max_bytes=5_000_000)
    with pytest.raises(L5xParseError):
        routine_type(root)
