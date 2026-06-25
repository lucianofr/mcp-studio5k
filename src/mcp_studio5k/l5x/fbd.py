"""Structural (graph-integrity) validation for FBD routines — spec §11."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue

# Known function-block pin sets. Unknown types yield an empty set so the
# caller skips pin validation and emits a warning instead (spec §11 rule 3).
_BLOCK_PINS: dict[str, frozenset[str]] = {
    "ADD": frozenset({"SourceA", "SourceB", "Dest"}),
    "SUB": frozenset({"SourceA", "SourceB", "Dest"}),
    "MUL": frozenset({"SourceA", "SourceB", "Dest"}),
    "DIV": frozenset({"SourceA", "SourceB", "Dest"}),
    "MOD": frozenset({"SourceA", "SourceB", "Dest"}),
    "SCL": frozenset(
        {"In", "InRawMax", "InRawMin", "InEUMax", "InEUMin", "Out"}
    ),
}

_NODE_TAGS = ("Block", "IRef", "OCon", "ICon")
_WIRE_TAGS = ("Wire", "FeedbackWire")
_REQUIRED_ATTRS: dict[str, tuple[str, ...]] = {
    "Block": ("ID", "Type", "X", "Y", "Operand"),
    "IRef": ("ID", "X", "Y", "Operand"),
    "OCon": ("ID", "X", "Y", "Name"),
    "ICon": ("ID", "X", "Y", "Name"),
    "Wire": ("FromID", "ToID"),
    "FeedbackWire": ("FromID", "ToID"),
}


def fbd_block_pins(block_type: str) -> frozenset[str]:
    """Return the known pin set for a block Type, or empty if unknown."""
    return _BLOCK_PINS.get(block_type, frozenset())


def validate_fbd(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate FBD graph integrity per spec §11 rules 1-6.

    Pure structural check. Operand refs are collected (rule 4) for the
    caller's hallucination check but not resolved here.
    """
    issues: list[ValidationIssue] = []
    content = routine_el.find("FBDContent")
    if content is None:
        return (
            ValidationIssue(
                severity="error",
                path=_path(routine_el),
                message="FBD routine missing <FBDContent>",
                line=_line(routine_el),
            ),
        )

    # Rule 6: FBDContent requires SheetSize & SheetOrientation.
    for attr in ("SheetSize", "SheetOrientation"):
        if content.get(attr) is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(content),
                    message=f"<FBDContent> missing required attribute '{attr}'",
                    line=_line(content),
                )
            )

    for sheet in content.findall("Sheet"):
        issues.extend(_validate_sheet(sheet))

    return tuple(issues)


def _validate_sheet(sheet) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ids: dict[str, object] = {}

    for el in sheet:
        tag = el.tag
        if tag in _NODE_TAGS:
            issues.extend(_check_required(el))
            issues.extend(_check_xy(el))
            node_id = el.get("ID")
            if node_id is not None:
                if node_id in ids:  # Rule 1: ID unique per Sheet.
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            path=_path(el),
                            message=f"duplicate ID '{node_id}' in Sheet",
                            line=_line(el),
                        )
                    )
                else:
                    ids[node_id] = el

    for el in sheet:
        if el.tag in _WIRE_TAGS:
            issues.extend(_check_required(el))
            issues.extend(_validate_wire(el, ids))

    return issues


def _validate_wire(wire, ids) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for end, param in (("FromID", "FromParam"), ("ToID", "ToParam")):
        ref = wire.get(end)
        if ref is None:
            continue
        target = ids.get(ref)
        if target is None:  # Rule 2: endpoint must resolve in same Sheet.
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(wire),
                    message=f"{wire.tag} {end}='{ref}' does not resolve to an ID in Sheet",
                    line=_line(wire),
                )
            )
            continue
        pin = wire.get(param)
        if pin is not None and target.tag == "Block":
            issues.extend(_check_pin(wire, target, pin))
    return issues


def _check_pin(wire, block, pin: str) -> list[ValidationIssue]:
    block_type = block.get("Type", "")
    known = fbd_block_pins(block_type)
    if not known:  # Unknown Type: skip pin check, warn (rule 3).
        return [
            ValidationIssue(
                severity="warning",
                path=_path(wire),
                message=f"unknown Block Type '{block_type}'; pin '{pin}' not verified",
                line=_line(wire),
            )
        ]
    issues: list[ValidationIssue] = []
    if pin not in known:  # Rule 3: pin must be valid for the Type.
        issues.append(
            ValidationIssue(
                severity="error",
                path=_path(wire),
                message=f"pin '{pin}' is not valid for Block Type '{block_type}'",
                line=_line(wire),
            )
        )
        return issues
    visible = set((block.get("VisiblePins") or "").split())
    if pin not in visible:  # Rule 3: used pins must be in VisiblePins.
        issues.append(
            ValidationIssue(
                severity="error",
                path=_path(wire),
                message=f"pin '{pin}' is not listed in VisiblePins of Block '{block.get('ID')}'",
                line=_line(wire),
            )
        )
    return issues


def _check_required(el) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for attr in _REQUIRED_ATTRS.get(el.tag, ()):
        if el.get(attr) is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(el),
                    message=f"<{el.tag}> missing required attribute '{attr}'",
                    line=_line(el),
                )
            )
    return issues


def _check_xy(el) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for attr in ("X", "Y"):
        raw = el.get(attr)
        if raw is None:
            continue
        try:  # Rule 5: X/Y must be integers.
            int(raw)
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=_path(el),
                    message=f"<{el.tag}> attribute '{attr}' must be an integer, got '{raw}'",
                    line=_line(el),
                )
            )
    return issues


def _path(el) -> str:
    return el.getroottree().getpath(el)


def _line(el) -> int | None:
    return el.sourceline
