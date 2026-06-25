"""Shared test doubles. FakeLogixProject mirrors confirmed async signatures (spec §2)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


class FakeLogixProject:
    """Faithful async stand-in for logix_designer_sdk.LogixProject (spec §2)."""

    fail_open = False
    fail_import = False
    fail_save = False
    fail_save_as = False
    calls: list[str] = []

    def __init__(self, project_file_path: str) -> None:
        self.project_file_path = project_file_path
        self.closed = False

    @staticmethod
    async def open_logix_project(project_file_path, operation_events=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"open:{project_file_path}")
        if FakeLogixProject.fail_open:
            raise RuntimeError("SDK open failed")
        return FakeLogixProject(str(project_file_path))

    @staticmethod
    async def create_new_project(
        project_file_path, major_revision, processor_type_name, controller_name,
        operation_events=None,
    ):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(
            f"create:{project_file_path}:{major_revision}:{processor_type_name}:{controller_name}"
        )
        return FakeLogixProject(str(project_file_path))

    async def save(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("save")
        if FakeLogixProject.fail_save:
            raise RuntimeError("SDK save failed")

    async def save_as(self, save_path, force=False, detailed_l5x=False):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"save_as:{save_path}:{force}:{detailed_l5x}")
        if FakeLogixProject.fail_save_as:
            raise RuntimeError("SDK save_as failed")

    async def close(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("close")
        self.closed = True

    async def partial_export_to_xml_file(self, x_path, file_path):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"export:{x_path}:{file_path}")

    async def partial_import_from_xml_file(
        self, x_path, xml_file_to_import, collision_option, continue_on_errors=False
    ):
        await asyncio.sleep(0)
        # xml_file_to_import is a FILE PATH (U1 fix); read content for traceability.
        try:
            _content = Path(xml_file_to_import).read_text(encoding="utf-8")
        except OSError:
            _content = ""
        FakeLogixProject.calls.append(f"import:{x_path}:{collision_option}")
        if FakeLogixProject.fail_import:
            raise RuntimeError("SDK import failed")

    async def get_tag_value_bool(self, tag_path, mode=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_tag_value_bool:{tag_path}:{mode}")
        return True

    async def get_tag_value_dint(self, tag_path, mode=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_tag_value_dint:{tag_path}:{mode}")
        return 42


def reset_fake() -> None:
    """Reset class-level state on FakeLogixProject; call between test files — FakeLogixProject.calls is CLASS-LEVEL and persists across tests without this."""
    FakeLogixProject.fail_open = False
    FakeLogixProject.fail_import = False
    FakeLogixProject.fail_save = False
    FakeLogixProject.fail_save_as = False
    FakeLogixProject.calls = []


class StubConfig:
    """Minimal Config surface used by ProjectSession."""

    def __init__(self, project_root: Path, backup_dir: Path) -> None:
        self.project_root = project_root
        self.backup_dir = backup_dir
        self.backup_rotation = 10
        self.safety_tag_exclusions: frozenset[str] = frozenset()
        self.max_l5x_bytes = 5_000_000


# ---------------------------------------------------------------------------
# mock_session — reusable AsyncMock ProjectSession double (Task 18+)
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def mock_session():
    """ProjectSession double: partial_export(x_path) -> L5X string per routed fixture.

    Usage in tests::

        mock_session._routes["Programs"] = "programs_export.L5X"
        result = await list_programs(mock_session)

    The ``_routes`` dict maps an xpath substring (needle) to a fixture filename
    under ``tests/fixtures/``.  Any x_path that contains the needle returns the
    fixture content; an unrouted path raises AssertionError to fail fast.
    """
    session = AsyncMock()
    session._routes: dict[str, str] = {}

    async def _partial_export(x_path: str) -> str:
        for needle, fixture in session._routes.items():
            if needle in x_path:
                return _load_fixture(fixture)
        raise AssertionError(f"no fixture routed for x_path={x_path!r}")

    session.partial_export = AsyncMock(side_effect=_partial_export)
    return session
