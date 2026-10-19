"""What the tests share: a fake key, answers, a client of either kind, and the spec's examples."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import httpx2

from haau3 import AsyncHaau3, Haau3

ROOT = Path(__file__).resolve().parent.parent
KEY = "haau3_sk_test_key_that_must_never_be_printed"
REQUEST_ID = "6b1f3c2e-4d5a-4e6f-8a9b-0c1d2e3f4a5b"
SPEC: dict[str, Any] = json.loads((ROOT / "openapi" / "openapi.json").read_text(encoding="utf-8"))

Kind = Literal["sync", "async"]
Handler = Callable[[httpx2.Request], httpx2.Response]


def answer(
    status: int = 200, body: object = None, *, headers: dict[str, str] | None = None, fhir: bool = True
) -> httpx2.Response:
    """An answer as the API sends one: JSON, with a request id."""
    content_type = "application/fhir+json" if fhir else "application/json"
    all_headers = {"x-request-id": REQUEST_ID, "content-type": content_type, **(headers or {})}
    content = b"" if body is None else json.dumps(body).encode()
    return httpx2.Response(status, headers=all_headers, content=content)


class Recorder:
    """A MockTransport handler that answers in turn and keeps every request it was sent."""

    def __init__(self, *answers: httpx2.Response | Handler | BaseException) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        self.requests.append(request)
        given = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if isinstance(given, BaseException):
            raise given
        if isinstance(given, httpx2.Response):
            return httpx2.Response(given.status_code, headers=given.headers, content=given.content)
        return given(request)

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]

    def query(self, index: int = -1) -> list[tuple[str, str]]:
        from urllib.parse import parse_qsl

        return parse_qsl(self.requests[index].url.query.decode(), keep_blank_values=True)


def sync_client(handler: Handler, **options: Any) -> Haau3:
    options.setdefault("api_key", KEY)
    return Haau3(http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **options)


def async_client(handler: Handler, **options: Any) -> AsyncHaau3:
    options.setdefault("api_key", KEY)
    return AsyncHaau3(http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), **options)


class Client:
    """A test's client, sync or async, called the same way from an async test."""

    def __init__(self, kind: Kind | str, handler: Handler, **options: Any) -> None:
        self.kind = kind
        self.inner: Haau3 | AsyncHaau3 = (
            sync_client(handler, **options) if kind == "sync" else async_client(handler, **options)
        )

    async def _done(self, result: Any) -> Any:
        return await result if self.kind == "async" else result

    async def __call__(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return await self._done(getattr(self.inner, method)(*args, **kwargs))

    async def with_response(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return await self._done(getattr(self.inner.with_response, method)(*args, **kwargs))

    async def pages(self, first: Any, **kwargs: Any) -> list[Any]:
        if isinstance(self.inner, Haau3):
            return list(self.inner.pages(first, **kwargs))
        return [page async for page in self.inner.pages(first, **kwargs)]

    async def resources(self, first: Any, **kwargs: Any) -> list[Any]:
        if isinstance(self.inner, Haau3):
            return list(self.inner.resources(first, **kwargs))
        return [resource async for resource in self.inner.resources(first, **kwargs)]

    async def raises(self, method: str, *args: Any, **kwargs: Any) -> BaseException:
        try:
            await self(method, *args, **kwargs)
        except BaseException as error:
            return error
        raise AssertionError(f"{method} did not raise")


# --- the spec's examples -----------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def operations() -> Iterator[tuple[str, str, str, dict[str, Any]]]:
    """(operationId, method, path, operation) for every operation of the pinned spec."""
    for path, item in SPEC["paths"].items():
        for method in ("get", "post"):
            if method in item:
                yield item[method]["operationId"], method.upper(), path, item[method]


def first_example(media: dict[str, Any]) -> Any:
    if "example" in media:
        return media["example"]
    return next(iter(media["examples"].values()))["value"]


def example_answer(op: dict[str, Any]) -> Any:
    return first_example(next(iter(op["responses"]["200"]["content"].values())))


def example_body(op: dict[str, Any]) -> Any:
    return first_example(next(iter(op["requestBody"]["content"].values())))


def _matches(template: str, path: str) -> bool:
    parts = _PLACEHOLDER.split(template)
    pattern = "".join(re.escape(part) if i % 2 == 0 else "[^/]+" for i, part in enumerate(parts))
    return re.fullmatch(pattern, path) is not None


def spec_answer(method: str, path: str) -> Any:
    """The spec's example answer for a request to this method and path."""
    for _, op_method, template, op in operations():
        if op_method == method and _matches(template, path):
            return example_answer(op)
    raise LookupError(f"no operation answers {method} {path}")


def _without_next(value: Any) -> Any:
    # A next link in an example points at a live page; the tests read only the example itself.
    if isinstance(value, dict) and isinstance(value.get("link"), list):
        return {**value, "link": [link for link in value["link"] if link.get("relation") != "next"]}
    return value


def spec_example_handler(request: httpx2.Request) -> httpx2.Response:
    """Answers any request the way the spec's examples say the API does."""
    request.read()
    path = request.url.path
    if request.method == "POST" and path == "/v1/fhir/r4":
        batch = json.loads(request.content)
        entries = []
        for entry in batch.get("entry", []):
            relative = entry["request"]["url"].split("?", 1)[0]
            resource = spec_answer(entry["request"]["method"], "/v1/fhir/r4/" + relative)
            entries.append({"resource": _without_next(resource), "response": {"status": "200 OK"}})
        return answer(200, {"resourceType": "Bundle", "type": "batch-response", "entry": entries})
    body = _without_next(spec_answer(request.method, path))
    return answer(200, body, fhir=path.startswith("/v1/fhir/"))
