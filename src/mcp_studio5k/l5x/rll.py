"""Ladder (LD/RLL) routine validation — linear rungs."""
from __future__ import annotations

from mcp_studio5k.l5x.errors import ValidationIssue


def validate_rll(routine_el) -> tuple[ValidationIssue, ...]:
    """Validate an RLL <Routine>: requires <RLLContent> of <Rung> with <Text>."""
    issues: list[ValidationIssue] = []
    name = routine_el.get("Name", "?")
    base = f"/Routine[@Name='{name}']"

    content = routine_el.find("RLLContent")
    if content is None:
        issues.append(
            ValidationIssue(
                severity="error",
                path=base,
                message="RLL routine is missing required <RLLContent>",
            )
        )
        return tuple(issues)

    rungs = content.findall("Rung")
    if not rungs:
        issues.append(
            ValidationIssue(
                severity="error",
                path=f"{base}/RLLContent",
                message="RLLContent has no <Rung> elements",
            )
        )
        return tuple(issues)

    expected = 0
    for rung_el in rungs:
        raw_number = rung_el.get("Number")
        try:
            number = int(raw_number) if raw_number is not None else expected
        except ValueError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{base}/RLLContent/Rung",
                    message=f"Rung Number is not an integer: {raw_number!r}",
                )
            )
            expected += 1
            continue

        rung_path = f"{base}/RLLContent/Rung[@Number='{number}']"
        text_el = rung_el.find("Text")
        if text_el is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=rung_path,
                    message="Rung is missing required <Text>",
                    line=number,
                )
            )
        elif text_el.text is None or text_el.text.strip() == "":
            issues.append(
                ValidationIssue(
                    severity="error",
                    path=f"{rung_path}/Text",
                    message="Rung <Text> has empty CDATA",
                    line=number,
                )
            )

        if number != expected:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    path=rung_path,
                    message=(
                        f"Rung Number {number} is not sequential "
                        f"(expected {expected})"
                    ),
                    line=number,
                )
            )
        expected = number + 1

    return tuple(issues)
