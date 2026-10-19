"""The two clients: `Haau3` for code that calls and waits, `AsyncHaau3` for code that awaits."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Literal, cast

import httpx2

from ._body import Hidden, Secret
from ._encode import QueryValue, checked_base_url
from ._errors import Haau3Error, NoAnswerError
from ._json import as_dict, as_list
from ._methods import AsyncMethods, AsyncWithResponse, SyncMethods, SyncWithResponse
from ._response import Options, RawResponse
from ._transport import AsyncEngine, Config, SyncEngine

if TYPE_CHECKING:
    from typing_extensions import Self

    from .fhir import Bundle, Resource

DEFAULT_BASE_URL = "https://api.haau3.com"
DEFAULT_TIMEOUT = 65.0
"""Seconds one attempt may take. The API gives a batch 60 seconds, so this is a little longer."""
DEFAULT_MAX_RETRIES = 1


def _config(api_key: str | None, base_url: str | None, timeout: float | None, max_retries: int | None) -> Config:
    if sys.platform == "emscripten":
        raise Haau3Error(
            "haau3 runs on a server only: an API key in a browser is public. Call the API from your server."
        )
    key = api_key if api_key is not None else (os.environ.get("HAAU3_API_KEY") or None)
    if not key:
        raise Haau3Error("No API key: pass api_key, or set HAAU3_API_KEY.")
    return Config(
        key=Secret(key),
        base_url=checked_base_url(base_url if base_url is not None else DEFAULT_BASE_URL),
        timeout=float(timeout) if timeout is not None else DEFAULT_TIMEOUT,
        max_retries=max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
    )


def _next_link(page: Hidden[object]) -> str | None:
    """A FHIR search page's `next` link, when it has one."""
    value = as_dict(page.reveal())
    links = as_list(value.get("link")) if value is not None else None
    for item in links or []:
        link = as_dict(item)
        url = link.get("url") if link is not None else None
        if link is not None and link.get("relation") == "next" and isinstance(url, str):
            return url
    return None


def _matches(page: Hidden[object]) -> list[Hidden[object]]:
    """The resources a search page found, each held: every entry whose search mode is `match`, or says none."""
    value = as_dict(page.reveal())
    found: list[Hidden[object]] = []
    for item in (as_list(value.get("entry")) if value is not None else None) or []:
        entry = as_dict(item)
        resource = entry.get("resource") if entry is not None else None
        search = as_dict(entry.get("search")) if entry is not None else None
        mode = search.get("mode") if search is not None else None
        if resource is not None and (mode is None or mode == "match"):
            found.append(Hidden(resource))
    return found


def _checked_client(given: object, kind: type[httpx2.Client] | type[httpx2.AsyncClient]) -> None:
    # The type says which client; this catches one from untyped code, such as the older httpx's.
    if given is not None and not isinstance(given, kind):
        raise Haau3Error(
            f"http_client is an httpx2.{kind.__name__}, not a {type(given).__module__}.{type(given).__name__}."
        )


def _repeated(name: str) -> NoAnswerError:
    return NoAnswerError(
        f"{name} answered with a next link to a page already read, so paging stopped.", reason="unreadable", attempts=0
    )


