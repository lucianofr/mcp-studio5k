"""Detect degenerate single-leg RLL branches: ``[...]`` with no top-level comma.

Studio 5000's neutral-text import rejects a branch that has a single leg (no
top-level comma separating parallel paths). The engine aborts the WHOLE import
with ``XMLSrv_E_IMPORT_ABORTED_NO_CHANGES`` — a cryptic, misleading token. A
single-leg branch is semantically just series, so the brackets are spurious.
Flagging it before the SDK call turns the silent engine abort into a precise
"rung N: single-leg branch" message.

A "leg separator" is a comma that sits at the branch's OWN bracket level — not
inside an instruction's parentheses ``MOV(a,b)`` and not inside a nested branch.
A branch with zero such commas has a single leg.
"""
from __future__ import annotations

_SNIPPET_MAX = 80


def single_leg_branch_spans(text: str) -> list[str]:
    """Return the source text of each single-leg ``[...]`` branch in ``text``.

    Char-scan with a stack of open branches. Commas are counted as leg
    separators only when they occur at the enclosing branch's paren depth, so
    ``MOV(a,b)`` and nested branches never mask a missing separator.
    """
    offenders: list[str] = []
    # Each open branch: [start_index, paren_depth_at_open, has_top_level_comma].
    stack: list[list] = []
    paren_depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            if paren_depth > 0:
                paren_depth -= 1
        elif ch == "[":
            stack.append([i, paren_depth, False])
        elif ch == ",":
            if stack and paren_depth == stack[-1][1]:
                stack[-1][2] = True
        elif ch == "]":
            if stack:
                start, _, has_comma = stack.pop()
                if not has_comma:
                    offenders.append(text[start : i + 1])
    return offenders


def _snippet(span: str) -> str:
    span = " ".join(span.split())
    return span if len(span) <= _SNIPPET_MAX else span[: _SNIPPET_MAX - 1] + "…"


def find_single_leg_branches(root) -> list[tuple["int | None", str]]:
    """Scan every ``<Rung>/<Text>`` under ``root`` for single-leg branches.

    Returns ``(rung_number, snippet)`` per offending branch; ``rung_number`` is
    None when the ``Number`` attribute is absent or non-integer.
    """
    out: list[tuple["int | None", str]] = []
    for rung in root.iter("Rung"):
        text_el = rung.find("Text")
        if text_el is None or not text_el.text:
            continue
        spans = single_leg_branch_spans(text_el.text)
        if not spans:
            continue
        raw = rung.get("Number")
        number = int(raw) if raw is not None and raw.lstrip("-").isdigit() else None
        for span in spans:
            out.append((number, _snippet(span)))
    return out
