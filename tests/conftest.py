"""Shared test doubles. FakeLogixProject mirrors confirmed async signatures (spec §2)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def _mode_name(mode) -> str:
    """Render an OperationMode/RequestedControllerMode enum or raw string uniformly."""
    return getattr(mode, "name", str(mode))


class FakeLogixProject:
    """Faithful async stand-in for logix_designer_sdk.LogixProject (spec §2)."""

    fail_open = False
    fail_import = False
    fail_save = False
    fail_save_as = False
    fail_live_op = False
    fail_upload = False
    fail_convert = False
    fail_static = False
    comm_path = ""
    controller_mode = "PROGRAM"
    connected_state = "OFFLINE"
    safety_locked = False
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
        FakeLogixProject.calls.append(f"get_tag_value_bool:{tag_path}:{_mode_name(mode)}")
        return True

    async def get_tag_value_dint(self, tag_path, mode=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_tag_value_dint:{tag_path}:{_mode_name(mode)}")
        return 42

    # ---- v31 live/comm/tag surface -----------------------------------------

    def __getattr__(self, name: str):
        """Typed get/set tag fallbacks so all 12 SDK data types are covered
        without 24 near-identical bodies. Records calls like the explicit ones.
        Only tag accessors are synthesized; anything else raises AttributeError
        so typos in session code still fail loudly."""
        if name.startswith("get_tag_value_"):
            suffix = name.removeprefix("get_tag_value_")

            async def _get(tag_path, mode=None):
                await asyncio.sleep(0)
                FakeLogixProject.calls.append(f"{name}:{tag_path}:{_mode_name(mode)}")
                defaults = {
                    "string": "abc", "real": 1.5, "lreal": 2.5, "usint": b"\x07",
                }
                return defaults.get(suffix, 7)

            return _get
        if name.startswith("set_tag_value_"):
            async def _set(tag_path, mode=None, tag_value=None):
                await asyncio.sleep(0)
                FakeLogixProject.calls.append(
                    f"{name}:{tag_path}:{_mode_name(mode)}:{tag_value!r}"
                )
                if FakeLogixProject.fail_live_op:
                    raise RuntimeError("SDK set tag failed")

            return _set
        raise AttributeError(name)

    async def get_communications_path(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("get_communications_path")
        return FakeLogixProject.comm_path

    async def set_communications_path(self, project_path):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"set_communications_path:{project_path}")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK set_communications_path failed")
        FakeLogixProject.comm_path = project_path

    async def read_controller_mode(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("read_controller_mode")

        class _M:  # minimal enum-like: .name only, mirrors ControllerMode member
            name = FakeLogixProject.controller_mode

        return _M()

    async def change_controller_mode(self, mode):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"change_controller_mode:{_mode_name(mode)}")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK change_controller_mode failed")
        FakeLogixProject.controller_mode = _mode_name(mode)

    async def read_connected_state(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("read_connected_state")

        class _S:
            name = FakeLogixProject.connected_state

        return _S()

    async def change_controller_type(self, processor_type_name):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"change_controller_type:{processor_type_name}")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK change_controller_type failed")

    async def go_online(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("go_online")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK go_online failed")

    async def go_offline(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("go_offline")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK go_offline failed")

    async def download(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("download")
        if FakeLogixProject.fail_live_op:
            raise RuntimeError("SDK download failed")

    async def upload(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("upload")
        if FakeLogixProject.fail_upload:
            raise RuntimeError("SDK upload failed")

    async def is_safety_locked(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("is_safety_locked")
        return FakeLogixProject.safety_locked

    async def get_safety_network_number(self, module_name):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_safety_network_number:{module_name}")
        return "3939_0311_1223"

    async def get_safety_signature(self):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append("get_safety_signature")
        return "16#abcd_1234"

    async def partial_import_rungs_from_xml_file(
        self, x_path, insert_position, replace_count, xml_file_to_import, online_import_option
    ):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(
            f"import_rungs:{x_path}:{insert_position}:{replace_count}:{_mode_name(online_import_option)}"
        )
        if FakeLogixProject.fail_import:
            raise RuntimeError("SDK rungs import failed")

    async def partial_import_with_target_from_xml_file(
        self, x_path, target_name, xml_file_to_import, online_import_option
    ):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(
            f"import_with_target:{x_path}:{target_name}:{_mode_name(online_import_option)}"
        )
        if FakeLogixProject.fail_import:
            raise RuntimeError("SDK target import failed")

    # ---- v31 static surface -------------------------------------------------

    @staticmethod
    async def get_processor_types(major_rev):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"get_processor_types:{major_rev}")
        if FakeLogixProject.fail_static:
            raise RuntimeError("SDK get_processor_types failed")

        class _PT:
            product_code = 94
            product_type = 14
            id = 1
            name = "1756-L85E"

        return {"1756-L85E": _PT()}

    @staticmethod
    async def convert(project_path, destination_revision, operation_events=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(f"convert:{project_path}:{destination_revision}")
        if FakeLogixProject.fail_convert:
            raise RuntimeError("SDK convert failed")
        return FakeLogixProject(str(project_path))

    @staticmethod
    async def upload_to_new_project(project_file_path, comm_path, operation_events=None):
        await asyncio.sleep(0)
        FakeLogixProject.calls.append(
            f"upload_to_new_project:{project_file_path}:{comm_path}"
        )
        if FakeLogixProject.fail_static:
            raise RuntimeError("SDK upload_to_new_project failed")
        return FakeLogixProject(str(project_file_path))


def reset_fake() -> None:
    """Reset class-level state on FakeLogixProject; call between test files — FakeLogixProject.calls is CLASS-LEVEL and persists across tests without this."""
    FakeLogixProject.fail_open = False
    FakeLogixProject.fail_import = False
    FakeLogixProject.fail_save = False
    FakeLogixProject.fail_save_as = False
    FakeLogixProject.fail_live_op = False
    FakeLogixProject.fail_upload = False
    FakeLogixProject.fail_convert = False
    FakeLogixProject.fail_static = False
    FakeLogixProject.comm_path = ""
    FakeLogixProject.controller_mode = "PROGRAM"
    FakeLogixProject.connected_state = "OFFLINE"
    FakeLogixProject.safety_locked = False
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
