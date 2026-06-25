from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Meta:
    total: int | None = None
    page: str | None = None  # next cursor
    truncated: bool = False
    size_bytes: int | None = None


def _meta_dict(meta: "Meta | None") -> dict:
    return asdict(meta if meta is not None else Meta())


def ok_envelope(data, *, meta: "Meta | None" = None) -> dict:
    return {"ok": True, "data": data, "error": None, "meta": _meta_dict(meta)}


def err_envelope(error: str, *, meta: "Meta | None" = None) -> dict:
    return {"ok": False, "data": None, "error": error, "meta": _meta_dict(meta)}
