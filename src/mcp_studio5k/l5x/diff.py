"""Human-readable diff per routine dialect — spec §5 preview_import."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from mcp_studio5k.l5x.parse import DEFAULT_MAX_L5X_BYTES, parse_l5x, routine_type

# Bare identifiers that are operand/tag references, excluding ST keywords.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_ST_KEYWORDS = frozenset(
    {
        "IF",
        "THEN",
        "ELSE",
        "ELSIF",
        "END_IF",
        "FOR",
        "TO",
        "BY",
        "DO",
        "END_FOR",
        "WHILE",
        "END_WHILE",
        "REPEAT",
        "UNTIL",
        "END_REPEAT",
        "CASE",
        "OF",
        "END_CASE",
        "RETURN",
        "AND",
        "OR",
        "XOR",
        "NOT",
        "MOD",
        "TRUE",
        "FALSE",
        "EXIT",
    }
)

# Coil-style (output) ladder instructions: their operand is a written coil.
_COIL_INSTR = frozenset({"OTE", "OTL", "OTU"})
# instruction(args) — e.g. XIC(Start), OTE(Motor), CPT(Out, In * 2)
_INSTR = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")


@dataclass(frozen=True)
class DiffEntry:
    kind: str  # "add" | "remove" | "alter"
    unit: str  # "rung" | "line" | "block" | "wire" | "instruction" | "coil"
    locator: str
    detail: str


@dataclass(frozen=True)
class RoutineDiff:
    routine_type: str  # "ST" | "RLL" | "FBD"
    entries: tuple[DiffEntry, ...]
    referenced_tags: tuple[str, ...]
    written_coils: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "routine_type": self.routine_type,
            "entries": [asdict(e) for e in self.entries],
            "referenced_tags": list(self.referenced_tags),
            "written_coils": list(self.written_coils),
        }


def diff_routines(
    old_l5x: "str | None", new_l5x: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> RoutineDiff:
    """Diff two routine L5X strings. old_l5x None => everything is "add"."""
    if len(new_l5x.encode("utf-8")) > max_bytes:
        raise ValueError("new_l5x exceeds max_bytes")
    if old_l5x is not None and len(old_l5x.encode("utf-8")) > max_bytes:
        raise ValueError("old_l5x exceeds max_bytes")

    new_root = parse_l5x(new_l5x.encode("utf-8"))
    new_routine = new_root.find(".//Routine")
    rtype = routine_type(new_root)

    old_routine = None
    if old_l5x is not None:
        old_routine = parse_l5x(old_l5x.encode("utf-8")).find(".//Routine")

    if rtype == "ST":
        return _diff_st(old_routine, new_routine)
    if rtype == "RLL":
        return _diff_rll(old_routine, new_routine)
    raise ValueError(f"unsupported routine type for diff: {rtype!r}")


def _st_lines(routine) -> dict[str, str]:
    """Extract ST lines from routine element."""
    if routine is None:
        return {}
    out: dict[str, str] = {}
    for line in routine.findall(".//Line"):
        out[line.get("Number", "")] = (line.text or "").strip()
    return out


def _st_refs(text: str) -> list[str]:
    """Extract tag references from ST text."""
    refs: list[str] = []
    for m in _IDENT.finditer(text):
        token = m.group(0)
        head = token.split(".")[0].upper()
        if head in _ST_KEYWORDS:
            continue
        refs.append(token)
    return refs


def _dedupe(items: list[str]) -> tuple[str, ...]:
    """Deduplicate items while preserving order."""
    seen: dict[str, None] = {}
    for it in items:
        seen.setdefault(it, None)
    return tuple(seen)


def _diff_st(old_routine, new_routine) -> RoutineDiff:
    """Diff ST routines per-line."""
    old = _st_lines(old_routine)
    new = _st_lines(new_routine)
    entries: list[DiffEntry] = []
    referenced: list[str] = []

    for num, text in new.items():
        if num not in old:
            entries.append(DiffEntry("add", "line", num, text))
            referenced.extend(_st_refs(text))
        elif old[num] != text:
            entries.append(DiffEntry("alter", "line", num, text))
            referenced.extend(_st_refs(text))
    for num, text in old.items():
        if num not in new:
            entries.append(DiffEntry("remove", "line", num, text))

    return RoutineDiff(
        routine_type="ST",
        entries=tuple(entries),
        referenced_tags=_dedupe(referenced),
        written_coils=(),
    )


def _rll_rungs(routine) -> dict[str, str]:
    """Extract RLL rungs from routine element."""
    if routine is None:
        return {}
    out: dict[str, str] = {}
    for rung in routine.findall(".//Rung"):
        text_el = rung.find("Text")
        out[rung.get("Number", "")] = (text_el.text or "").strip() if text_el is not None else ""
    return out


def _rll_refs(text: str) -> tuple[list[str], list[str]]:
    """Extract tag references and written coils from RLL text."""
    refs: list[str] = []
    coils: list[str] = []
    for m in _INSTR.finditer(text):
        instr = m.group(1).upper()
        args = [a.strip() for a in m.group(2).split(",") if a.strip()]
        for arg in args:
            for tag_m in _IDENT.finditer(arg):
                refs.append(tag_m.group(0))
        if instr in _COIL_INSTR and args:
            coils.append(args[0])
    return refs, coils


def _diff_rll(old_routine, new_routine) -> RoutineDiff:
    """Diff RLL routines per-rung."""
    old = _rll_rungs(old_routine)
    new = _rll_rungs(new_routine)
    entries: list[DiffEntry] = []
    referenced: list[str] = []
    coils: list[str] = []

    for num, text in new.items():
        if num not in old:
            entries.append(DiffEntry("add", "rung", num, text))
            refs, written = _rll_refs(text)
            referenced.extend(refs)
            coils.extend(written)
        elif old[num] != text:
            entries.append(DiffEntry("alter", "rung", num, text))
            refs, written = _rll_refs(text)
            referenced.extend(refs)
            coils.extend(written)
    for num, text in old.items():
        if num not in new:
            entries.append(DiffEntry("remove", "rung", num, text))

    return RoutineDiff(
        routine_type="RLL",
        entries=tuple(entries),
        referenced_tags=_dedupe(referenced),
        written_coils=_dedupe(coils),
    )
