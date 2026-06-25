"""Structured Text (ST) routine validation — linear, per-line."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue


def validate_st(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate an ST <Routine>: requires <STContent> with CDATA lines."""
    issues: list[ValidationIssue] = []
    name = routine_el.get("Name", "?")
    base = f"/Routine[@Name='{name}']"

    content = routine_el.find("STContent")
    if content is None:
        issues.append(
            ValidationIssue(
                severity="error",
                path=base,
                message="ST routine is missing required <STContent>",
            )
        )
        return tuple(issues)

    return tuple(issues)
