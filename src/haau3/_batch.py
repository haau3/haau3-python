"""Several operations of the FHIR base in one request, each with its own answer or error."""

# pyright: reportPrivateUsage=false
# (The functions below read a Call's own fields, which the public never sees.)

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeAlias, TypeVar, Union, cast, final

from ._body import Hidden
from ._encode import FHIR_BASE, QueryValue, encode_query, fill_path
from ._errors import Haau3Error, NoAnswerError, RefusedError
from ._json import as_dict, as_list
from ._judge import Context, judge
from ._operation import Operation

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

_STATUS = re.compile(r"(\d{3})(?:\s|$)", re.A)


@final
class Call(Generic[T_co]):
    """One operation, ready to go in a batch. Made by `haau3.calls`, and sent only by `batch()`.

    `T` is what the operation answers with. Its repr names the operation, never a value you passed.
    """

    __slots__ = ("_body", "_expects", "_label", "_method", "_url")

    def __init__(self, operation: Operation[Any], url: str, body: Hidden[object] | None) -> None:
        self._method: Literal["GET", "POST"] = operation.method
        self._url = url
        self._label = operation.label
        self._expects = operation.expects
        self._body = body

    @property
    def method(self) -> Literal["GET", "POST"]:
        return self._method

    @property
    def url(self) -> str:
        """Where the entry goes, relative to the FHIR base, as the batch sends it."""
        return self._url

    def __repr__(self) -> str:
        return f"<Call {self._label}>"


def make_call(
    operation: Operation[T],
    path_values: Sequence[str],
    query: Iterable[tuple[str, QueryValue]],
    body: Hidden[object] | None,
) -> Call[T]:
    """The entry for one operation: the same path and query a direct call sends, relative to the FHIR base."""
    path = fill_path(operation.path, path_values)
    relative = path[len(FHIR_BASE) + 1 :]
    q = encode_query(query)
    return Call(operation, f"{relative}?{q}" if q else relative, body)


@final
@dataclass(frozen=True)
class BatchOk(Generic[T]):
    """An entry that answered. Its repr leaves out `data`."""

    data: T = field(repr=False)
    status: int
    ok: Literal[True] = field(default=True, init=False)


@final
@dataclass(frozen=True)
class BatchError:
    """An entry that failed, with the error the call alone would have raised."""

    error: RefusedError | NoAnswerError
    ok: Literal[False] = field(default=False, init=False)


BatchResult: TypeAlias = Union[BatchOk[T], BatchError]
"""One entry's outcome: its answer, or the error a single call would have raised."""


def batch_bundle(calls: Sequence[object]) -> tuple[list[Call[object]], Hidden[object]]:
    """The batch Bundle for these entries. Refuses anything `haau3.calls` did not make."""
    checked: list[Call[object]] = []
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, Call):
            raise Haau3Error(f"Entry {index} of the batch was not made by haau3.calls.")
        checked.append(cast("Call[object]", call))
    return checked, Hidden({"resourceType": "Bundle", "type": "batch", "entry": [_entry(call) for call in checked]})


def _entry(call: Call[object]) -> dict[str, object]:
    entry: dict[str, object] = {"request": {"method": call.method, "url": call.url}}
    if call._body is not None:
        entry["resource"] = call._body.reveal()
    return entry


def _shape(answer: Hidden[object]) -> tuple[bool, int]:
    """Whether the answer is a batch-response, and how many entries it holds (-1 when `entry` is not a list)."""
    value = as_dict(answer.reveal())
    if value is None or value.get("type") != "batch-response":
        return False, 0
    if value.get("entry") is None:
        return True, 0
    entries = as_list(value.get("entry"))
    return True, len(entries) if entries is not None else -1


def _entry_answer(answer: Hidden[object], index: int) -> tuple[int | None, Hidden[object] | None]:
    """One entry's status ("200", or "200 OK" as FHIR allows) and its body, held."""
    value = as_dict(answer.reveal())
    entries = as_list(value.get("entry")) if value is not None else None
    entry = as_dict(entries[index]) if entries is not None else None
    if entry is None:
        return None, None
    response = as_dict(entry.get("response"))
    status = response.get("status") if response is not None else None
    match = _STATUS.match(status) if isinstance(status, str) else None
    code = int(match.group(1)) if match else None
    # A failed entry's OperationOutcome is its resource, or, as FHIR also allows, its response's outcome.
    body = entry.get("resource")
    if body is None and code is not None and code >= 400 and response is not None:
        body = response.get("outcome")
    return code, (Hidden(body) if body is not None else None)


def read_batch(
    calls: Sequence[Call[object]], answer: Hidden[object], *, status: int, context: Context
) -> tuple[BatchResult[Any], ...]:
    """One result per entry sent, paired by position as FHIR R4 says a batch-response is.

    An answer that is not a batch-response, or holds another number of entries, cannot be paired at
    all and is no answer for the whole batch.
    """
    c = context
    is_batch, count = _shape(answer)

    def no_answer(message: str) -> NoAnswerError:
        return NoAnswerError(
            message,
            reason="unreadable",
            status=status,
            attempts=c.attempts,
            duration_ms=c.duration_ms,
            request_id=c.request_id,
            correlation_id=c.correlation_id,
        )

    if not is_batch:
        raise no_answer("POST /v1/fhir/r4 answered a Bundle that is not a batch-response.")
    if count < 0:
        raise no_answer("POST /v1/fhir/r4 answered a batch-response whose entries are not a list.")
    if count != len(calls):
        raise no_answer(f"POST /v1/fhir/r4 answered {count} entries for a batch of {len(calls)}.")
    results: list[BatchResult[Any]] = []
    for index, call in enumerate(calls):
        name = f"{call._label} (entry {index + 1} of {len(calls)})"
        code, body = _entry_answer(answer, index)
        if code is None:
            results.append(
                BatchError(
                    NoAnswerError(
                        f"{name} answered with no status.",
                        reason="unreadable",
                        attempts=c.attempts,
                        duration_ms=c.duration_ms,
                        request_id=c.request_id,
                        correlation_id=c.correlation_id,
                    )
                )
            )
            continue
        verdict = judge(
            name,
            code,
            body,
            Context(
                request_id=c.request_id,
                correlation_id=c.correlation_id,
                attempts=c.attempts,
                duration_ms=c.duration_ms,
                missing="no resource",
                expects=call._expects,
                entry=True,
            ),
        )
        if isinstance(verdict, Hidden):
            results.append(BatchOk(verdict.reveal(), code))
        elif isinstance(verdict, (RefusedError, NoAnswerError)):
            results.append(BatchError(verdict))
        else:  # An entry is never judged an AuthError; the key is checked once, for the whole batch.
            raise AssertionError("an entry was judged an AuthError")
    return tuple(results)
