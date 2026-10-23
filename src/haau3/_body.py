"""Holders that keep a body or the API key out of anything that prints.

A body the API answered with can repeat what the request carried, and for some operations that is a
patient's data. So from the moment the SDK reads a body until it hands the answer back, the body
lives in a `Hidden`: its repr is `<body hidden>`, so a traceback that shows each frame's local
variables (`traceback`'s `capture_locals`, or an error reporter that records them) shows that and
nothing else. The same goes for a request body, for the key in a `Secret`, and for the headers a
call's own options carry, such as a member's authorization, in `HeldHeaders`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, NoReturn, TypeVar, final

T_co = TypeVar("T_co", covariant=True)


@final
class Hidden(Generic[T_co]):
    """A value that is never printed: its repr, str and format are `<body hidden>`."""

    __slots__ = ("_value",)

    def __init__(self, value: T_co) -> None:
        self._value = value

    def reveal(self) -> T_co:
        return self._value

    def __repr__(self) -> str:
        return "<body hidden>"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return "<body hidden>"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a hidden body is never pickled or copied")


@final
class HeldHeaders:
    """Headers a call's own options carry, by option or by header name: printed as `<headers hidden>`, never pickled."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str | None]) -> None:
        self._values = dict(values)

    def reveal(self) -> dict[str, str | None]:
        return dict(self._values)

    def __repr__(self) -> str:
        return "<headers hidden>"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return "<headers hidden>"

    def __reduce__(self) -> NoReturn:
        raise TypeError("held headers are never pickled or copied")


@final
class Secret:
    """The API key: its repr, str and format are `<key hidden>`, and it is never pickled."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<key hidden>"

    __str__ = __repr__

    def __format__(self, format_spec: str) -> str:
        return "<key hidden>"

    def __reduce__(self) -> NoReturn:
        raise TypeError("the API key is never pickled or copied")
