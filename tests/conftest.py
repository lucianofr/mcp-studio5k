"""Shared test doubles. FakeLogixProject mirrors confirmed async signatures (spec §2)."""
from __future__ import annotations

import asyncio
from pathlib import Path


class FakeLogixProject:
    """Faithful async stand-in for logix_designer_sdk.LogixProject (spec §2)."""

    fail_open = False
    fail_import = False
    fail_save = False
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
    FakeLogixProject.fail_open = False
    FakeLogixProject.fail_import = False
    FakeLogixProject.fail_save = False
    FakeLogixProject.calls = []


class StubConfig:
    """Minimal Config surface used by ProjectSession."""

    def __init__(self, project_root: Path, backup_dir: Path) -> None:
        self.project_root = project_root
        self.backup_dir = backup_dir
        self.backup_rotation = 10
        self.safety_tag_exclusions: frozenset[str] = frozenset()
        self.max_l5x_bytes = 5_000_000
