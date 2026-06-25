"""Minimal valid L5X routine templates — spec §5 resources, §11 FBD shape."""
from __future__ import annotations

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision="32.00" '
    'TargetType="Routine">\n'
    '  <Controller Use="Context" Name="Template">\n'
    "    <Programs>\n"
    '      <Program Use="Context" Name="MainProgram">\n'
    "        <Routines>\n"
)
_FOOTER = (
    "        </Routines>\n"
    "      </Program>\n"
    "    </Programs>\n"
    "  </Controller>\n"
    "</RSLogix5000Content>\n"
)

_ST_BODY = (
    '          <Routine Name="NewRoutine" Type="ST">\n'
    "            <STContent>\n"
    '              <Line Number="0"><![CDATA[(* new routine *)]]></Line>\n'
    "            </STContent>\n"
    "          </Routine>\n"
)

_LD_BODY = (
    '          <Routine Name="NewRoutine" Type="RLL">\n'
    "            <RLLContent>\n"
    '              <Rung Number="0" Type="N">\n'
    "                <Text><![CDATA[NOP();]]></Text>\n"
    "              </Rung>\n"
    "            </RLLContent>\n"
    "          </Routine>\n"
)

# FBD: 1 IRef + 1 Block(ADD) + 1 OCon + wires, all §11-valid.
_FBD_BODY = (
    '          <Routine Name="NewRoutine" Type="FBD">\n'
    '            <FBDContent SheetSize="Tabloid - 11 x 17 in" '
    'SheetOrientation="Landscape">\n'
    '              <Sheet Number="1">\n'
    '                <IRef ID="0" X="160" Y="420" Operand="InputTag"/>\n'
    '                <Block Type="ADD" ID="1" X="300" Y="100" Operand="ADD_01" '
    'VisiblePins="SourceA SourceB Dest"/>\n'
    '                <OCon ID="2" X="520" Y="280" Name="OutputTag"/>\n'
    '                <Wire FromID="0" ToID="1" ToParam="SourceA"/>\n'
    '                <Wire FromID="1" FromParam="Dest" ToID="2"/>\n'
    "              </Sheet>\n"
    "            </FBDContent>\n"
    "          </Routine>\n"
)

_TEMPLATES: dict[str, str] = {
    "st": _ST_BODY,
    "ld": _LD_BODY,
    "fbd": _FBD_BODY,
}


def get_l5x_template(kind: str) -> str:
    """Return a minimal valid L5X routine for kind in {st, ld, fbd}."""
    body = _TEMPLATES.get(kind)
    if body is None:
        raise ValueError(f"unknown template kind: {kind!r}")
    return _HEADER + body + _FOOTER
