"""The connection, retries and timing every operation shares: one engine per client, sync or async.

Everything that decides (encoding, verdicts, waits, messages) is in the pure modules; an engine only
sends, reads and waits. Its frames never hold a body or the key in a plain local variable when they
can raise, so a traceback that records locals shows neither.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast, final

import anyio
import httpx2

from ._batch import BatchResult, batch_bundle, read_batch
from ._body import HeldHeaders, Hidden, Secret
from ._encode import FHIR_BASE, QueryValue, encode_query, fill_path, is_fhir, is_uuid, json_bytes, path_of, url_on_base
from ._errors import Haau3Error, NoAnswerError, NoAnswerReason
from ._judge import Context, detail, judge, parse_body, retry_after_seconds, wait_after_answer, wait_after_failure
from ._operation import DeclaredHeader, Operation
from ._response import Options, RawResponse, Response
from ._version import __version__

T = TypeVar("T")
Method = Literal["GET", "POST"]

FHIR_JSON = "application/fhir+json"
USER_AGENT = (
    f"haau3-python/{__version__} (python {sys.version_info[0]}.{sys.version_info[1]}; httpx2 {httpx2.__version__})"
)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


async def _async_sleep(seconds: float) -> None:
    await anyio.sleep(seconds)


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


@final
@dataclass(frozen=True)
class Config:
    key: Secret
    base_url: str
    timeout: float
    max_retries: int


@final
class Prepared:
    """A request ready to send. Its repr names the request, never its headers (the key) or its body."""

    __slots__ = ("content", "headers", "max_retries", "method", "name", "timeout", "url")
    method: Method
    url: str
    name: str
    headers: httpx2.Headers
    content: Hidden[bytes] | None
    timeout: float
    max_retries: int

    def __init__(
        self,
        method: Method,
        url: str,
        name: str,
        headers: httpx2.Headers,
        content: Hidden[bytes] | None,
        timeout: float,
        max_retries: int,
    ) -> None:
        self.method = method
        self.url = url
        self.name = name
        self.headers = headers
        self.content = content
        self.timeout = timeout
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return f"<request {self.name}>"


@final
class Sent:
    """What came back from the last attempt. Its body is held, and its repr names only the status."""

    __slots__ = ("attempts", "content", "duration_ms", "headers", "reason_phrase", "status")
    status: int
    reason_phrase: str
    headers: httpx2.Headers
    content: Hidden[bytes]
    attempts: int
    duration_ms: int

    def __init__(
        self,
        status: int,
        reason_phrase: str,
        headers: httpx2.Headers,
        content: Hidden[bytes],
        attempts: int,
        duration_ms: int,
    ) -> None:
        self.status = status
        self.reason_phrase = reason_phrase
        self.headers = headers
        self.content = content
        self.attempts = attempts
        self.duration_ms = duration_ms

    def __repr__(self) -> str:
        return f"<answer {self.status}>"

    def context(self, missing: str, expects: Sequence[str] = ()) -> Context:
        return Context(
            request_id=self.headers.get("x-request-id"),
            correlation_id=self.headers.get("x-correlation-id"),
            attempts=self.attempts,
            duration_ms=self.duration_ms,
            retry_after=retry_after_seconds(self.headers.get("retry-after")),
            missing=missing,
            expects=expects,
        )

    def response(self, data: T) -> Response[T]:
        return Response(
            data=data,
            request_id=self.headers.get("x-request-id"),
            correlation_id=self.headers.get("x-correlation-id"),
            duration_ms=self.duration_ms,
            attempts=self.attempts,
            status=self.status,
            headers=self.headers,
        )


class _AttemptTimedOut(Exception):
    """A sync attempt's body was still arriving at its deadline."""


def _problem(header: DeclaredHeader, declared: HeldHeaders) -> str | None:
    """What is wrong with one declared header's value, in words that never carry it; None when it may go.

    Its own frame, so the value is gone before anything is raised: a traceback that shows each frame's
    local variables shows `declared`, which prints as `<headers hidden>`, and nothing else.
    """
    value = declared.reveal().get(header.option)
    if value is None:
        return None
    if header.max_length is not None and len(value) > header.max_length:
        return f"is longer than the {header.max_length} characters the API takes in {header.wire}."
    # A header value never holds a line break, and Python's `$` would match before a final one.
    if "\n" in value or "\r" in value or (header.pattern is not None and re.search(header.pattern, value) is None):
        return f"is not in the form the API takes in {header.wire}, which the keyword's description gives."
    return None


