"""Safety primitives: safety-tag exclusions, allowed properties, write rate limiting."""
from __future__ import annotations

from lxml import etree

DEFAULT_MAX_L5X_BYTES = 5_000_000

_HARDENED_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
)

# Attributes that carry a tag/operand reference in L5X.
_NAME_ATTRS = ("Operand", "Name")


class SafetyError(Exception):
    """Raised when L5X content cannot be safely inspected."""


def check_safety_exclusions(
    l5x_content: str,
    exclusions: frozenset[str],
    *,
    max_bytes: int = DEFAULT_MAX_L5X_BYTES,
) -> tuple[str, ...]:
    """Return the excluded safety-tag names this L5X would touch (empty => safe)."""
    raw = l5x_content.encode("utf-8")
    if len(raw) > max_bytes:
        raise SafetyError(f"l5x_content exceeds max_bytes ({len(raw)} > {max_bytes})")
    if "<!DOCTYPE" in l5x_content:
        raise SafetyError("DOCTYPE declarations are not allowed")
    if not exclusions:
        return ()
    try:
        root = etree.fromstring(raw, parser=_HARDENED_PARSER)
    except etree.XMLSyntaxError as exc:
        raise SafetyError(f"invalid L5X: {exc}") from exc

    seen: list[str] = []
    found: set[str] = set()
    for el in root.iter():
        for attr in _NAME_ATTRS:
            value = el.get(attr)
            if value in exclusions and value not in found:
                found.add(value)
                seen.append(value)
    return tuple(seen)


def check_allowed_property(name: str, allowed: frozenset[str]) -> bool:
    """Return True iff a controller-property edit targets an allowlisted name."""
    return name in allowed


class RateLimitError(Exception):
    """Raised when a write is refused by the rate limiter."""


class WriteRateLimiter:
    """Per-session write counter + cooldown between writes (injected clock)."""

    def __init__(self, *, limit: int, cooldown_seconds: float) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be >= 0")
        self._limit = limit
        self._cooldown = cooldown_seconds
        self._count = 0
        self._last_write: float | None = None

    def record_write(self, *, now: float) -> None:
        self._count += 1
        self._last_write = now

    def needs_reconfirm(self) -> bool:
        return self._count >= self._limit

    def in_cooldown(self, *, now: float) -> bool:
        if self._last_write is None:
            return False
        return (now - self._last_write) < self._cooldown

    @property
    def count(self) -> int:
        return self._count

    def check(self, *, now: float) -> None:
        if self.in_cooldown(now=now):
            raise RateLimitError("write cooldown active; wait before next import")
        if self.needs_reconfirm():
            raise RateLimitError("write limit reached this session; re-confirm required")
        self.record_write(now=now)
