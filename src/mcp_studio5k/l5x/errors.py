"""Stable error schema shared by every l5x validator and dispatcher."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding with an xpath-ish locator."""

    severity: str  # "error" | "warning"
    path: str  # xpath-ish locator
    message: str
    line: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Aggregate validation outcome returned by validate_l5x."""

    ok: bool
    issues: tuple[ValidationIssue, ...]
