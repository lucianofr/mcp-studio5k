"""Hardened L5X parser: no entities, no network, no DTD, size-capped."""
from __future__ import annotations

from lxml import etree
from defusedxml.common import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.lxml import fromstring as _defused_fromstring

DEFAULT_MAX_L5X_BYTES = 5_000_000
# Tag prefix that begins a DOCTYPE declaration; rejected outright.
_DOCTYPE_TOKEN = "<!DOCTYPE"


class L5xParseError(Exception):
    """Raised when L5X content is malformed, oversize, or unsafe to parse."""


def parse_l5x(
    content: "str | bytes", *, max_bytes: int = DEFAULT_MAX_L5X_BYTES
) -> "etree._Element":
    """Parse hardened L5X text into an lxml element.

    Accepts str or bytes. Size check and DOCTYPE rejection happen BEFORE the
    parser sees the bytes, so a billion-laughs payload is refused up front.
    """
    encoded = content if isinstance(content, bytes) else content.encode("utf-8")
    text = content.decode("utf-8", "ignore") if isinstance(content, bytes) else content
    if len(encoded) > max_bytes:
        raise L5xParseError(
            f"content exceeds max_bytes ({len(encoded)} > {max_bytes})"
        )

    if _DOCTYPE_TOKEN in text:
        raise L5xParseError("DOCTYPE declarations are not allowed")

    # Defense-in-depth: defusedxml refuses DTDs/entities/external refs outright.
    try:
        _defused_fromstring(encoded)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise L5xParseError(f"forbidden XML construct: {exc}") from exc
    except Exception:
        # Malformed/other errors are re-surfaced by the hardened lxml pass below.
        pass

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(encoded, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise L5xParseError(f"invalid XML: {exc}") from exc
    return root


def routine_type(root: "etree._Element") -> str:
    """Return the Type of the first <Routine> ("ST"|"RLL"|"FBD")."""
    routine = root.find(".//Routine")
    if routine is None:
        raise L5xParseError("no <Routine> element found")
    routine_kind = routine.get("Type")
    if routine_kind is None:
        raise L5xParseError("<Routine> is missing required Type attribute")
    return routine_kind
