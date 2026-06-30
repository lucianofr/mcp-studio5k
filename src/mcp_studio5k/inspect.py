from __future__ import annotations

import base64
import re
from urllib.parse import quote

from lxml import etree

from .envelope import Meta, err_envelope, ok_envelope
from .l5x.parse import DEFAULT_MAX_L5X_BYTES, L5xParseError, parse_l5x
from .project_session import SessionError

PROGRAMS_XPATH = "Controller/Programs"

# PLC identifiers: letter/underscore start, alphanumeric/underscore body, max 40 chars.
# fullmatch() used (not match()) to avoid the trailing-newline edge where "$" in match()
# would accept "abc\n" but fullmatch correctly rejects it.
_VALID_PLC_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")

# Canonical tag_xpath templates.  Only these two structural shapes are permitted;
# the variable slot must satisfy _VALID_PLC_NAME.  A character-class whitelist is
# intentionally NOT used: `Tag[@Name='T']` and `Tag[@Name='T' or //X]` share the
# same characters, so a whitelist cannot distinguish them.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,39}"
_TAG_XPATH_PATTERNS = [
    # Controller-scoped:  Controller/Tags/Tag[@Name='IDENT']
    re.compile(rf"Controller/Tags/Tag\[@Name='{_IDENT}'\]"),
    # Program-scoped:     Controller/Programs/Program[@Name='IDENT']/Tags/Tag[@Name='IDENT']
    re.compile(rf"Controller/Programs/Program\[@Name='{_IDENT}'\]/Tags/Tag\[@Name='{_IDENT}'\]"),
]

_VALID_MODES = {"ONLINE", "OFFLINE"}


def _validate_name(name: str, param: str) -> None:
    if not _VALID_PLC_NAME.fullmatch(name):
        raise ValueError(f"invalid {param}: {name!r}")


def _validate_tag_xpath(tag_xpath: str) -> None:
    """Raise ValueError when tag_xpath does not match a canonical safe template.

    Accepted shapes (IDENT = ``[A-Za-z_][A-Za-z0-9_]{0,39}``):
      - ``Controller/Tags/Tag[@Name='IDENT']``
      - ``Controller/Programs/Program[@Name='IDENT']/Tags/Tag[@Name='IDENT']``
    """
    if not any(p.fullmatch(tag_xpath) for p in _TAG_XPATH_PATTERNS):
        raise ValueError(f"invalid tag_xpath: {tag_xpath!r}")


def strip_comments(l5x_content: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES) -> str:
    root = parse_l5x(l5x_content, max_bytes=max_bytes)
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


