from __future__ import annotations

import base64
import re

from lxml import etree

from .envelope import Meta, err_envelope, ok_envelope
from .l5x.parse import L5xParseError, parse_l5x

PROGRAMS_XPATH = "Controller/Programs"

_VALID_PLC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")


def _validate_name(name: str, param: str) -> None:
    if not _VALID_PLC_NAME.match(name):
        raise ValueError(f"invalid {param}: {name!r}")


def strip_comments(l5x_content: str) -> str:
    root = parse_l5x(l5x_content)
    for comment in root.findall(".//Comment"):
        comment.getparent().remove(comment)
    return etree.tostring(root, encoding="unicode")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: "str | None") -> int:
    if cursor is None:
        return 0
    try:
        value = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, TypeError):
        raise ValueError("invalid cursor")
    if value < 0:
        raise ValueError("invalid cursor: negative offset")
    return value


def _paginate(items: list, page_size: int, cursor: "str | None"):
    total = len(items)
    start = _decode_cursor(cursor)
    window = items[start : start + page_size]
    next_cursor = _encode_cursor(start + page_size) if start + page_size < total else None
    return window, next_cursor, total


async def list_programs(session, *, page_size: int = 100, cursor: "str | None" = None) -> dict:
    try:
        xml = strip_comments(await session.partial_export(PROGRAMS_XPATH))
        root = parse_l5x(xml)
        programs = [
            {"name": el.get("Name"), "data_type": None, "scope": "controller"}
            for el in root.findall(".//Programs/Program")
            if el.get("Name")
        ]
        window, next_cursor, total = _paginate(programs, page_size, cursor)
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        window,
        meta=Meta(total=total, page=next_cursor, truncated=(next_cursor is not None)),
    )


def _routines_xpath(program: str) -> str:
    return f"Controller/Programs/Program[@Name='{program}']/Routines"


async def list_routines(
    session, program: str, *, page_size: int = 100, cursor: "str | None" = None
) -> dict:
    try:
        _validate_name(program, "program")
        xml = strip_comments(await session.partial_export(_routines_xpath(program)))
        root = parse_l5x(xml)
        routines = [
            {"name": el.get("Name"), "data_type": el.get("Type"), "scope": program}
            for el in root.findall(".//Routines/Routine")
            if el.get("Name")
        ]
        window, next_cursor, total = _paginate(routines, page_size, cursor)
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        window,
        meta=Meta(total=total, page=next_cursor, truncated=(next_cursor is not None)),
    )


def _tags_xpath(scope: str) -> str:
    if scope == "controller":
        return "Controller/Tags"
    return f"Controller/Programs/Program[@Name='{scope}']/Tags"


async def list_tags(
    session,
    scope: str,
    *,
    name_filter: "str | None" = None,
    page_size: int = 100,
    cursor: "str | None" = None,
) -> dict:
    try:
        if scope != "controller":
            _validate_name(scope, "scope")
        xml = strip_comments(await session.partial_export(_tags_xpath(scope)))
        root = parse_l5x(xml)
        needle = name_filter.lower() if name_filter else None
        tags = [
            {"name": el.get("Name"), "data_type": el.get("DataType"), "scope": scope}
            for el in root.findall(".//Tags/Tag")
            if el.get("Name") and (needle is None or needle in el.get("Name").lower())
        ]
        window, next_cursor, total = _paginate(tags, page_size, cursor)
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        window,
        meta=Meta(total=total, page=next_cursor, truncated=(next_cursor is not None)),
    )
