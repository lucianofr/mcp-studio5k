"""Offline parsing of L5X import outcomes — structured errors and an imported-element summary.

Pure-Python, no SDK dependency: this is the offline-authoring half of the two-layer
architecture.  Two concerns:

  * ``parse_import_errors`` turns the free-text message a failed
    ``partial_import_from_xml_file`` raises into a list of
    ``{rung, instrucao, codigo_erro, mensagem}`` objects so a client can correct the
    L5X programmatically instead of scraping prose.
  * ``summarize_imported_elements`` reads the L5X *payload* (not the SDK reply) to
    report what the import targeted — used to fill ``elementos_importados`` on success.

[incerto] The Logix Designer SDK does not document the exact text format of an import
failure.  ``parse_import_errors`` is therefore a tolerant best-effort parser: it
extracts whatever rung / instruction / error-code tokens it can recognise and always
falls back to one object carrying the full raw message, so no information is lost.
"""
from __future__ import annotations

import re

from .parse import DEFAULT_MAX_L5X_BYTES, L5xParseError, parse_l5x

# --- error-text token patterns (best-effort; see [incerto] note above) -------------
# Rung index:        "Rung 3", "rung #3", "Rung: 3"
_RE_RUNG = re.compile(r"\brung\s*#?\s*:?\s*(\d+)", re.IGNORECASE)
# Instruction:       a quoted token  'XIC' / "MOV", or "instruction XIC"
_RE_INSTR_QUOTED = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]{0,39})['\"]")
_RE_INSTR_WORD = re.compile(r"\binstruction\s+([A-Za-z_][A-Za-z0-9_]{0,39})", re.IGNORECASE)
# Error code:        "Error 1234", "error #16#A1", "code 0x1F", or a bare 4+ digit run
_RE_CODE = re.compile(
    r"\b(?:error|err|code|status)\s*#?\s*:?\s*((?:0x|16#)?[0-9A-Fa-f]{2,})",
    re.IGNORECASE,
)


def _parse_error_line(line: str) -> "dict | None":
    """Parse one line into an error object, or None when the line has no signal."""
    text = line.strip()
    if not text:
        return None

    rung_m = _RE_RUNG.search(text)
    code_m = _RE_CODE.search(text)
    instr_m = _RE_INSTR_WORD.search(text) or _RE_INSTR_QUOTED.search(text)

    # Skip lines that look like pure boilerplate with no locator and no code.
    if rung_m is None and code_m is None and instr_m is None:
        return None

    return {
        "rung": int(rung_m.group(1)) if rung_m else None,
        "instrucao": instr_m.group(1) if instr_m else None,
        "codigo_erro": code_m.group(1) if code_m else None,
        "mensagem": text,
    }


def parse_import_errors(error_text: str) -> list[dict]:
    """Parse an SDK import-failure message into a list of structured error objects.

    Each object is ``{rung, instrucao, codigo_erro, mensagem}`` (values are ``None``
    when that field could not be recognised).  The parser walks the message line by
    line and keeps every line that carries a rung index, an instruction token, or an
    error code.  If no line yields any signal, a single fallback object is returned
    with the full message as ``mensagem`` so the caller always gets the raw detail.

    [incerto] Field extraction is heuristic — the SDK's exact error grammar is not
    documented.  The raw text is always preserved in ``mensagem``.
    """
    raw = (error_text or "").strip()
    if not raw:
        return [{"rung": None, "instrucao": None, "codigo_erro": None, "mensagem": ""}]

    parsed = [obj for line in raw.splitlines() if (obj := _parse_error_line(line))]
    if parsed:
        return parsed

    # Nothing structured — surface the whole message as a single error.
    return [{"rung": None, "instrucao": None, "codigo_erro": None, "mensagem": raw}]


def summarize_imported_elements(
    l5x_content: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> list[dict]:
    """List the components an L5X payload imports, as ``[{tipo, nome}]``.

    Reads the import *payload* (the L5X text being applied), not any SDK reply: an
    RSLogix5000Content envelope declares its ``TargetType``/``TargetName`` and carries
    the component definitions (tags, UDTs, AOIs, routines, programs).  Returns the
    target plus every named definition found, de-duplicated, preserving order.

    Best-effort: a payload that cannot be parsed yields ``[]`` rather than raising, so
    a successful import is never reported as failed merely because the summary failed.
    """
    try:
        root = parse_l5x(l5x_content, max_bytes=max_bytes)
    except (L5xParseError, ValueError):
        return []

    elements: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(tipo: "str | None", nome: "str | None") -> None:
        if not tipo or not nome:
            return
        key = (tipo, nome)
        if key in seen:
            return
        seen.add(key)
        elements.append({"tipo": tipo, "nome": nome})

    # The envelope's declared target (e.g. TargetType="Routine" TargetName="Main").
    _add(root.get("TargetType"), root.get("TargetName"))

    # Named definitions carried by the payload.
    component_tags = (
        "DataType",
        "AddOnInstructionDefinition",
        "Tag",
        "Routine",
        "Program",
        "Module",
    )
    for tag in component_tags:
        for el in root.iter(tag):
            _add(tag, el.get("Name"))

    return elements