async def list_programs(
    session, *, page_size: int = 100, cursor: "str | None" = None,
    max_bytes: int = DEFAULT_MAX_L5X_BYTES,
) -> dict:
    try:
        xml = strip_comments(
            await session.partial_export(PROGRAMS_XPATH), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
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
    session, program: str, *, page_size: int = 100, cursor: "str | None" = None,
    max_bytes: int = DEFAULT_MAX_L5X_BYTES,
) -> dict:
    try:
        _validate_name(program, "program")
        xml = strip_comments(
            await session.partial_export(_routines_xpath(program)), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
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


def _tag_matches(el, needle: "str | None", datatype_needle: "str | None") -> bool:
    """True when a Tag element passes the optional name and datatype substring filters."""
    name = el.get("Name")
    if not name:
        return False
    if needle is not None and needle not in name.lower():
        return False
    if datatype_needle is not None:
        data_type = (el.get("DataType") or "").lower()
        if datatype_needle not in data_type:
            return False
    return True


async def list_tags(
    session,
    scope: str,
    *,
    name_filter: "str | None" = None,
    datatype_filter: "str | None" = None,
    page_size: int = 100,
    cursor: "str | None" = None,
    max_bytes: int = DEFAULT_MAX_L5X_BYTES,
) -> dict:
    """List tags in ``scope`` ("controller" or a program name) with real type/dimension.

    Grounding read: returns ``{name, data_type, scope, dimension}`` per tag straight
    from a partial export of the live project, so a client never has to guess a tag
    name or type.  ``name_filter`` / ``datatype_filter`` are case-insensitive substring
    filters.  ``dimension`` carries the L5X ``Dimensions`` attribute (``None`` for a
    scalar tag).
    """
    try:
        if scope != "controller":
            _validate_name(scope, "scope")
        xml = strip_comments(
            await session.partial_export(_tags_xpath(scope)), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
        needle = name_filter.lower() if name_filter else None
        datatype_needle = datatype_filter.lower() if datatype_filter else None
        tags = [
            {
                "name": el.get("Name"),
                "data_type": el.get("DataType"),
                "scope": scope,
                "dimension": el.get("Dimensions"),
            }
            for el in root.findall(".//Tags/Tag")
            if _tag_matches(el, needle, datatype_needle)
        ]
        window, next_cursor, total = _paginate(tags, page_size, cursor)
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        window,
        meta=Meta(total=total, page=next_cursor, truncated=(next_cursor is not None)),
    )


# ---------------------------------------------------------------------------
# Tag value read — delegates to ProjectSession (reconciliation rule 9)
# ---------------------------------------------------------------------------

async def get_tag_value(session, tag_xpath: str, data_type: str, mode: str = "OFFLINE") -> dict:
    """Read a typed tag value; typed dispatch lives in ProjectSession (rule 9).

    Security: ``tag_xpath`` is validated against canonical structural templates
    before forwarding — a character-class whitelist cannot distinguish
    ``Tag[@Name='T']`` from ``Tag[@Name='T' or //X]``.  ``data_type`` is
    validated as a PLC identifier.  ``mode`` must be ``"ONLINE"`` or
    ``"OFFLINE"``.
    """
    try:
        _validate_tag_xpath(tag_xpath)
    except ValueError as exc:
        return err_envelope(str(exc))
    try:
        _validate_name(data_type, "data_type")
    except ValueError as exc:
        return err_envelope(str(exc))
    if mode not in _VALID_MODES:
        return err_envelope(f"invalid mode: {mode!r}; must be one of {sorted(_VALID_MODES)}")
    try:
        value = await session.get_tag_value(tag_xpath, data_type, mode=mode)
    except SessionError as exc:
        return err_envelope(str(exc))
    return ok_envelope({"value": value, "data_type": data_type.upper(), "mode": mode})


# ---------------------------------------------------------------------------
# L5X export — strip comments, report size, return resource hint if over limit
# ---------------------------------------------------------------------------

def _node_resource_uri(x_path: str) -> str:
    return f"l5x://node/{quote(x_path, safe='')}"


async def export_l5x(session, x_path: str, *, max_bytes: int) -> dict:
    """Export an L5X fragment, strip comments, and inline if within ``max_bytes``.

    When the stripped payload exceeds ``max_bytes`` the ``l5x`` field is set to
    ``None`` and a ``resource_uri`` is returned so callers can fetch on demand.
    """
    try:
        stripped = strip_comments(await session.partial_export(x_path))
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    size_bytes = len(stripped.encode("utf-8"))
    if size_bytes > max_bytes:
        return ok_envelope(
            {"l5x": None, "resource_uri": _node_resource_uri(x_path), "x_path": x_path},
            meta=Meta(truncated=True, size_bytes=size_bytes),
        )
    return ok_envelope(
        {"l5x": stripped, "resource_uri": None, "x_path": x_path},
        meta=Meta(truncated=False, size_bytes=size_bytes),
    )


# ---------------------------------------------------------------------------
# Grounding reads — the project is the source of truth.  Each tool partial-exports
# the relevant node to L5X (live project state) and parses it offline, so a client
# can read real tag/UDT/AOI/program/module facts before generating any logic and
# never has to invent names, member types, or AOI signatures.
# ---------------------------------------------------------------------------

_UDTS_XPATH = "Controller/DataTypes"
_AOIS_XPATH = "Controller/AddOnInstructionDefinitions"
_MODULES_XPATH = "Controller/Modules"

# Map the L5X Parameter ``Usage`` attribute to the IN / OUT / InOut grouping a faceplate
# author thinks in.  Anything unrecognised is reported verbatim under its own key.
_AOI_USAGE_GROUPS = {"Input": "in", "Output": "out", "InOut": "in_out"}


def _udt_xpath(name: str) -> str:
    return f"Controller/DataTypes/DataType[@Name='{name}']"


def _aoi_xpath(name: str) -> str:
    return f"Controller/AddOnInstructionDefinitions/AddOnInstructionDefinition[@Name='{name}']"


async def get_udt_definition(
    session, name: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> dict:
    """Return a UDT's real member layout: ``{name, family, class, members:[...]}``.

    Each member is ``{name, data_type, dimension, radix, hidden}`` read from the live
    project (``Controller/DataTypes/DataType[@Name=...]``).  Hidden members (compiler
    padding / backing bits) are included with ``hidden=true`` so a client sees the
    exact structure.  Returns an err envelope when the UDT does not exist.
    """
    try:
        _validate_name(name, "name")
        xml = strip_comments(
            await session.partial_export(_udt_xpath(name)), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
        dt = root.find(f".//DataType[@Name='{name}']")
        if dt is None:
            return err_envelope(f"UDT not found: {name}")
        members = [
            {
                "name": m.get("Name"),
                "data_type": m.get("DataType"),
                "dimension": m.get("Dimension"),
                "radix": m.get("Radix"),
                "hidden": (m.get("Hidden") or "").lower() == "true",
            }
            for m in dt.findall(".//Members/Member")
            if m.get("Name")
        ]
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        {
            "name": dt.get("Name"),
            "family": dt.get("Family"),
            "class": dt.get("Class"),
            "members": members,
        },
        meta=Meta(total=len(members)),
    )


async def get_aoi_signature(
    session, name: str, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> dict:
    """Return an Add-On Instruction's call signature from the live project.

    Shape: ``{name, revision, parameters:[...], in:[...], out:[...], in_out:[...]}``.
    Each parameter is ``{name, data_type, usage, required, visible, dimension}`` read
    from ``Controller/AddOnInstructionDefinitions/AddOnInstructionDefinition[@Name=...]``;
    parameters are also grouped by ``usage`` (IN / OUT / InOut) so a caller can wire the
    instruction correctly without guessing argument order.  Err envelope when missing.
    """
    try:
        _validate_name(name, "name")
        xml = strip_comments(
            await session.partial_export(_aoi_xpath(name)), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
        aoi = root.find(f".//AddOnInstructionDefinition[@Name='{name}']")
        if aoi is None:
            return err_envelope(f"AOI not found: {name}")
        grouped: dict[str, list] = {"in": [], "out": [], "in_out": [], "other": []}
        parameters = []
        for p in aoi.findall(".//Parameters/Parameter"):
            if not p.get("Name"):
                continue
            usage = p.get("Usage")
            param = {
                "name": p.get("Name"),
                "data_type": p.get("DataType"),
                "usage": usage,
                "required": (p.get("Required") or "").lower() == "true",
                "visible": (p.get("Visible") or "").lower() == "true",
                "dimension": p.get("Dimension"),
            }
            parameters.append(param)
            grouped[_AOI_USAGE_GROUPS.get(usage or "", "other")].append(param)
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(
        {
            "name": aoi.get("Name"),
            "revision": aoi.get("Revision"),
            "parameters": parameters,
            "in": grouped["in"],
            "out": grouped["out"],
            "in_out": grouped["in_out"],
        },
        meta=Meta(total=len(parameters)),
    )


async def list_programs_routines(
    session, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> dict:
    """List every program with its routines and each routine's language.

    Shape: ``[{program, routines:[{name, language}]}]`` — ``language`` is the L5X
    routine ``Type`` ("RLL" ladder, "ST" structured text, "FBD", "SFC").  Built from a
    single partial export of ``Controller/Programs`` (which carries the nested routine
    list), giving a client the real program/routine map in one call.

    [incerto] Relies on a ``Controller/Programs`` export including nested ``Routine``
    elements; if a given SDK build returns only program shells the ``routines`` lists
    come back empty rather than failing.
    """
    try:
        xml = strip_comments(
            await session.partial_export(PROGRAMS_XPATH), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
        programs = []
        for prog in root.findall(".//Programs/Program"):
            if not prog.get("Name"):
                continue
            routines = [
                {"name": r.get("Name"), "language": r.get("Type")}
                for r in prog.findall(".//Routines/Routine")
                if r.get("Name")
            ]
            programs.append({"program": prog.get("Name"), "routines": routines})
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(programs, meta=Meta(total=len(programs)))


async def get_module_config(
    session, *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> dict:
    """List configured I/O modules and their addressing from the live project.

    Shape: ``[{name, catalog_number, vendor, product_type, product_code, major, minor,
    parent_module, parent_mod_port_id, ports:[{id, type, address, upstream}]}]`` parsed
    from ``Controller/Modules``.  Ports carry the slot/IP addressing a client needs to
    reference I/O correctly.

    [incerto] Modules are not in the SDK's documented partial-export component list; if
    a given SDK build refuses the ``Controller/Modules`` node the underlying export
    raises and this returns an err envelope.  Verify against the installed SDK.
    """
    try:
        xml = strip_comments(
            await session.partial_export(_MODULES_XPATH), max_bytes=max_bytes
        )
        root = parse_l5x(xml, max_bytes=max_bytes)
        modules = []
        for mod in root.findall(".//Modules/Module"):
            if not mod.get("Name"):
                continue
            ports = [
                {
                    "id": port.get("Id"),
                    "type": port.get("Type"),
                    "address": port.get("Address"),
                    "upstream": (port.get("Upstream") or "").lower() == "true",
                }
                for port in mod.findall(".//Ports/Port")
            ]
            modules.append(
                {
                    "name": mod.get("Name"),
                    "catalog_number": mod.get("CatalogNumber"),
                    "vendor": mod.get("Vendor"),
                    "product_type": mod.get("ProductType"),
                    "product_code": mod.get("ProductCode"),
                    "major": mod.get("Major"),
                    "minor": mod.get("Minor"),
                    "parent_module": mod.get("ParentModule"),
                    "parent_mod_port_id": mod.get("ParentModPortId"),
                    "ports": ports,
                }
            )
    except (ValueError, L5xParseError) as exc:
        return err_envelope(str(exc))
    return ok_envelope(modules, meta=Meta(total=len(modules)))
