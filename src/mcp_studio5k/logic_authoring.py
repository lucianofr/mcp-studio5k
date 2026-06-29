"""Task 20: Logic authoring helpers — change-token and preview_import.

make_change_token:  Derive a content-bound gate token for the human-confirmation
                    flow.  The token is a SHA-256 hex digest over:
                    ``l5x_content + NUL + x_path + NUL + salt``.
                    Because it embeds both the *exact proposed content* and the
                    *target xpath*, Task 21 (import_l5x) can verify that the
                    confirmed import matches precisely what the operator previewed.

preview_import:     Export the current routine, diff against the proposed L5X,
                    flag tags referenced but absent from the project, and return
                    a signed change_token.  No mutation occurs here; the token
                    must be passed back to import_l5x to complete the gate.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from pathlib import Path

from .envelope import err_envelope, ok_envelope
from .inspect import strip_comments
from .l5x.diff import diff_routines
from .l5x.parse import parse_l5x
from .l5x.validate import validate_l5x
from .safety import RateLimitError, SafetyError, check_safety_exclusions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOKEN_SEPARATOR = b"\x00"


# ---------------------------------------------------------------------------
# Public: make_change_token
# ---------------------------------------------------------------------------


def make_change_token(l5x_content: str, x_path: str, *, salt: str) -> str:
    """Return a 64-char hex SHA-256 token that binds content + xpath + salt.

    The token is deterministic: same inputs always produce the same hex string.
    Changing any byte of *l5x_content*, *x_path*, or *salt* yields a different
    token, so Task 21 can detect a substitution attack or a stale preview.
    """
    digest = hashlib.sha256()
    digest.update(l5x_content.encode("utf-8"))
    digest.update(_TOKEN_SEPARATOR)
    digest.update(x_path.encode("utf-8"))
    digest.update(_TOKEN_SEPARATOR)
    digest.update(salt.encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_field(issue, key: str, default=None):
    """Unified field accessor: handles both dict issues (tests) and dataclass issues (production)."""
    if isinstance(issue, dict):
        return issue.get(key, default)
    return getattr(issue, key, default)


def _format_issues(issues) -> str:
    """Format validation issues into a human-readable error string."""
    parts = []
    for issue in issues:
        line = _get_field(issue, "line")
        loc = f"{_get_field(issue, 'path', '?')}" + (f":{line}" if line is not None else "")
        parts.append(
            f"[{_get_field(issue, 'severity', 'error')}] {loc} {_get_field(issue, 'message', '')}"
        )
    return "; ".join(parts) or "L5X validation failed"


def _referenced_operands(l5x_content: str) -> list[str]:
    """Extract all candidate tag/operand names from L5X content.

    Collects XML ``Operand`` attributes plus bare identifiers from
    ``<Text>`` and ``<Line>`` element bodies.  Uses the hardened parser
    so malicious XML is rejected before any traversal.
    """
    root = parse_l5x(l5x_content)
    operands: list[str] = []
    for el in root.iter():
        op = el.get("Operand")
        if op:
            operands.append(op)
    for node in root.findall(".//Text") + root.findall(".//Line"):
        if node.text:
            operands.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.text))
    return operands


async def _project_tag_names(session) -> set[str]:
    """Collect every controller-scoped tag name via paginated list_tags calls."""
    from .inspect import list_tags

    names: set[str] = set()
    cursor = None
    while True:
        page = await list_tags(session, "controller", page_size=500, cursor=cursor)
        if not page["ok"]:
            break
        names.update(t["name"] for t in page["data"])
        cursor = page["meta"]["page"]
        if cursor is None:
            break
    return names


# ---------------------------------------------------------------------------
# Public: preview_import
# ---------------------------------------------------------------------------


async def preview_import(
    session, l5x_content: str, x_path: str, *, max_bytes: int, salt: str
) -> dict:
    """Preview importing *l5x_content* at *x_path* without applying any change.

    Steps:
      1. Validate the proposed L5X (hardened parse + dialect checks).
      2. Export the current routine at *x_path* via ``session.partial_export``.
      3. Diff current vs. proposed content.
      4. Identify tags referenced in the new content that are absent from the project.
      5. Return a signed ``change_token`` that import_l5x (Task 21) must present
         to confirm the human-reviewed the exact same payload.

    Returns an ``err_envelope`` on validation failure or export error; an
    ``ok_envelope`` with ``diff``, ``referenced_tags_not_in_project``,
    ``change_token``, and ``x_path`` on success.
    """
    # Step 1 — validate proposed content first; fail fast before any I/O.
    validation = validate_l5x(l5x_content, max_bytes=max_bytes)
    if not validation.ok:
        return err_envelope(_format_issues(validation.issues))

    # Step 2 — export the current routine (None means "new routine", so ValueError
    # from a bad x_path surfaces here as an err envelope).
    try:
        current = strip_comments(await session.partial_export(x_path))
    except ValueError as exc:
        return err_envelope(str(exc))

    # Step 3 — diff current vs. proposed (positional call, reconciliation rule 3).
    new_content = strip_comments(l5x_content)
    diff = diff_routines(current, new_content)

    # Step 4 — identify referenced tags not yet in the project.
    existing = await _project_tag_names(session)
    referenced = _referenced_operands(l5x_content)
    missing = sorted({op for op in referenced if op not in existing})

    # Step 5 — mint a content-bound change token.
    token = make_change_token(l5x_content, x_path, salt=salt)

    return ok_envelope(
        {
            "diff": diff.to_dict(),
            "referenced_tags_not_in_project": missing,
            "change_token": token,
            "x_path": x_path,
        }
    )


# ---------------------------------------------------------------------------
# import_l5x — human-confirmation gate (Task 21)
# ---------------------------------------------------------------------------

# import_l5x (routine/logic import) must NOT silently overwrite existing logic:
# OVERWRITE_ON_COLL is intentionally excluded here and gated to import_tag_l5x.
_ALLOWED_COLLISION = frozenset({"CANCEL_ON_COLL", "DISCARD_ON_COLL"})


async def import_l5x(
    session,
    l5x_content: str,
    x_path: str,
    *,
    collision_option: str = "CANCEL_ON_COLL",
    confirmed: bool = False,
    change_token: "str | None" = None,
    expected_change_token: "str | None",
    exclusions,
    rate_limiter,
    max_bytes: int,
    salt: str,
    now: "float | None" = None,
) -> dict:
    """Apply an L5X import after passing all security and human-confirmation guards.

    Guard order (each failing guard returns err_envelope with NO write):
      1. confirmed must be True (human gate)
      2. change_token must equal expected_change_token, and that token must itself
         bind to this exact content+xpath+salt (all constant-time comparisons)
      3. collision_option must be in _ALLOWED_COLLISION
      4. l5x_content byte size must be <= max_bytes
      5. safety exclusions must not be touched
      6. rate limiter must allow the call
      7. apply (session.apply_l5x_import awaited exactly once)
    """
    # Guard 1: human confirmation gate
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")

    # Guard 2: the caller-presented change_token must match the expected token that
    # preview_import minted, AND that expected token must bind to this exact
    # content+xpath+salt.  The salt is a server-held secret; an empty/blank salt
    # would let an attacker who knows content+xpath forge a token, so reject it.
    if not salt or not salt.strip():
        return err_envelope("import refused: server salt is not configured")
    recomputed = make_change_token(l5x_content, x_path, salt=salt)
    if (
        not change_token
        or not expected_change_token
        or not hmac.compare_digest(change_token, expected_change_token)
        or not hmac.compare_digest(expected_change_token, recomputed)
    ):
        return err_envelope(
            "import refused: change_token missing or does not match a recent preview_import"
        )

    # Guard 3: collision option allowlist
    if collision_option not in _ALLOWED_COLLISION:
        return err_envelope(
            f"import refused: collision_option must be one of {sorted(_ALLOWED_COLLISION)}"
            " (OVERWRITE_ON_COLL requires a separate human step)"
        )

    # Guard 4: size ceiling
    if len(l5x_content.encode("utf-8")) > max_bytes:
        return err_envelope(
            f"import refused: l5x_content size exceeds max_bytes ({max_bytes})"
        )

    # Guard 5: safety exclusions. check_safety_exclusions raises SafetyError on a
    # DOCTYPE/oversize/malformed payload; convert that to a refusal envelope so the
    # gate never leaks an unhandled exception to the caller.
    try:
        hits = check_safety_exclusions(l5x_content, exclusions, max_bytes=max_bytes)
    except SafetyError as exc:
        return err_envelope(f"import refused: {exc}")
    if hits:
        return err_envelope(
            "import refused: content touches safety-excluded tags: "
            + ", ".join(sorted(hits))
        )

    # Guard 6: rate limit
    try:
        # Fall back to a real monotonic clock when the caller omits `now`.
        # Passing now=None would leave WriteRateLimiter._last_write unset and
        # fail-open (cooldown never engages), so the gate always supplies a float.
        rate_limiter.check(now=now if now is not None else time.monotonic())
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    # All guards passed — apply exactly once
    await session.apply_l5x_import(l5x_content, x_path, collision_option)
    return ok_envelope(
        {"applied": True, "x_path": x_path, "collision_option": collision_option}
    )


# ---------------------------------------------------------------------------
# import_tag_l5x — tag-creation gate (no preview: a not-yet-existing tag cannot
# be exported, so the content-bound change_token flow does not apply here).
# ---------------------------------------------------------------------------

# Creating/overwriting a tag is this tool's purpose, so OVERWRITE_ON_COLL is
# allowed here (unlike import_l5x). Kept separate to avoid widening the
# routine-import gate.
_ALLOWED_TAG_COLLISION = _ALLOWED_COLLISION | {"OVERWRITE_ON_COLL"}


async def import_tag_l5x(
    session,
    l5x_content: str,
    x_path: str,
    *,
    collision_option: str = "OVERWRITE_ON_COLL",
    confirmed: bool = False,
    exclusions,
    rate_limiter,
    max_bytes: int,
    now: "float | None" = None,
) -> dict:
    """Create/overwrite a single controller- or program-scoped Tag via partial import.

    The routine-oriented preview_import gate cannot be used to create a tag: a tag
    that does not yet exist cannot be exported, so there is no "current" content to
    diff and no change_token to mint.  This path therefore relies on the same
    backup→import→reopen→rollback safety in ``session.apply_l5x_import`` plus a
    reduced guard set:

      1. confirmed must be True (human gate)
      2. payload must declare TargetType="Tag" (refuse arbitrary L5X here)
      3. collision_option must be in _ALLOWED_COLLISION
      4. byte size must be <= max_bytes
      5. safety exclusions must not be touched
      6. rate limiter must allow the call
      7. apply (session.apply_l5x_import awaited exactly once)
    """
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")

    if 'TargetType="Tag"' not in l5x_content:
        return err_envelope(
            "import_tag_l5x refused: payload TargetType must be Tag"
        )

    if collision_option not in _ALLOWED_TAG_COLLISION:
        return err_envelope(
            f"import refused: collision_option must be one of {sorted(_ALLOWED_TAG_COLLISION)}"
        )

    if len(l5x_content.encode("utf-8")) > max_bytes:
        return err_envelope(
            f"import refused: l5x_content size exceeds max_bytes ({max_bytes})"
        )

    try:
        hits = check_safety_exclusions(l5x_content, exclusions, max_bytes=max_bytes)
    except SafetyError as exc:
        return err_envelope(f"import refused: {exc}")
    if hits:
        return err_envelope(
            "import refused: content touches safety-excluded tags: "
            + ", ".join(sorted(hits))
        )

    try:
        rate_limiter.check(now=now if now is not None else time.monotonic())
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    await session.apply_l5x_import(l5x_content, x_path, collision_option)
    return ok_envelope(
        {"applied": True, "x_path": x_path, "collision_option": collision_option}
    )


# Controller-scoped component definitions (AOI/UDT) import under the Controller node.
_COMPONENT_IMPORT_XPATH = "Controller"
_COMPONENT_TARGET_TYPES = ("AddOnInstructionDefinition", "DataType")
_ROUTINE_TARGET_TYPES = ("Routine",)
# Reject UNC/device paths the same way ProjectSession.resolve_under_root does, so an
# import source cannot reach \\host\share or \\?\ device namespaces.
_UNC_PREFIXES = ("\\\\", "//", "\\\\?\\", "\\\\.\\")


def _read_l5x_file_guarded(path: str, *, max_bytes: int, allowed_target_types):
    """Shared file-source guards for the file-based imports.

    Returns ``(l5x_content, byte_len, None)`` on success, or
    ``(None, None, message)`` where ``message`` is a bare reason (callers prefix
    "import refused: "). Guards: non-UNC/device, ``.L5X`` suffix, file exists,
    size <= max_bytes, valid UTF-8 (BOM tolerated), declared TargetType in the
    allowlist. Reading server-side keeps the bytes faithful — no LLM transcription.
    """
    raw = str(path)
    if any(raw.startswith(p) for p in _UNC_PREFIXES):
        return None, None, f"UNC/device paths are not allowed: {raw}"

    file_path = Path(path)
    if file_path.suffix.lower() != ".l5x":
        return None, None, f"path must end in .L5X: {file_path}"
    if not file_path.is_file():
        return None, None, f"file not found: {file_path}"

    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return None, None, f"cannot read file: {exc}"

    if len(data) > max_bytes:
        return None, None, f"file size {len(data)} exceeds max_bytes ({max_bytes})"

    # utf-8-sig tolerates an optional BOM that RSLogix exports sometimes carry.
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, None, f"file is not valid UTF-8: {exc}"

    if not any(f'TargetType="{t}"' in content for t in allowed_target_types):
        return None, None, (
            "payload TargetType must be one of " + ", ".join(allowed_target_types)
        )

    return content, len(data), None


async def _apply_file_import(
    session,
    path,
    x_path,
    *,
    collision_option,
    confirmed,
    exclusions,
    rate_limiter,
    max_bytes,
    allowed_target_types,
    now,
):
    """Confirmed-gated file-based import shared by the component and routine tools."""
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")

    content, byte_len, file_err = _read_l5x_file_guarded(
        path, max_bytes=max_bytes, allowed_target_types=allowed_target_types
    )
    if file_err is not None:
        return err_envelope(f"import refused: {file_err}")

    if collision_option not in _ALLOWED_TAG_COLLISION:
        return err_envelope(
            f"import refused: collision_option must be one of "
            f"{sorted(_ALLOWED_TAG_COLLISION)}"
        )

    try:
        hits = check_safety_exclusions(content, exclusions, max_bytes=max_bytes)
    except SafetyError as exc:
        return err_envelope(f"import refused: {exc}")
    if hits:
        return err_envelope(
            "import refused: content touches safety-excluded tags: "
            + ", ".join(sorted(hits))
        )

    try:
        rate_limiter.check(now=now if now is not None else time.monotonic())
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    try:
        await session.apply_l5x_import(content, x_path, collision_option)
    except Exception as exc:  # SessionError/rollback or SDK failure → clean envelope
        return err_envelope(str(exc))

    return ok_envelope(
        {
            "applied": True,
            "x_path": x_path,
            "collision_option": collision_option,
            "source": str(Path(path)),
            "bytes": byte_len,
        }
    )


async def import_component_l5x(
    session,
    path: str,
    *,
    collision_option: str = "CANCEL_ON_COLL",
    confirmed: bool = False,
    exclusions,
    rate_limiter,
    max_bytes: int,
    now: "float | None" = None,
) -> dict:
    """Import a controller-scoped component definition (AOI/UDT) from an on-disk .L5X.

    Neither existing import path fits an AOI/UDT definition: ``import_l5x`` is
    routine-only (its change_token is derived from a routine diff, and the payload
    is too large to retransmit inline byte-for-byte twice) and ``import_tag_l5x``
    refuses any TargetType other than ``Tag``. This reads the file server-side and
    applies under the Controller node, gated on confirmed + the shared file guards.
    """
    return await _apply_file_import(
        session,
        path,
        _COMPONENT_IMPORT_XPATH,
        collision_option=collision_option,
        confirmed=confirmed,
        exclusions=exclusions,
        rate_limiter=rate_limiter,
        max_bytes=max_bytes,
        allowed_target_types=_COMPONENT_TARGET_TYPES,
        now=now,
    )


async def import_routine_l5x(
    session,
    path: str,
    x_path: str,
    *,
    collision_option: str = "OVERWRITE_ON_COLL",
    confirmed: bool = False,
    exclusions,
    rate_limiter,
    max_bytes: int,
    now: "float | None" = None,
) -> dict:
    """Import/replace a Routine from an on-disk .L5X at ``x_path`` (server reads bytes).

    ``import_l5x`` requires the full routine inline AND a change_token bound to a
    byte-identical retransmission of it — infeasible for large routines (e.g. a
    ~900 KB C_CONTROLE). The file-based authoring flow is: export the routine to
    disk, edit the rung(s) in the file, then call this with the edited file. The
    caller supplies the target routine ``x_path``; with OVERWRITE_ON_COLL the SDK
    replaces the existing routine. Safety still goes through
    ``session.apply_l5x_import`` (backup → import → reopen → rollback).
    """
    if not x_path or not str(x_path).strip():
        return err_envelope("import refused: x_path (target routine) is required")
    return await _apply_file_import(
        session,
        path,
        x_path,
        collision_option=collision_option,
        confirmed=confirmed,
        exclusions=exclusions,
        rate_limiter=rate_limiter,
        max_bytes=max_bytes,
        allowed_target_types=_ROUTINE_TARGET_TYPES,
        now=now,
    )
