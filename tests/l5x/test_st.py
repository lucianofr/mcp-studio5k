from mcp_studio5k.l5x.parse import parse_l5x
from mcp_studio5k.l5x.st import validate_st


def _routine(doc: str):
    root = parse_l5x(doc, max_bytes=5_000_000)
    return root.find(".//Routine")


VALID_ST = """<RSLogix5000Content><Controller><Routine Name="GearChange" Type="ST">
  <STContent>
    <Line Number="0"><![CDATA[IF input THEN]]></Line>
    <Line Number="1"><![CDATA[  state := NextState;]]></Line>
    <Line Number="2"><![CDATA[END_IF;]]></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""

NO_CONTENT_ST = """<RSLogix5000Content><Controller><Routine Name="Empty" Type="ST">
</Routine></Controller></RSLogix5000Content>"""


def test_valid_st_routine_has_no_issues():
    assert validate_st(_routine(VALID_ST)) == ()


def test_missing_stcontent_is_error():
    issues = validate_st(_routine(NO_CONTENT_ST))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "STContent" in issues[0].message


NO_LINES_ST = """<RSLogix5000Content><Controller><Routine Name="Blank" Type="ST">
  <STContent></STContent>
</Routine></Controller></RSLogix5000Content>"""

EMPTY_CDATA_ST = """<RSLogix5000Content><Controller><Routine Name="EmptyLine" Type="ST">
  <STContent>
    <Line Number="0"></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""

GAP_ST = """<RSLogix5000Content><Controller><Routine Name="Gap" Type="ST">
  <STContent>
    <Line Number="0"><![CDATA[a := 1;]]></Line>
    <Line Number="2"><![CDATA[b := 2;]]></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""


def test_stcontent_without_lines_is_error():
    issues = validate_st(_routine(NO_LINES_ST))
    assert any(i.severity == "error" and "no <Line>" in i.message for i in issues)


def test_line_without_cdata_text_is_error():
    issues = validate_st(_routine(EMPTY_CDATA_ST))
    assert any(i.severity == "error" and "empty" in i.message.lower() for i in issues)
    assert issues[0].line == 0


def test_non_sequential_line_numbers_is_warning():
    issues = validate_st(_routine(GAP_ST))
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "sequential" in warnings[0].message.lower()
    assert warnings[0].line == 2