def declared_headers(operation: Operation[Any], options: Options) -> HeldHeaders | None:
    """The headers `operation` declares, from the call's own keywords, by header name and lower-cased.

    Each value set is checked against the spec's schema for it, and one the API would refuse is refused
    here, before anything is sent, in a message that names the keyword, never the value: a header such
    as `Payer-Authorization` carries a credential.
    """
    if options.declared is None or not operation.headers:
        return None
    for header in operation.headers:
        problem = _problem(header, options.declared)
        if problem is not None:
            raise Haau3Error(f"{operation.label} was not sent: {header.option} {problem}")
    values = options.declared.reveal()
    return HeldHeaders(
        {h.wire.lower(): values[h.option] for h in operation.headers if values.get(h.option) is not None}
    )


def prepare(
    config: Config,
    method: Method,
    url: str,
    content: Hidden[bytes] | None,
    options: Options,
    name: str,
    declared: HeldHeaders | None = None,
) -> Prepared:
    """Headers and limits for a request. A `correlation_id` that is not a UUID is refused before the key is touched."""
    if options.correlation_id is not None and not is_uuid(options.correlation_id):
        raise Haau3Error(
            f"{name} was not sent: correlation_id must be a UUID, which is all the API keeps (it drops anything else)."
        )
    fhir = is_fhir(path_of(config.base_url, url))
    headers = httpx2.Headers(
        {
            "accept": FHIR_JSON if fhir else "application/json",
            "authorization": f"Bearer {config.key.reveal()}",
            "user-agent": USER_AGENT,
        }
    )
    if content is not None:
        headers["content-type"] = FHIR_JSON if fhir else "application/json"
    for header, value in (options.extra_headers or {}).items():
        headers[header.lower()] = value
    # A header the operation declares is sent from its own keyword, over the same one in extra_headers.
    for wire, held in (declared.reveal() if declared is not None else {}).items():
        if held is not None:
            headers[wire] = held
    if options.correlation_id is not None:
        headers["x-request-id"] = options.correlation_id
    return Prepared(
        method,
        url,
        name,
        headers,
        content,
        options.timeout if options.timeout is not None else config.timeout,
        options.max_retries if options.max_retries is not None else config.max_retries,
    )


def body_bytes(body: Hidden[object] | None) -> Hidden[bytes] | None:
    """A request body as JSON bytes, held. A body that is not JSON is refused before anything is sent."""
    if body is None:
        return None
    problem = None
    try:
        return Hidden(json_bytes(body.reveal()))
    except (TypeError, ValueError) as error:
        problem = str(error)
    raise Haau3Error(f"The request body cannot be sent as JSON: {problem}")


def raw_content(body: object) -> Hidden[bytes] | None:
    """`request()`'s body: a str is sent as it is, anything else as JSON."""
    if body is None:
        return None
    if isinstance(body, str):
        return Hidden(body.encode("utf-8"))
    return body_bytes(Hidden(body))


def answer_of(name: str, expects: Sequence[str], sent: Sent) -> Hidden[object]:
    """The answer of a sent request, held, or the error it is, raised."""
    verdict = judge(
        name,
        sent.status,
        parse_body(sent.content, sent.headers.get("content-type")),
        sent.context("a body that is not JSON", expects),
    )
    if isinstance(verdict, Hidden):
        return verdict
    raise verdict


def raw_response(sent: Sent) -> RawResponse:
    parsed = parse_body(sent.content, sent.headers.get("content-type"))
    value = parsed.reveal() if parsed is not None else None
    return RawResponse(
        status=sent.status,
        reason_phrase=sent.reason_phrase,
        body=value if value is not None else sent.content.reveal().decode("utf-8", errors="replace"),
        headers=sent.headers,
        request_id=sent.headers.get("x-request-id"),
        correlation_id=sent.headers.get("x-correlation-id"),
        attempts=sent.attempts,
        duration_ms=sent.duration_ms,
    )


