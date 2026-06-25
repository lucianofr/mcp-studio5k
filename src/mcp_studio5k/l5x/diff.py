"""Human-readable diff per routine dialect — spec §5 preview_import."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from mcp_studio5k.l5x.parse import DEFAULT_MAX_L5X_BYTES, parse_l5x, routine_type


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


def _diff_st(old_routine, new_routine) -> RoutineDiff:
    """Placeholder for ST diff."""
    raise NotImplementedError("_diff_st not yet implemented")


def _diff_rll(old_routine, new_routine) -> RoutineDiff:
    """Placeholder for RLL diff."""
    raise NotImplementedError("_diff_rll not yet implemented")
