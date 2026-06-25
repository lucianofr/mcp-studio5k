"""SDK discovery: locate wheel/server, validate Python version and license."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 12)
MAX_PYTHON_EXCLUSIVE = (3, 14)


class SdkDiscoveryError(Exception):
    """Raised when the SDK cannot be located or validated."""


def validate_python_version() -> bool:
    """True only for Python in [3.12, 3.14) per SDK Requires-Python."""
    # Handle both real sys.version_info and monkeypatched tuples
    vi = sys.version_info
    current = (vi[0], vi[1])
    return MIN_PYTHON <= current < MAX_PYTHON_EXCLUSIVE
