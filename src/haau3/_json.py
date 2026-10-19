"""Reading JSON the API answered with, without trusting its shape."""

from __future__ import annotations

from typing import cast


def as_dict(value: object) -> dict[str, object] | None:
    """The value as a JSON object, or None when it is not one."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[object] | None:
    """The value as a JSON array, or None when it is not one."""
    return cast("list[object]", value) if isinstance(value, list) else None
