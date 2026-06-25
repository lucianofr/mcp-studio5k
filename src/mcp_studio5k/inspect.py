from __future__ import annotations

import base64

from lxml import etree

from .envelope import Meta, err_envelope, ok_envelope

MAX_L5X_BYTES = 5 * 1024 * 1024  # §7 size ceiling before parse

PROGRAMS_XPATH = "Controller/Programs"


def _hardened_parser() -> "etree.XMLParser":
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def _parse(l5x_content: str) -> "etree._Element":
    raw = l5x_content.encode("utf-8")
    if len(raw) > MAX_L5X_BYTES:
        raise ValueError("L5X exceeds maximum allowed size")
    if "<!DOCTYPE" in l5x_content:
        raise ValueError("DOCTYPE is not allowed in L5X")
    return etree.fromstring(raw, parser=_hardened_parser())


def strip_comments(l5x_content: str) -> str:
    root = _parse(l5x_content)
    for comment in root.findall(".//Comment"):
        comment.getparent().remove(comment)
    return etree.tostring(root, encoding="unicode")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: "str | None") -> int:
    if cursor is None:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, TypeError):
        raise ValueError("invalid cursor")


def _paginate(items: list, page_size: int, cursor: "str | None"):
    total = len(items)
    start = _decode_cursor(cursor)
    window = items[start : start + page_size]
    next_cursor = _encode_cursor(start + page_size) if start + page_size < total else None
    return window, next_cursor, total


async def list_programs(session, *, page_size: int = 100, cursor: "str | None" = None) -> dict:
    try:
        xml = strip_comments(await session.partial_export(PROGRAMS_XPATH))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    programs = [
        {"name": el.get("Name"), "data_type": None, "scope": "controller"}
        for el in root.findall(".//Programs/Program")
        if el.get("Name")
    ]
    window, next_cursor, total = _paginate(programs, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))


def _routines_xpath(program: str) -> str:
    return f"Controller/Programs/Program[@Name='{program}']/Routines"


async def list_routines(
    session, program: str, *, page_size: int = 100, cursor: "str | None" = None
) -> dict:
    try:
        xml = strip_comments(await session.partial_export(_routines_xpath(program)))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    routines = [
        {"name": el.get("Name"), "data_type": el.get("Type"), "scope": program}
        for el in root.findall(".//Routines/Routine")
        if el.get("Name")
    ]
    window, next_cursor, total = _paginate(routines, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))


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
        xml = strip_comments(await session.partial_export(_tags_xpath(scope)))
        root = etree.fromstring(xml.encode("utf-8"), parser=_hardened_parser())
    except ValueError as exc:
        return err_envelope(str(exc))
    needle = name_filter.lower() if name_filter else None
    tags = [
        {"name": el.get("Name"), "data_type": el.get("DataType"), "scope": scope}
        for el in root.findall(".//Tags/Tag")
        if el.get("Name") and (needle is None or needle in el.get("Name").lower())
    ]
    window, next_cursor, total = _paginate(tags, page_size, cursor)
    return ok_envelope(window, meta=Meta(total=total, page=next_cursor))
