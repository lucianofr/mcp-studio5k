import pytest

from mcp_studio5k.envelope import Meta, err_envelope, ok_envelope


def test_ok_envelope_has_full_shape_with_default_meta():
    result = ok_envelope({"items": [1, 2]})
    assert result == {
        "ok": True,
        "data": {"items": [1, 2]},
        "error": None,
        "meta": {"total": None, "page": None, "truncated": False, "size_bytes": None},
    }


def test_err_envelope_carries_error_and_null_data():
    result = err_envelope("path outside PROJECT_ROOT")
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"] == "path outside PROJECT_ROOT"
    assert result["meta"]["truncated"] is False


def test_meta_values_are_serialized_into_dict():
    meta = Meta(total=12, page="cursor:eyJv", truncated=True, size_bytes=2048)
    result = ok_envelope([], meta=meta)
    assert result["meta"] == {
        "total": 12, "page": "cursor:eyJv", "truncated": True, "size_bytes": 2048
    }


def test_meta_is_frozen():
    import dataclasses

    meta = Meta(total=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        meta.total = 2  # type: ignore[misc]
