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
from .l5x.import_result import parse_import_errors, summarize_imported_elements
from .l5x.parse import L5xParseError, parse_l5x
from .l5x.validate import validate_l5x
from .project_session import (
    IMPORT_NO_CHANGES,
    IMPORT_NO_CHANGES_TOKEN,
    SessionError,
)
from .safety import RateLimitError, SafetyError, check_safety_exclusions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOKEN_SEPARATOR = b"\x00"

def _import_no_changes_envelope(details: dict) -> dict:
    """Honest envelope for a NO_CHANGES abort — nothing was written.

    Prevents the "applied:true mentiroso" silent no-op: the SDK aborted with
    ``XMLSrv_E_IMPORT_ABORTED_NO_CHANGES`` and made no change. This is an error
    (ok=False) so callers never treat it as applied; the message points at the
    usual causes (payload target/insert_position/replace_count resolving to no
    real change, or the target already matching the payload).
    """
    env = err_envelope(
        "import made NO changes: the SDK aborted with "
        f"{IMPORT_NO_CHANGES_TOKEN} and nothing was written. Verify by value — do "
        "NOT treat this as applied. Likely causes: the target already matches the "
        "payload, or the payload's target node / insert_position / replace_count "
        "did not resolve to a real change."
    )
    env["data"] = {"status": "no_changes", "applied": False, **details}
    return env


# ---------------------------------------------------------------------------
# Structured import outcomes — a failed import returns a machine-correctable list
# of {rung, instrucao, codigo_erro, mensagem}; success reports what was imported.
# These wrap the apply call so the SDK's free-text reply becomes a turnable signal.
# ---------------------------------------------------------------------------


def _import_failure_envelope(exc: Exception) -> dict:
    """Build an err envelope whose ``data.errors`` is the structured failure list.

    Keeps the human-readable ``error`` string (backwards compatible) and adds a
    parsed ``errors`` array so a client can fix the offending rung/instruction.
    """
    message = str(exc)
    env = err_envelope(message)
    env["data"] = {"status": "error", "errors": parse_import_errors(message)}
    return env


def _import_success_envelope(applied: dict, l5x_content: str, *, max_bytes: int) -> dict:
    """Build an ok envelope carrying ``status="ok"`` and ``elementos_importados``."""
    return ok_envelope(
        {
            **applied,
            "status": "ok",
            "elementos_importados": summarize_imported_elements(
                l5x_content, max_bytes=max_bytes
            ),
        }
    )


# ---------------------------------------------------------------------------
# Public: make_change_token
# ---------------------------------------------------------------------------


def make_change_token(
    l5x_content: str, x_path: str, *, salt: str, context: str = ""
) -> str:
    """Return a 64-char hex SHA-256 token binding content + xpath + salt + context.

    The token is deterministic: same inputs always produce the same hex string.
    *context* binds the token to the project state the human actually previewed
    (current-routine hash + project path, see ``_token_context``), so a token
    stops validating when the routine changes or a different project is opened.
    """
    digest = hashlib.sha256()
    digest.update(l5x_content.encode("utf-8"))
    digest.update(_TOKEN_SEPARATOR)
    digest.update(x_path.encode("utf-8"))
    digest.update(_TOKEN_SEPARATOR)
    digest.update(salt.encode("utf-8"))
    digest.update(_TOKEN_SEPARATOR)
    digest.update(context.encode("utf-8"))
    return digest.hexdigest()


def _token_context(session, current: str) -> str:
    """Context string binding a change token to project identity + current state."""
    project_path = session.status().get("path") or ""
    state_digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
    return f"{state_digest}\x00{project_path}"


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


# Dotted tag path (Pump1.Speed → base tag Pump1); a name followed by "(" is an
# instruction/function call (XIC(...), TON(...)), not a tag reference.
_TAG_PATH = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)(\s*\()?"
)

# Structured Text keywords/operators that the identifier scan must not report
# as "tags missing from the project".
_ST_KEYWORDS = frozenset({
    "IF", "THEN", "ELSE", "ELSIF", "END_IF", "CASE", "OF", "END_CASE",
    "FOR", "TO", "BY", "DO", "END_FOR", "WHILE", "END_WHILE",
    "REPEAT", "UNTIL", "END_REPEAT", "EXIT", "RETURN",
    "NOT", "AND", "OR", "XOR", "MOD", "TRUE", "FALSE",
})


