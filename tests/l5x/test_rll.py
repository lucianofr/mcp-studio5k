from mcp_studio5k.l5x.parse import parse_l5x
from mcp_studio5k.l5x.rll import validate_rll


def _routine(doc: str):
    return parse_l5x(doc, max_bytes=5_000_000).find(".//Routine")


VALID_RLL = """<RSLogix5000Content><Controller><Routine Name="Scale" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N">
      <Comment><![CDATA[scale the input]]></Comment>
      <Text><![CDATA[CPT(Output, Input * Rate + Offset);]]></Text>
    </Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

NO_CONTENT_RLL = """<RSLogix5000Content><Controller><Routine Name="Empty" Type="RLL">
</Routine></Controller></RSLogix5000Content>"""


def test_valid_rll_routine_has_no_issues():
    assert validate_rll(_routine(VALID_RLL)) == ()


def test_missing_rllcontent_is_error():
    issues = validate_rll(_routine(NO_CONTENT_RLL))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "RLLContent" in issues[0].message


NO_RUNGS_RLL = """<RSLogix5000Content><Controller><Routine Name="Blank" Type="RLL">
  <RLLContent></RLLContent>
</Routine></Controller></RSLogix5000Content>"""

RUNG_NO_TEXT_RLL = """<RSLogix5000Content><Controller><Routine Name="NoText" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Comment><![CDATA[only a comment]]></Comment></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

RUNG_EMPTY_TEXT_RLL = """<RSLogix5000Content><Controller><Routine Name="EmptyText" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Text></Text></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""

GAP_RLL = """<RSLogix5000Content><Controller><Routine Name="Gap" Type="RLL">
  <RLLContent>
    <Rung Number="0" Type="N"><Text><![CDATA[XIC(a)OTE(b);]]></Text></Rung>
    <Rung Number="5" Type="N"><Text><![CDATA[XIC(c)OTE(d);]]></Text></Rung>
  </RLLContent>
</Routine></Controller></RSLogix5000Content>"""


def test_rllcontent_without_rungs_is_error():
    issues = validate_rll(_routine(NO_RUNGS_RLL))
    assert any(i.severity == "error" and "no <Rung>" in i.message for i in issues)


def test_rung_without_text_is_error():
    issues = validate_rll(_routine(RUNG_NO_TEXT_RLL))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "Text" in issues[0].message
    assert issues[0].line == 0


def test_rung_with_empty_text_is_error():
    issues = validate_rll(_routine(RUNG_EMPTY_TEXT_RLL))
    assert any(i.severity == "error" and "empty" in i.message.lower() for i in issues)


def test_non_sequential_rung_numbers_is_warning():
    issues = validate_rll(_routine(GAP_RLL))
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "sequential" in warnings[0].message.lower()
    assert warnings[0].line == 5
