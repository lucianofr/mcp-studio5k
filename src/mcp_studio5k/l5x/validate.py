"""Top-level L5X validation: hardened parse then dispatch by dialect."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue, ValidationResult
from mcp_studio5k.l5x.fbd import validate_fbd
from mcp_studio5k.l5x.parse import (
    DEFAULT_MAX_L5X_BYTES,
    L5xParseError,
    parse_l5x,
    routine_type,
)
from mcp_studio5k.l5x.rll import validate_rll
from mcp_studio5k.l5x.st import validate_st

# Maps <Routine Type=...> to its dialect validator.
_DISPATCH = {
    "ST": validate_st,
    "RLL": validate_rll,
    "FBD": validate_fbd,
}


def _parse_failure(message: str) -> ValidationResult:
    """Stable shape for any pre-dispatch failure."""
    return ValidationResult(
        ok=False,
        issues=(ValidationIssue(severity="error", path="/", message=message),),
    )


def validate_l5x(content: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES) -> ValidationResult:
    """Parse hardened L5X and validate via the dialect-specific validator."""
    try:
        root = parse_l5x(content, max_bytes=max_bytes)
        kind = routine_type(root)
    except L5xParseError as exc:
        return _parse_failure(str(exc))

    validator = _DISPATCH.get(kind)
    if validator is None:
        return _parse_failure(f"unsupported routine Type: {kind!r}")

    routine_el = root.find(".//Routine")
    issues = validator(routine_el)
    return ValidationResult(ok=not issues, issues=tuple(issues))