def _referenced_operands(l5x_content: str) -> list[str]:
    """Extract candidate BASE tag names referenced by the L5X content.

    Collects XML ``Operand`` attributes plus identifiers from ``<Text>`` and
    ``<Line>`` element bodies, excluding instruction/function mnemonics
    (identifiers followed by "(") and ST keywords, and reducing dotted member
    paths to their base tag. Uses the hardened parser so malicious XML is
    rejected before any traversal.
    """
    root = parse_l5x(l5x_content)
    operands: list[str] = []
    for el in root.iter():
        op = el.get("Operand")
        if op:
            operands.append(op.split(".")[0])
    for node in root.findall(".//Text") + root.findall(".//Line"):
        if not node.text:
            continue
        for match in _TAG_PATH.finditer(node.text):
            name, is_call = match.group(1), match.group(2)
            if is_call:
                continue  # instruction/function mnemonic, not a tag
            base = name.split(".")[0]
            if base.upper() in _ST_KEYWORDS:
                continue
            operands.append(base)
    return operands


async def _project_tag_names(session) -> set[str]:
    """Collect controller- AND program-scoped tag names via paginated list calls.

    Program-scoped tags are legitimate references in routine logic; checking
    only the controller scope would flag them as missing.
    """
    from .inspect import list_programs, list_tags

    scopes: list[str] = ["controller"]
    cursor = None
    while True:
        page = await list_programs(session, page_size=500, cursor=cursor)
        if not page["ok"]:
            break
        scopes.extend(p["name"] for p in page["data"])
        cursor = page["meta"]["page"]
        if cursor is None:
            break

    names: set[str] = set()
    for scope in scopes:
        cursor = None
        while True:
            page = await list_tags(session, scope, page_size=500, cursor=cursor)
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

    # Step 2 — export the current routine; a bad x_path / closed session / SDK
    # failure surfaces here as an err envelope (never a raw exception).
    try:
        current = strip_comments(
            await session.partial_export(x_path), max_bytes=max_bytes
        )
    except (ValueError, SessionError, L5xParseError) as exc:
        return err_envelope(str(exc))

    # Step 3 — diff current vs. proposed (positional call, reconciliation rule 3).
    new_content = strip_comments(l5x_content, max_bytes=max_bytes)
    diff = diff_routines(current, new_content)

    # Step 4 — identify referenced tags not yet in the project.
    existing = await _project_tag_names(session)
    referenced = _referenced_operands(l5x_content)
    missing = sorted({op for op in referenced if op not in existing})

    # Step 5 — mint a change token bound to content + xpath + salt + the exact
    # current state (and project) the human is about to confirm against.
    token = make_change_token(
        l5x_content, x_path, salt=salt, context=_token_context(session, current)
    )

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
    # Recompute against the CURRENT project state so a token minted before the
    # routine changed (or against another project) stops validating.
    try:
        current = strip_comments(
            await session.partial_export(x_path), max_bytes=max_bytes
        )
    except (ValueError, SessionError, L5xParseError) as exc:
        return err_envelope(
            f"import refused: cannot export current state for change_token "
            f"verification: {exc}"
        )
    recomputed = make_change_token(
        l5x_content, x_path, salt=salt, context=_token_context(session, current)
    )
    if (
        not change_token
        or not expected_change_token
        or not hmac.compare_digest(change_token, expected_change_token)
        or not hmac.compare_digest(expected_change_token, recomputed)
    ):
        return err_envelope(
            "import refused: change_token missing or does not match a recent "
            "preview_import (the project state may have changed since the preview)"
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

    # Guard 6: rate limit. Fall back to a real monotonic clock when the caller
    # omits `now` so the cooldown always engages.
    now_val = now if now is not None else time.monotonic()
    try:
        rate_limiter.check(now=now_val)
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    # All guards passed — apply exactly once.  A failed import (rollback or raw SDK
    # fault) is turned into a structured, machine-correctable error list and does
    # NOT consume the write budget; only a successful write records.
    try:
        outcome = await session.apply_l5x_import(l5x_content, x_path, collision_option)
    except Exception as exc:
        return _import_failure_envelope(exc)
    if outcome == IMPORT_NO_CHANGES:
        return _import_no_changes_envelope(
            {"x_path": x_path, "collision_option": collision_option}
        )
    rate_limiter.record_write(now=now_val)
    return _import_success_envelope(
        {"applied": True, "x_path": x_path, "collision_option": collision_option},
        l5x_content,
        max_bytes=max_bytes,
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

    now_val = now if now is not None else time.monotonic()
    try:
        rate_limiter.check(now=now_val)
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    try:
        outcome = await session.apply_l5x_import(l5x_content, x_path, collision_option)
    except Exception as exc:
        return _import_failure_envelope(exc)
    if outcome == IMPORT_NO_CHANGES:
        return _import_no_changes_envelope(
            {"x_path": x_path, "collision_option": collision_option}
        )
    rate_limiter.record_write(now=now_val)
    return _import_success_envelope(
        {"applied": True, "x_path": x_path, "collision_option": collision_option},
        l5x_content,
        max_bytes=max_bytes,
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
    """Confirmed-gated file-based import shared by the component and routine tools.

    Both routines and components go through the generic
    ``partial_import_from_xml_file`` (a Routine is a valid target via the
    ``Program.*`` rule). A NO_CHANGES abort is surfaced honestly by the outcome
    check below — it means the engine rejected the import (e.g. an unresolved
    operand reference), NOT that the interface was wrong.
    """
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

    now_val = now if now is not None else time.monotonic()
    try:
        rate_limiter.check(now=now_val)
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    try:
        outcome = await session.apply_l5x_import(content, x_path, collision_option)
    except Exception as exc:  # SessionError/rollback or SDK failure → structured errors
        return _import_failure_envelope(exc)

    if outcome == IMPORT_NO_CHANGES:
        return _import_no_changes_envelope(
            {"x_path": x_path, "collision_option": collision_option}
        )

    rate_limiter.record_write(now=now_val)
    return _import_success_envelope(
        {
            "applied": True,
            "x_path": x_path,
            "collision_option": collision_option,
            "source": str(Path(path)),
            "bytes": byte_len,
        },
        content,
        max_bytes=max_bytes,
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
    # A Routine IS a valid Target for the generic partial_import interface
    # (covered by the Program.* target rule), so OVERWRITE_ON_COLL is the correct
    # overwrite path. A NO_CHANGES abort here is NOT a routing problem — it means
    # the engine rejected the import (typically an unresolved operand reference in
    # one of the rungs), which _apply_file_import now surfaces honestly.
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


async def import_rungs_l5x(
    session,
    l5x_content: str,
    x_path: str,
    *,
    insert_position: int,
    replace_count: int = 0,
    confirmed: bool = False,
    exclusions,
    rate_limiter,
    max_bytes: int,
    now: "float | None" = None,
) -> dict:
    """Import rungs into an existing routine (SDK partial_import_rungs).

    Guard chain mirrors import_tag_l5x: confirmed gate, payload target check,
    size cap, safety exclusions, rate limit, then the session mutation with
    full backup/rollback semantics. ``replace_count`` existing rungs starting
    at ``insert_position`` are replaced by the imported ones.
    """
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")

    if 'TargetType="Rung"' not in l5x_content and "<Rung" not in l5x_content:
        return err_envelope("import_rungs_l5x refused: payload contains no rungs")

    if insert_position < 0 or replace_count < 0:
        return err_envelope(
            "import refused: insert_position and replace_count must be >= 0"
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

    now_val = now if now is not None else time.monotonic()
    try:
        rate_limiter.check(now=now_val)
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    try:
        outcome = await session.apply_rungs_import(
            l5x_content, x_path, insert_position, replace_count
        )
    except Exception as exc:
        return _import_failure_envelope(exc)
    if outcome == IMPORT_NO_CHANGES:
        return _import_no_changes_envelope(
            {
                "x_path": x_path,
                "insert_position": insert_position,
                "replace_count": replace_count,
            }
        )
    rate_limiter.record_write(now=now_val)
    return _import_success_envelope(
        {
            "applied": True,
            "x_path": x_path,
            "insert_position": insert_position,
            "replace_count": replace_count,
        },
        l5x_content,
        max_bytes=max_bytes,
    )


async def import_with_target_l5x(
    session,
    l5x_content: str,
    x_path: str,
    target_name: str,
    *,
    confirmed: bool = False,
    exclusions,
    rate_limiter,
    max_bytes: int,
    now: "float | None" = None,
) -> dict:
    """Import a single component renaming it to ``target_name`` on the way in.

    Wraps SDK partial_import_with_target (not valid for Trends/Modules). Same
    guard chain as the other imports.
    """
    if confirmed is not True:
        return err_envelope("import refused: confirmed=True is required (human gate)")

    if not target_name or not target_name.strip():
        return err_envelope("import refused: target_name must be non-empty")

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

    now_val = now if now is not None else time.monotonic()
    try:
        rate_limiter.check(now=now_val)
    except RateLimitError as exc:
        return err_envelope(f"import refused: {exc}")

    try:
        outcome = await session.apply_import_with_target(l5x_content, x_path, target_name)
    except Exception as exc:
        return _import_failure_envelope(exc)
    if outcome == IMPORT_NO_CHANGES:
        return _import_no_changes_envelope(
            {"x_path": x_path, "target_name": target_name}
        )
    rate_limiter.record_write(now=now_val)
    return _import_success_envelope(
        {"applied": True, "x_path": x_path, "target_name": target_name},
        l5x_content,
        max_bytes=max_bytes,
    )
