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


def fbd_block_pins(block_type: str) -> frozenset[str]:
    """Return the known pin set for a block Type, or empty if unknown."""
    return _BLOCK_PINS.get(block_type, frozenset())


def validate_fbd(routine_el) -> tuple[ValidationIssue, ...]:
    """Stub — to be implemented in Cycle 9.2."""
    return ()
