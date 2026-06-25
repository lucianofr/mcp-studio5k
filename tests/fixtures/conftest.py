"""Pytest fixtures exposing real sample L5X files as strings."""
from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).parent


def _read_sample(filename: str) -> str:
    return (_FIXTURE_DIR / filename).read_text(encoding="utf-8")


@pytest.fixture
def st_gearchange_l5x() -> str:
    """Real minimal Structured Text sample routine."""
    return _read_sample("ST_GearChange.L5X")


@pytest.fixture
def ld_scale_value_l5x() -> str:
    """Real minimal Ladder (RLL) sample routine."""
    return _read_sample("LD_Scale_Value.L5X")