def _no_answer(name: str, reason: NoAnswerReason, what: str, attempts: int, started: float) -> NoAnswerError:
    return NoAnswerError(
        f"{name} got no answer ({reason}) after {attempts} attempt{'' if attempts == 1 else 's'}.",
        reason=reason,
        attempts=attempts,
        duration_ms=_elapsed_ms(started),
        detail=what,
    )


class _Engine:
    """What both engines share: where requests go, and how a URL is checked."""

    def __init__(self, config: Config) -> None:
        self._config = config

    @property
    def config(self) -> Config:
        return self._config

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._config.base_url}>"

    def operation_url(
        self, operation: Operation[Any], path_values: Sequence[str], query: Iterable[tuple[str, QueryValue]]
    ) -> str:
        return url_on_base(self._config.base_url, fill_path(operation.path, path_values), encode_query(query))

    def resolve(self, path_or_url: str, query: Iterable[tuple[str, QueryValue]] = ()) -> str:
        return url_on_base(self._config.base_url, path_or_url, encode_query(query))

    def name(self, method: Method, url: str) -> str:
        return f"{method} {path_of(self._config.base_url, url)}"


@final
class SyncEngine(_Engine):
    def __init__(self, config: Config, http: httpx2.Client | None) -> None:
        super().__init__(config)
        self._owns = http is None
        self._http = (
            http if http is not None else httpx2.Client(timeout=httpx2.Timeout(config.timeout), follow_redirects=False)
        )

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def call(
        self,
        operation: Operation[T],
        path_values: Sequence[str],
        query: Iterable[tuple[str, QueryValue]],
        body: Hidden[object] | None,
        options: Options,
    ) -> Response[T]:
        url = self.operation_url(operation, path_values, query)
        declared = declared_headers(operation, options)
        sent = self.send(operation.method, url, body_bytes(body), options, operation.label, declared)
        return sent.response(cast("T", answer_of(operation.label, operation.expects, sent).reveal()))

    def batch(self, calls: Sequence[object], options: Options) -> Response[tuple[BatchResult[Any], ...]]:
        checked, bundle = batch_bundle(calls)
        sent = self.send("POST", self.resolve(FHIR_BASE), body_bytes(bundle), options, "POST /v1/fhir/r4")
        answer = answer_of("POST /v1/fhir/r4", ("Bundle",), sent)
        return sent.response(read_batch(checked, answer, status=sent.status, context=sent.context("no resource")))

    def next_page(self, url: str, options: Options) -> Hidden[object]:
        name = self.name("GET", url)
        return answer_of(name, ("Bundle",), self.send("GET", url, None, options, name))

    def request(
        self, method: Method, path: str, query: Iterable[tuple[str, QueryValue]], body: object, options: Options
    ) -> RawResponse:
        url = self.resolve(path, query)
        return raw_response(self.send(method, url, raw_content(body), options, self.name(method, url)))

    def send(
        self,
        method: Method,
        url: str,
        content: Hidden[bytes] | None,
        options: Options,
        name: str,
        declared: HeldHeaders | None = None,
    ) -> Sent:
        prepared = prepare(self._config, method, url, content, options, name, declared)
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            failure: tuple[NoAnswerReason, str] | None = None
            sent: Sent | None = None
            try:
                sent = self._attempt(prepared, attempt, started)
            except (httpx2.TimeoutException, _AttemptTimedOut) as error:
                failure = ("timeout", detail(error))
            except httpx2.RequestError as error:
                failure = ("network", detail(error))
            if sent is not None:
                wait = wait_after_answer(attempt, prepared.max_retries, sent.status, sent.headers.get("retry-after"))
                if wait is None:
                    return sent
            else:
                wait = wait_after_failure(attempt, prepared.max_retries)
                if wait is None:
                    break
            _sleep(wait)
        assert failure is not None
        # Raised here, outside the `except`, so it carries no chained traceback: those frames hold the request.
        raise _no_answer(name, failure[0], failure[1], attempt, started)

    def _attempt(self, prepared: Prepared, attempt: int, started: float) -> Sent:
        deadline = time.monotonic() + prepared.timeout
        request = self._http.build_request(
            prepared.method,
            prepared.url,
            headers=prepared.headers,
            content=prepared.content.reveal() if prepared.content is not None else None,
            timeout=httpx2.Timeout(prepared.timeout),
        )
        response = self._http.send(request, stream=True, follow_redirects=False)
        try:
            received = bytearray()
            for chunk in response.iter_bytes():
                received += chunk
                if time.monotonic() > deadline:
                    raise _AttemptTimedOut
            return Sent(
                response.status_code,
                response.reason_phrase,
                response.headers,
                Hidden(bytes(received)),
                attempt,
                _elapsed_ms(started),
            )
        finally:
            response.close()


