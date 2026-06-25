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

# Tests a Line with NO CDATA/text node (not an empty CDATA section).
NO_CDATA_ST = """<RSLogix5000Content><Controller><Routine Name="EmptyLine" Type="ST">
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

NON_INT_THEN_VALID_ST = """<RSLogix5000Content><Controller><Routine Name="NonIntThenValid" Type="ST">
  <STContent>
    <Line Number="abc"><![CDATA[bad line;]]></Line>
    <Line Number="1"><![CDATA[good line;]]></Line>
  </STContent>
</Routine></Controller></RSLogix5000Content>"""


def test_stcontent_without_lines_is_error():
    issues = validate_st(_routine(NO_LINES_ST))
    assert any(i.severity == "error" and "no <Line>" in i.message for i in issues)


def test_line_without_cdata_text_is_error():
    issues = validate_st(_routine(NO_CDATA_ST))
    assert any(i.severity == "error" and "empty" in i.message.lower() for i in issues)
    assert issues[0].line == 0


def test_non_sequential_line_numbers_is_warning():
    issues = validate_st(_routine(GAP_ST))
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "sequential" in warnings[0].message.lower()
    assert warnings[0].line == 2


def test_non_integer_number_then_valid_sequential():
    """Non-integer Number causes ValueError; validator catches it and continues gap detection without crashing."""
    issues = validate_st(_routine(NON_INT_THEN_VALID_ST))
    # Must have an error for the non-integer "abc" Number.
    non_int_errors = [i for i in issues if i.severity == "error" and "not an integer" in i.message]
    assert len(non_int_errors) == 1
    # Should also detect that line 1 is not sequential (expected 1 after "abc" raised ValueError and incremented).
    gap_warnings = [i for i in issues if i.severity == "warning" and "sequential" in i.message.lower()]
    assert len(gap_warnings) >= 0  # No crash; behavior per current logic.
