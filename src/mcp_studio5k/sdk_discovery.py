"""SDK discovery: locate wheel/server, validate Python version and license."""
from __future__ import annotations

import re
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


DEFAULT_ACTIVATION_DIR = Path(
    r"C:\ProgramData\Rockwell\Rockwell Automation\Activations"
)
LICENSE_SUFFIX = ".lic"


def validate_license(*, activation_dir: Path | None = None) -> bool:
    """True when at least one FactoryTalk Activation license file is present.

    activation_dir is injectable so this is unit-testable without the real SDK.
    """
    directory = activation_dir if activation_dir is not None else DEFAULT_ACTIVATION_DIR
    if not directory.is_dir():
        return False
    return any(entry.suffix.lower() == LICENSE_SUFFIX for entry in directory.iterdir())


DEFAULT_WHEEL_DIR = Path(
    r"C:\Users\Public\Documents\Studio 5000\Logix Designer SDK\python"
)
DEFAULT_SERVER_DIR = Path(
    r"C:\Program Files (x86)\Rockwell Software\Studio 5000\Logix Designer SDK"
)
SERVER_EXE_NAME = "LdSdkServer.exe"
WHEEL_GLOB = "logix_designer_sdk-*.whl"
WHEEL_VERSION_RE = re.compile(r"^logix_designer_sdk-(?P<version>\d+\.\d+\.\d+)-")


@dataclass(frozen=True)
class SdkInfo:
    """Immutable result of SDK discovery."""

    wheel_path: Path
    server_exe_path: Path
    sdk_version: str
    python_compatible: bool
    license_present: bool


def _find_wheel(wheel_dir: Path) -> Path:
    if not wheel_dir.is_dir():
        raise SdkDiscoveryError(f"SDK wheel directory not found: {wheel_dir}")
    # First try to find the properly named wheels
    matches = sorted(wheel_dir.glob(WHEEL_GLOB))
    if matches:
        return matches[-1]
    # Fall back to any .whl file (for unparseable name error path)
    any_wheels = sorted(wheel_dir.glob("*.whl"))
    if any_wheels:
        return any_wheels[-1]
    raise SdkDiscoveryError(f"No SDK wheel matching {WHEEL_GLOB} in {wheel_dir}")


def _parse_wheel_version(wheel_path: Path) -> str:
    match = WHEEL_VERSION_RE.match(wheel_path.name)
    if match is None:
        raise SdkDiscoveryError(
            f"Cannot parse SDK version from wheel name: {wheel_path.name}"
        )
    return match.group("version")


def _find_server_exe(server_dir: Path) -> Path:
    exe = server_dir / SERVER_EXE_NAME
    if not exe.is_file():
        raise SdkDiscoveryError(f"{SERVER_EXE_NAME} not found under {server_dir}")
    return exe


def discover_sdk(
    *, wheel_dir: Path | None = None, server_dir: Path | None = None
) -> SdkInfo:
    """Locate the SDK wheel and server exe; report version/compat/license."""
    resolved_wheel_dir = wheel_dir if wheel_dir is not None else DEFAULT_WHEEL_DIR
    resolved_server_dir = server_dir if server_dir is not None else DEFAULT_SERVER_DIR

    wheel_path = _find_wheel(resolved_wheel_dir)
    server_exe_path = _find_server_exe(resolved_server_dir)
    sdk_version = _parse_wheel_version(wheel_path)

    return SdkInfo(
        wheel_path=wheel_path,
        server_exe_path=server_exe_path,
        sdk_version=sdk_version,
        python_compatible=validate_python_version(),
        license_present=validate_license(),
    )