@final
class AsyncEngine(_Engine):
    def __init__(self, config: Config, http: httpx2.AsyncClient | None) -> None:
        super().__init__(config)
        self._owns = http is None
        self._http = (
            http
            if http is not None
            else httpx2.AsyncClient(timeout=httpx2.Timeout(config.timeout), follow_redirects=False)
        )

    async def close(self) -> None:
        if self._owns:
            await self._http.aclose()

    async def call(
        self,
        operation: Operation[T],
        path_values: Sequence[str],
        query: Iterable[tuple[str, QueryValue]],
        body: Hidden[object] | None,
        options: Options,
    ) -> Response[T]:
        url = self.operation_url(operation, path_values, query)
        declared = declared_headers(operation, options)
        sent = await self.send(operation.method, url, body_bytes(body), options, operation.label, declared)
        return sent.response(cast("T", answer_of(operation.label, operation.expects, sent).reveal()))

    async def batch(self, calls: Sequence[object], options: Options) -> Response[tuple[BatchResult[Any], ...]]:
        checked, bundle = batch_bundle(calls)
        sent = await self.send("POST", self.resolve(FHIR_BASE), body_bytes(bundle), options, "POST /v1/fhir/r4")
        answer = answer_of("POST /v1/fhir/r4", ("Bundle",), sent)
        return sent.response(read_batch(checked, answer, status=sent.status, context=sent.context("no resource")))

    async def next_page(self, url: str, options: Options) -> Hidden[object]:
        name = self.name("GET", url)
        return answer_of(name, ("Bundle",), await self.send("GET", url, None, options, name))

    async def request(
        self, method: Method, path: str, query: Iterable[tuple[str, QueryValue]], body: object, options: Options
    ) -> RawResponse:
        url = self.resolve(path, query)
        return raw_response(await self.send(method, url, raw_content(body), options, self.name(method, url)))

    async def send(
        self,
        method: Method,
        url: str,
        content: Hidden[bytes] | None,
        options: Options,
        name: str,
        declared: HeldHeaders | None = None,
    ) -> Sent:
        prepared = prepare(self._config, method, url, content, options, name, declared)
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            failure: tuple[NoAnswerReason, str] | None = None
            sent: Sent | None = None
            try:
                sent = await self._attempt(prepared, attempt, started)
            except (httpx2.TimeoutException, TimeoutError) as error:
                failure = ("timeout", detail(error))
            except httpx2.RequestError as error:
                failure = ("network", detail(error))
            if sent is not None:
                wait = wait_after_answer(attempt, prepared.max_retries, sent.status, sent.headers.get("retry-after"))
                if wait is None:
                    return sent
            else:
                wait = wait_after_failure(attempt, prepared.max_retries)
                if wait is None:
                    break
            await _async_sleep(wait)
        assert failure is not None
        # Raised here, outside the `except`, so it carries no chained traceback: those frames hold the request.
        raise _no_answer(name, failure[0], failure[1], attempt, started)

    async def _attempt(self, prepared: Prepared, attempt: int, started: float) -> Sent:
        # The whole attempt, sending and reading the body, ends at its timeout exactly.
        with anyio.fail_after(prepared.timeout):
            request = self._http.build_request(
                prepared.method,
                prepared.url,
                headers=prepared.headers,
                content=prepared.content.reveal() if prepared.content is not None else None,
                timeout=httpx2.Timeout(prepared.timeout),
            )
            response = await self._http.send(request, stream=True, follow_redirects=False)
            try:
                received = bytearray()
                async for chunk in response.aiter_bytes():
                    received += chunk
                return Sent(
                    response.status_code,
                    response.reason_phrase,
                    response.headers,
                    Hidden(bytes(received)),
                    attempt,
                    _elapsed_ms(started),
                )
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
