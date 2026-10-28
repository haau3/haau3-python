"""What a call gives back with its answer: the request id, timing and headers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Generic, TypeVar, final

import httpx2

from ._body import HeldHeaders

T_co = TypeVar("T_co", covariant=True)


@final
@dataclass(frozen=True)
class Response(Generic[T_co]):
    """An answer with what came with it, from `client.with_response.<method>(...)`.

    Its repr leaves out `data` and `headers`, so printing it never prints an answer.
    """

    data: T_co = field(repr=False)
    """The answer, as the method alone returns it."""
    request_id: str | None
    """The `X-Request-Id` the API sent. Quote it when you ask for help."""
    correlation_id: str | None
    """The `correlation_id` you sent, as the API returned it in `X-Correlation-Id`."""
    duration_ms: int
    """Milliseconds from the first attempt to the answer, waits between attempts included."""
    attempts: int
    """How many attempts were made."""
    status: int
    reason_phrase: str
    """The status's text: the answer's own, or the standard one (`"OK"`) when it came with none, as HTTP/2 answers do."""
    headers: httpx2.Headers = field(repr=False)


@final
@dataclass(frozen=True)
class RawResponse:
    """What the API answered to `request()`, whatever its status. Its repr leaves out the body and headers."""

    status: int
    reason_phrase: str
    """The status's text: `"OK"`, `"Not Found"`."""
    body: object = field(repr=False)
    """The body, parsed when it is JSON, else its text."""
    headers: httpx2.Headers = field(repr=False)
    request_id: str | None
    correlation_id: str | None
    attempts: int
    duration_ms: int


@final
@dataclass(frozen=True)
class Options:
    """The per-call options every method takes, as keywords."""

    timeout: float | None = None
    max_retries: int | None = None
    correlation_id: str | None = None
    extra_headers: Mapping[str, str] | None = field(default=None, repr=False)
    declared: HeldHeaders | None = field(default=None, repr=False)
    """The keywords of the headers the operation declares, held where nothing prints them."""
