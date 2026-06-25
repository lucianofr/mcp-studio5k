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

    lines = content.findall("Line")
    if not lines:
        issues.append(
            ValidationIssue(
                severity="error",
                path=f"{base}/STContent",
                message="STContent has no <Line> elements",
            )
        )
        return tuple(issues)

    expected = 0
    for line_el in lines:
        raw_number = line_el.get("Number")
        try:
            number = int(raw_number) if raw_number is not None else expected
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/STContent/Line",
                    message=f"Line Number is not an integer: {raw_number!r}",
                )
            )
            expected += 1
            continue

        text = line_el.text
        if text is None or text.strip() == "":
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/STContent/Line[@Number='{number}']",
                    message="Line has empty CDATA text",
                    line=number,
                )
            )

        if number != expected:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path=f"{base}/STContent/Line[@Number='{number}']",
                    message=(
                        f"Line Number {number} is not sequential "
                        f"(expected {expected})"
                    ),
                    line=number,
                )
            )
        expected = number + 1

    return tuple(issues)