class Haau3(SyncMethods):
    """The haau3 platform API client, for code that calls and waits: Django, Flask, scripts, notebooks.

    ```python
    from haau3 import Haau3

    client = Haau3()  # reads HAAU3_API_KEY
    plan = client.read_insurance_plan("H2406-013-000")
    ```

    Every operation of the API is a method named by its operationId. Make one client and reuse it: it
    holds a pool of connections, and is safe to share between threads. Server-side only.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        """Every option has a default.

        - `api_key`: your platform API key. Defaults to `os.environ["HAAU3_API_KEY"]`.
        - `base_url`: where the API is. Defaults to `https://api.haau3.com`.
        - `timeout`: seconds one attempt may take. Defaults to 65.
        - `max_retries`: retries after a timeout, a network failure, a 429 or a 5xx. Defaults to 1.
        - `http_client`: an `httpx2.Client` of your own, for a proxy, certificates or limits. The SDK
          never closes a client you pass.
        """
        config = _config(api_key, base_url, timeout, max_retries)
        _checked_client(http_client, httpx2.Client)
        self._engine = SyncEngine(config, http_client)
        self.with_response: SyncWithResponse = SyncWithResponse(self._engine)
        """Every method again, each returning a `Response` with the request id and timing as well as the answer."""

    @property
    def base_url(self) -> str:
        return self._engine.config.base_url

    @property
    def timeout(self) -> float:
        return self._engine.config.timeout

    @property
    def max_retries(self) -> int:
        return self._engine.config.max_retries

    def __repr__(self) -> str:
        return f"Haau3(base_url={self.base_url!r})"

    def close(self) -> None:
        """Closes the connections the client opened. A client you passed as `http_client` stays open."""
        self._engine.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def pages(
        self,
        first: Bundle,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Iterator[Bundle]:
        """Every page of a FHIR search, starting with the one you have, following each `next` link.

        A link to any host but the client's own `base_url` is refused, so the key is never sent
        elsewhere, and so is a link to a page already read.

        ```python
        first = client.search_insurance_plan(coverage_area_state="NC")
        for page in client.pages(first):
            for entry in page.get("entry", []):
                print(entry["resource"]["id"])
        ```
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        held: Hidden[object] = Hidden(first)
        del first
        for page in self._held_pages(held, options):
            yield cast("Bundle", page.reveal())

    def resources(
        self,
        first: Bundle,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Iterator[Resource]:
        """Every resource a FHIR search found, across every page, in order.

        Each entry whose search mode is `match`, or says none. A resource an `_include` or
        `_revinclude` brought, and a search's own OperationOutcome, are not among them; `pages()`
        gives every entry. Checking `resource["resourceType"]` narrows each one.

        ```python
        first = client.search_insurance_plan(coverage_area_state="VT")
        for resource in client.resources(first):
            if resource["resourceType"] == "InsurancePlan":
                print(resource.get("name"))
        ```
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        held: Hidden[object] = Hidden(first)
        del first
        for page in self._held_pages(held, options):
            for resource in _matches(page):
                yield cast("Resource", resource.reveal())

    def _held_pages(self, page: Hidden[object], options: Options) -> Iterator[Hidden[object]]:
        """Each page, held, following `next` links on the client's own host to a page not yet read."""
        followed: set[str] = set()
        while True:
            yield page
            link = _next_link(page)
            if link is None:
                return
            url = self._engine.resolve(link)
            if url in followed:
                raise _repeated(self._engine.name("GET", url))
            followed.add(url)
            page = self._engine.next_page(url, options)

    def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> RawResponse:
        """Sends any request and returns what the API answered, whatever its status.

        `path` is a path such as `/v1/scheduling/publishers`, or a whole URL on the client's own
        `base_url`; `query` uses the API's own parameter names; a str `body` is sent as it is, anything
        else as JSON. Raises only when no answer arrived (`NoAnswerError`), or for a URL on another host.
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return self._engine.request(method, path, (query or {}).items(), body, options)


class AsyncHaau3(AsyncMethods):
    """The haau3 platform API client, for code that awaits: FastAPI, Starlette, any asyncio or trio app.

    ```python
    from haau3 import AsyncHaau3

    client = AsyncHaau3()  # reads HAAU3_API_KEY
    plan = await client.read_insurance_plan("H2406-013-000")
    ```

    Every operation of the API is a method named by its operationId. Make one client per event loop and
    reuse it (in FastAPI, in the app's lifespan): it holds a pool of connections. Server-side only.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        """Every option has a default, as on `Haau3`; `http_client` is an `httpx2.AsyncClient` of your own."""
        config = _config(api_key, base_url, timeout, max_retries)
        _checked_client(http_client, httpx2.AsyncClient)
        self._engine = AsyncEngine(config, http_client)
        self.with_response: AsyncWithResponse = AsyncWithResponse(self._engine)
        """Every method again, each returning a `Response` with the request id and timing as well as the answer."""

    @property
    def base_url(self) -> str:
        return self._engine.config.base_url

    @property
    def timeout(self) -> float:
        return self._engine.config.timeout

    @property
    def max_retries(self) -> int:
        return self._engine.config.max_retries

    def __repr__(self) -> str:
        return f"AsyncHaau3(base_url={self.base_url!r})"

    async def close(self) -> None:
        """Closes the connections the client opened. A client you passed as `http_client` stays open."""
        await self._engine.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None
    ) -> None:
        await self.close()

    async def pages(
        self,
        first: Bundle,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[Bundle]:
        """Every page of a FHIR search, starting with the one you have, as `Haau3.pages` does.

        ```python
        first = await client.search_insurance_plan(coverage_area_state="NC")
        async for page in client.pages(first):
            ...
        ```
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        held: Hidden[object] = Hidden(first)
        del first
        async for page in self._held_pages(held, options):
            yield cast("Bundle", page.reveal())

    async def resources(
        self,
        first: Bundle,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[Resource]:
        """Every resource a FHIR search found, across every page, as `Haau3.resources` does.

        ```python
        first = await client.search_insurance_plan(coverage_area_state="VT")
        async for resource in client.resources(first):
            ...
        ```
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        held: Hidden[object] = Hidden(first)
        del first
        async for page in self._held_pages(held, options):
            for resource in _matches(page):
                yield cast("Resource", resource.reveal())

    async def _held_pages(self, page: Hidden[object], options: Options) -> AsyncIterator[Hidden[object]]:
        """Each page, held, as `Haau3._held_pages` gives them."""
        followed: set[str] = set()
        while True:
            yield page
            link = _next_link(page)
            if link is None:
                return
            url = self._engine.resolve(link)
            if url in followed:
                raise _repeated(self._engine.name("GET", url))
            followed.add(url)
            page = await self._engine.next_page(url, options)

    async def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> RawResponse:
        """Sends any request and returns what the API answered, whatever its status, as `Haau3.request` does."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return await self._engine.request(method, path, (query or {}).items(), body, options)


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MAX_RETRIES", "DEFAULT_TIMEOUT", "AsyncHaau3", "Haau3"]
