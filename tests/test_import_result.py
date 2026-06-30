"""Offline import-result parsing: structured errors + imported-element summary."""
from __future__ import annotations

from mcp_studio5k.l5x.import_result import (
    parse_import_errors,
    summarize_imported_elements,
)


def test_parse_import_errors_extracts_rung_instruction_code():
    text = "Rung 3: instruction XIC - Error 1234: tag 'Foo' not found"
    errors = parse_import_errors(text)
    assert len(errors) == 1
    e = errors[0]
    assert e["rung"] == 3
    assert e["instrucao"] == "XIC"
    assert e["codigo_erro"] == "1234"
    assert "Foo" in e["mensagem"]


def test_parse_import_errors_multiline():
    text = "Rung 1: 'MOV' error 16#0A1\nRung 5: 'ADD' error 0x1F\nimport completed with errors"
    errors = parse_import_errors(text)
    assert [e["rung"] for e in errors] == [1, 5]
    assert errors[0]["codigo_erro"] == "16#0A1"


def test_parse_import_errors_fallback_when_unstructured():
    text = "import failed and was rolled back: backend refused the operation"
    errors = parse_import_errors(text)
    assert len(errors) == 1
    assert errors[0]["rung"] is None
    assert errors[0]["instrucao"] is None
    assert errors[0]["mensagem"] == text


def test_summarize_imported_elements_from_target_and_definitions():
    l5x = (
        '<RSLogix5000Content TargetType="DataType" TargetName="Motor_UDT">'
        "<Controller><DataTypes>"
        '<DataType Name="Motor_UDT"><Members>'
        '<Member Name="Speed" DataType="REAL"/></Members></DataType>'
        "</DataTypes></Controller></RSLogix5000Content>"
    )
    elems = summarize_imported_elements(l5x)
    assert {"tipo": "DataType", "nome": "Motor_UDT"} in elems
    # The target is reported once (de-duplicated).
    assert sum(e["nome"] == "Motor_UDT" for e in elems) == 1


def test_summarize_imported_elements_bad_payload_returns_empty():
    assert summarize_imported_elements("not xml at all <<<") == []
