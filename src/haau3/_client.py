"""The two clients: `Haau3` for code that calls and waits, `AsyncHaau3` for code that awaits."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx2

from ._app_info import AppInfo, user_agent
from ._body import Hidden, Secret
from ._encode import QueryValue, checked_base_url, origin
from ._env_file import unloaded_env_file
from ._errors import Haau3Error, NoAnswerError
from ._json import as_dict, as_list
from ._methods import AsyncMethods, AsyncWithResponse, SyncMethods, SyncWithResponse
from ._parameters import DosageReview
from ._parameters import parameter as _parameter
from ._parameters import parameters as _parameters
from ._parameters import review_flags as _review_flags
from ._response import Options, RawResponse, Response
from ._terminology import Designation, DesignationUse, LookupResult, ValidationResult
from ._terminology import designations as _designations
from ._terminology import lookup_result as _lookup_result
from ._terminology import validation_result as _validation_result
from ._transport import AsyncEngine, Config, SyncEngine

if TYPE_CHECKING:
    from typing_extensions import Self

    from .fhir import Bundle, Dosage, Parameters, ParametersParameter, Resource

Environment = Literal["production", "sandbox"]
"""The API's two environments: `production` at https://api.haau3.com, and `sandbox` at
https://sandbox.haau3.com, which serves production's paths with fixed answers and synthetic data, to
build and test against."""

ENVIRONMENTS: dict[Environment, str] = {
    "production": "https://api.haau3.com",
    "sandbox": "https://sandbox.haau3.com",
}
"""Where each environment is."""

DEFAULT_BASE_URL = ENVIRONMENTS["production"]
DEFAULT_TIMEOUT = 65.0
"""Seconds one attempt may take. The API gives a batch 60 seconds, so this is a little longer."""
DEFAULT_MAX_RETRIES = 1

NO_KEY = (
    "No API key. Set HAAU3_API_KEY, or pass it as api_key. Create a free key at "
    "https://platform.haau3.com, or run npx haau3 login, then npx haau3 keys create, which writes one to "
    "this project's env file (see https://docs.haau3.com/authentication). To call the Sandbox with no "
    'key, set HAAU3_ENVIRONMENT=sandbox, or pass environment="sandbox".'
)
"""What a client with no key says: both ways to give one, where one comes from (the dashboard, or the
command line, which writes it to the project's env file), and the Sandbox's way to call with none."""


def _unloaded(file: str) -> str:
    """What a client with no key says when an env file in the current folder sets one: the program never
    loaded that file. The file is named; its contents never are."""
    return (
        f"No API key. HAAU3_API_KEY is not set, but {file} in this folder sets it, and this program has not "
        f'loaded that file. Load it first: call load_dotenv("{file}") from python-dotenv before making the '
        f"client, or run set -a; . ./{file}; set +a in the shell before the program."
    )


def _no_key() -> Haau3Error:
    """The refusal for no key, naming an env file the program did not load when there is one."""
    file = unloaded_env_file()
    return Haau3Error(NO_KEY if file is None else _unloaded(file), code="no-api-key")


KeyMode = Literal["live", "test", "older"]


def _key_mode(key: str) -> KeyMode:
    """A key's mode, from its prefix alone: the API checks the rest."""
    if key.startswith("haau3_sk_test_"):
        return "test"
    if key.startswith("haau3_sk_live_"):
        return "live"
    return "older"


def _checked_environment(value: object, source: str) -> Environment:
    """An environment, refused unless it is one of the two. The variable is read trimmed and in any
    case, as a shell or an env file may write it; the keyword is typed and taken as it is. The value is
    never repeated, since a variable set by mistake can hold anything."""
    read = value.strip().lower() if source == "HAAU3_ENVIRONMENT" and isinstance(value, str) else value
    if read == "production":
        return "production"
    if read == "sandbox":
        return "sandbox"
    raise Haau3Error(f'{source} takes "production" or "sandbox", and this is neither.', code="invalid-environment")


def _held_key(api_key: object) -> tuple[Secret | None, str]:
    """The key, trimmed and held where nothing prints it, from the keyword or else HAAU3_API_KEY, with
    where it came from; None when there is none.

    A value that is not a string, or that carries anything before its `haau3_sk_` prefix (quotes an env
    file kept, or "Bearer "), is refused, naming only where it came from. Every plain copy is gone
    before anything is raised, so a traceback that shows each frame's local variables shows none.
    """
    source = "api_key" if api_key is not None else "HAAU3_API_KEY"
    raw = api_key if api_key is not None else os.environ.get("HAAU3_API_KEY")
    del api_key
    if raw is not None and not isinstance(raw, str):
        del raw
        raise Haau3Error(f"{source} takes a string.", code="invalid-api-key")
    text = (raw or "").strip()
    del raw
    before_prefix = text.find("haau3_sk_") > 0
    held = Secret(text) if text else None
    del text
    if before_prefix:
        raise Haau3Error(
            f'The key in {source} has something before its haau3_sk_ prefix, such as quotes or "Bearer ". '
            "Set the key alone.",
            code="api-key-not-alone",
        )
    return held, source


def _environment_at(base_url: str) -> Environment | None:
    """The environment whose own address a base URL is, by its origin, or None for any other host."""
    there = origin(base_url)
    for name, address in ENVIRONMENTS.items():
        if origin(address) == there:
            return name
    return None


_ADDRESS_OF: dict[Environment, str] = {
    "production": "production's address, https://api.haau3.com",
    "sandbox": "the Sandbox's address, https://sandbox.haau3.com",
}
_NAMED: dict[Environment, str] = {"production": "production", "sandbox": "the Sandbox"}


def _mismatch(mode: KeyMode, key_from: str, environment: Environment, named_in: str) -> Haau3Error | None:
    """Why a key and a named environment cannot go together, or None when they can. The key is named
    by where it came from and its kind, never by its value, and the way to call with no key is the one
    its source needs."""
    with_no_key = (
        "unset HAAU3_API_KEY to call it with no key"
        if key_from == "HAAU3_API_KEY"
        else "pass no key to call it with none"
    )
    if environment == "production" and mode == "test":
        return Haau3Error(
            f"The key in {key_from} is a test key (haau3_sk_test_…), which works only in the Sandbox, and "
            f"{named_in} names production. Use a live key there, or name the Sandbox.",
            code="test-key-outside-sandbox",
        )
    if environment == "sandbox" and mode == "live":
        return Haau3Error(
            f"The key in {key_from} is a live key (haau3_sk_live_…), which does not work in the Sandbox, and "
            f"{named_in} names the Sandbox. Use a test key there, or {with_no_key}.",
            code="live-key-in-sandbox",
        )
    if environment == "sandbox" and mode == "older":
        return Haau3Error(
            f"The key in {key_from} was made before test and live keys, so it does not work in the Sandbox, and "
            f"{named_in} names the Sandbox. Create a test key at https://platform.haau3.com, or {with_no_key}.",
            code="older-key-in-sandbox",
        )
    return None


def _base_url(given: str | None, environment: Environment) -> tuple[str, str]:
    """The option's base URL, else HAAU3_BASE_URL's, else the environment's, each checked by its own
    name, with where it came from."""
    if given is not None:
        return checked_base_url(given, "base_url"), "base_url"
    from_env = os.environ.get("HAAU3_BASE_URL") or None
    if from_env is not None:
        return checked_base_url(from_env, "HAAU3_BASE_URL"), "HAAU3_BASE_URL"
    return ENVIRONMENTS[environment], "base_url"


def _config(
    key: Secret | None,
    key_from: str,
    environment: Environment | None,
    base_url: str | None,
    timeout: float | None,
    max_retries: int | None,
    app_info: AppInfo | None,
) -> Config:
    """The client's settings. `key` is already held, so no frame here holds a plain copy of it."""
    # The variable is read only when no keyword names the environment, so a stray one never stops a
    # client that says what it calls.
    source = "environment" if environment is not None else "HAAU3_ENVIRONMENT"
    from_env = os.environ.get("HAAU3_ENVIRONMENT") or None if environment is None else None
    named = (
        _checked_environment(environment, "environment")
        if environment is not None
        else (_checked_environment(from_env, "HAAU3_ENVIRONMENT") if from_env is not None else None)
    )
    if key is None and named != "sandbox":
        raise _no_key()
    mode = _key_mode(key.reveal()) if key is not None else None
    refused = _mismatch(mode, key_from, named, source) if mode is not None and named is not None else None
    if refused is not None:
        raise refused
    chosen: Environment = named if named is not None else ("sandbox" if mode == "test" else "production")
    where, base_from = _base_url(base_url, chosen)
    # A base URL that is one environment's own address is that environment. A named environment must
    # agree with it, and the Sandbox's address takes test keys only. Production's still takes a test
    # key, at the Sandbox's first address, until that address goes.
    at = _environment_at(where)
    if at is not None and named is not None and named != at:
        raise Haau3Error(
            f"{source} names {_NAMED[named]}, and {base_from} is {_ADDRESS_OF[at]}. "
            "Name one environment, or a base URL that is neither's.",
            code="environment-conflicts-with-base-url",
        )
    if at == "sandbox" and mode is not None and mode != "test":
        kind = "is a live key (haau3_sk_live_…)" if mode == "live" else "was made before test and live keys"
        raise Haau3Error(
            f"The key in {key_from} {kind}, and {base_from} is {_ADDRESS_OF['sandbox']}, which takes test keys "
            "only. Use a test key there.",
            code="live-key-in-sandbox" if mode == "live" else "older-key-in-sandbox",
        )
    return Config(
        key=key,
        base_url=where,
        timeout=float(timeout) if timeout is not None else DEFAULT_TIMEOUT,
        max_retries=max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
        user_agent=user_agent(app_info),
        environment=at if at is not None else chosen,
        # The Sandbox's own host, which does not serve its first address: its address, or a stand-in
        # named with the Sandbox as the environment.
        on_the_sandbox_host=at == "sandbox" or (at is None and named == "sandbox"),
    )


def _server_only() -> None:
    """Refuses a browser (Pyodide), where a key would be public."""
    if sys.platform == "emscripten":
        raise Haau3Error(
            "haau3 runs on a server only: an API key in a browser is public. Call the API from your server.",
            code="in-browser",
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
            f"http_client is an httpx2.{kind.__name__}, not a {type(given).__module__}.{type(given).__name__}.",
            code="invalid-http-client",
        )


def _repeated(name: str) -> NoAnswerError:
    return NoAnswerError(
        f"{name} answered with a next link to a page already read, so paging stopped.", reason="unreadable", attempts=0
    )


class _SyncWithResponse(SyncWithResponse):
    """`Haau3.with_response`: every method again, `get` and `post` too, each returning a `Response`."""

    __slots__ = ()

    def get(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response[Any]:
        """`Haau3.get`, returning a `Response` with the request id and timing as well as the answer."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return self._engine.call_path("GET", path, path_params, (query or {}).items(), None, expects, options)

    def post(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response[Any]:
        """`Haau3.post`, returning a `Response` with the request id and timing as well as the answer."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return self._engine.call_path("POST", path, path_params, (query or {}).items(), body, expects, options)


class _AsyncWithResponse(AsyncWithResponse):
    """`AsyncHaau3.with_response`: every method again, `get` and `post` too, each returning a `Response`."""

    __slots__ = ()

    async def get(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response[Any]:
        """`AsyncHaau3.get`, returning a `Response` with the request id and timing as well as the answer."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return await self._engine.call_path("GET", path, path_params, (query or {}).items(), None, expects, options)

    async def post(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Response[Any]:
        """`AsyncHaau3.post`, returning a `Response` with the request id and timing as well as the answer."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return await self._engine.call_path("POST", path, path_params, (query or {}).items(), body, expects, options)


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
        environment: Environment | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx2.Client | None = None,
        app_info: AppInfo | None = None,
    ) -> None:
        """Every option has a default.

        - `api_key`: your platform API key. Defaults to `os.environ["HAAU3_API_KEY"]`. Its mode picks
          the environment: a test key (`haau3_sk_test_…`) sends every call to the Sandbox, and a live
          key (`haau3_sk_live_…`), or one made before test and live keys, to production.
        - `environment`: `"production"` or `"sandbox"`. Defaults to `os.environ["HAAU3_ENVIRONMENT"]`,
          then the key's mode. Name `"sandbox"` to call the Sandbox with no key. A key of the other
          mode is refused here, before anything is sent.
        - `base_url`: where the API is, for a stand-in or a proxy. Defaults to
          `os.environ["HAAU3_BASE_URL"]`, then the environment's address: `https://api.haau3.com` or
          `https://sandbox.haau3.com`. The API key is sent there only, and the environment's rules for
          the key still hold.
        - `timeout`: seconds one attempt may take. Defaults to 65.
        - `max_retries`: retries after a timeout, a network failure, a 429 or a 5xx. Defaults to 1.
        - `http_client`: an `httpx2.Client` of your own, for a proxy, certificates or limits. The SDK
          never closes a client you pass.
        - `app_info`: your app's name, version and URL, sent in the User-Agent after the SDK's own:
          `{"name": "my-plugin", "version": "1.2.0"}`. A value the header cannot carry is refused here,
          before anything is sent. A `user-agent` in a call's `extra_headers` replaces the whole value.
        """
        _server_only()
        key, key_from = _held_key(api_key)
        del api_key  # held from here on, so no refusal below has a plain copy in this frame
        config = _config(key, key_from, environment, base_url, timeout, max_retries, app_info)
        _checked_client(http_client, httpx2.Client)
        self._engine = SyncEngine(config, http_client)
        self.with_response: _SyncWithResponse = _SyncWithResponse(self._engine)
        """Every method again, each returning a `Response` with the request id and timing as well as the answer."""

    @property
    def environment(self) -> Environment:
        """The environment the client calls: the one named, else the key's mode."""
        return self._engine.config.environment

    @property
    def base_url(self) -> str:
        """Where requests go."""
        return self._engine.config.base_url

    @property
    def timeout(self) -> float:
        return self._engine.config.timeout

    @property
    def max_retries(self) -> int:
        return self._engine.config.max_retries

    def __repr__(self) -> str:
        # A client its constructor refused has no engine, and a traceback that shows each frame's
        # local variables prints it all the same (Python 3.10 does not catch a repr that raises).
        if "_engine" not in vars(self):
            return "Haau3(not made)"
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

    def parameters(self, answer: Parameters, name: str) -> list[ParametersParameter]:
        """Every part of a Parameters answer with that name, in order, with no request.

        FHIR operations answer this way: `$lookup`'s `property` parts, `$structure-dosage`'s `dosage`
        part. Each part holds its value in the `value[x]` its operation names, so read `valueDosage`,
        `valueString` and so on from it. Empty when there is none. The plain function
        `haau3.parameters()` does the same with no client.

        ```python
        answer = client.code_system_lookup(system="http://loinc.org", code="2345-7", property=["COMPONENT"])
        for part in client.parameters(answer, "property"):
            print(part.get("part"))
        ```
        """
        return _parameters(answer, name)

    def parameter(self, answer: Parameters, name: str) -> ParametersParameter | None:
        """The one part of a Parameters answer with that name, with no request, or None when there is none.

        Raises a `Haau3Error` when there are two, since which one is meant is then ambiguous;
        `parameters()` reads a name that repeats. The plain function `haau3.parameter()` does the same
        with no client.

        ```python
        answer = client.code_system_lookup(system="http://loinc.org", code="2345-7")
        part = client.parameter(answer, "display")
        print(part.get("valueString") if part else None)
        ```
        """
        return _parameter(answer, name)

    def review_flags(self, dosage: Dosage) -> DosageReview:
        """The review flags on a Dosage `$structure-dosage` answered with, and the versions it names.

        With no request. Each flag is something a person should check: its code, its display, and the
        words of the directions it is about, when it is about words. The versions say how the
        directions were read and coded, and which RxNorm release the medication was looked up in. The
        plain function `haau3.review_flags()` does the same with no client, so a stored answer can be
        read where no key is.

        ```python
        answer = client.structure_dosage(structure_dosage_request("Take 1 tablet by mouth twice daily"))
        part = client.parameter(answer, "dosage")
        if part and "valueDosage" in part:
            for flag in client.review_flags(part["valueDosage"]).flags:
                print(flag.code, flag.words)
        ```
        """
        return _review_flags(dosage)

    def lookup_result(self, answer: Parameters) -> LookupResult:
        """A `CodeSystem/$lookup` answer, typed, with no request.

        The code, its system and version, its display, whether it is abstract, every designation and
        every property. A part this reader does not know stays in `other`. Raises a `Haau3Error` when a
        part FHIR allows once comes twice. The plain function `haau3.lookup_result()` does the same with
        no client.

        ```python
        lookup = client.lookup_result(client.code_system_lookup(system="http://hl7.org/fhir/sid/icd-10-cm", code="E08.37X1"))
        print(lookup.display, lookup.abstract)
        ```
        """
        return _lookup_result(answer)

    def validation_result(self, answer: Parameters) -> ValidationResult:
        """A `$validate-code` answer, from a code system or a value set, typed, with no request.

        Whether the code is valid, its display, and the server's message. Raises a `Haau3Error` when a
        part FHIR allows once comes twice. The plain function `haau3.validation_result()` does the same
        with no client.

        ```python
        answer = client.code_system_validate_code(url="http://loinc.org", code="8867-4", display="Pulse")
        check = client.validation_result(answer)
        ```
        """
        return _validation_result(answer)

    def designations(
        self, lookup: LookupResult, *, use: DesignationUse | None = None, language: str | None = None
    ) -> list[Designation]:
        """The designations of a lookup with a use and in a language, in answer order, with no request.

        A use matches by its system and code together; a language as RFC 4647's basic filtering says, so
        `en` finds `en-US`. A field left out matches every designation. The plain function
        `haau3.designations()` does the same with no client.

        ```python
        lookup = client.lookup_result(client.code_system_lookup(system="http://hl7.org/fhir/sid/icd-10-cm", code="E08.37X1"))
        short_title = client.designations(lookup, use=haau3.SYNONYM_USE)
        ```
        """
        return _designations(lookup, use=use, language=language)

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

    def get(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Sends a GET to a path on the API and returns its answer, or raises the errors every
        operation's own method raises: `AuthError`, `ForbiddenError`, `RefusedError` or `NoAnswerError`.

        For an operation this version of the SDK has no method for, or one you call by its path.
        `path` is the path as the spec writes it, such as `/v1/fhir/r4/Practitioner/{id}`, and
        `path_params` a value for each `{name}` in it, each encoded into the path; a message names the
        path as written, never a value. A `{name}` with no value, or a value with no `{name}`, is refused
        before anything is sent. `expects` lists the FHIR resource types the answer may be, as each
        operation's own method checks them; any other is a `NoAnswerError` with reason `unreadable`.
        `with_response.get()` returns a `Response` with the request id and timing as well.
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return self._engine.call_path("GET", path, path_params, (query or {}).items(), None, expects, options).data

    def post(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Sends a POST to a path on the API, with a body, and returns its answer or raises, as `get()` does.

        A str `body` is sent as it is, anything else as JSON.
        """
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return self._engine.call_path("POST", path, path_params, (query or {}).items(), body, expects, options).data

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
        environment: Environment | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        http_client: httpx2.AsyncClient | None = None,
        app_info: AppInfo | None = None,
    ) -> None:
        """Every option has a default, as on `Haau3`; `http_client` is an `httpx2.AsyncClient` of your own."""
        _server_only()
        key, key_from = _held_key(api_key)
        del api_key  # held from here on, so no refusal below has a plain copy in this frame
        config = _config(key, key_from, environment, base_url, timeout, max_retries, app_info)
        _checked_client(http_client, httpx2.AsyncClient)
        self._engine = AsyncEngine(config, http_client)
        self.with_response: _AsyncWithResponse = _AsyncWithResponse(self._engine)
        """Every method again, each returning a `Response` with the request id and timing as well as the answer."""

    @property
    def environment(self) -> Environment:
        """The environment the client calls: the one named, else the key's mode."""
        return self._engine.config.environment

    @property
    def base_url(self) -> str:
        """Where requests go."""
        return self._engine.config.base_url

    @property
    def timeout(self) -> float:
        return self._engine.config.timeout

    @property
    def max_retries(self) -> int:
        return self._engine.config.max_retries

    def __repr__(self) -> str:
        # As Haau3's: a client its constructor refused has no engine.
        if "_engine" not in vars(self):
            return "AsyncHaau3(not made)"
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

    def parameters(self, answer: Parameters, name: str) -> list[ParametersParameter]:
        """Every part of a Parameters answer with that name, in order, with no request.

        FHIR operations answer this way: `$lookup`'s `property` parts, `$structure-dosage`'s `dosage`
        part. Each part holds its value in the `value[x]` its operation names, so read `valueDosage`,
        `valueString` and so on from it. Empty when there is none. The plain function
        `haau3.parameters()` does the same with no client.

        ```python
        answer = await client.code_system_lookup(system="http://loinc.org", code="2345-7", property=["COMPONENT"])
        for part in client.parameters(answer, "property"):
            print(part.get("part"))
        ```
        """
        return _parameters(answer, name)

    def parameter(self, answer: Parameters, name: str) -> ParametersParameter | None:
        """The one part of a Parameters answer with that name, with no request, or None when there is none.

        Raises a `Haau3Error` when there are two, since which one is meant is then ambiguous;
        `parameters()` reads a name that repeats. The plain function `haau3.parameter()` does the same
        with no client.

        ```python
        answer = await client.code_system_lookup(system="http://loinc.org", code="2345-7")
        part = client.parameter(answer, "display")
        print(part.get("valueString") if part else None)
        ```
        """
        return _parameter(answer, name)

    def review_flags(self, dosage: Dosage) -> DosageReview:
        """The review flags on a Dosage `$structure-dosage` answered with, and the versions it names.

        With no request. Each flag is something a person should check: its code, its display, and the
        words of the directions it is about, when it is about words. The versions say how the
        directions were read and coded, and which RxNorm release the medication was looked up in. The
        plain function `haau3.review_flags()` does the same with no client, so a stored answer can be
        read where no key is.

        ```python
        answer = await client.structure_dosage(structure_dosage_request("Take 1 tablet by mouth twice daily"))
        part = client.parameter(answer, "dosage")
        if part and "valueDosage" in part:
            for flag in client.review_flags(part["valueDosage"]).flags:
                print(flag.code, flag.words)
        ```
        """
        return _review_flags(dosage)

    def lookup_result(self, answer: Parameters) -> LookupResult:
        """A `CodeSystem/$lookup` answer, typed, with no request.

        The code, its system and version, its display, whether it is abstract, every designation and
        every property. A part this reader does not know stays in `other`. Raises a `Haau3Error` when a
        part FHIR allows once comes twice. The plain function `haau3.lookup_result()` does the same with
        no client.

        ```python
        lookup = client.lookup_result(await client.code_system_lookup(system="http://hl7.org/fhir/sid/icd-10-cm", code="E08.37X1"))
        print(lookup.display, lookup.abstract)
        ```
        """
        return _lookup_result(answer)

    def validation_result(self, answer: Parameters) -> ValidationResult:
        """A `$validate-code` answer, from a code system or a value set, typed, with no request.

        Whether the code is valid, its display, and the server's message. Raises a `Haau3Error` when a
        part FHIR allows once comes twice. The plain function `haau3.validation_result()` does the same
        with no client.

        ```python
        answer = await client.code_system_validate_code(url="http://loinc.org", code="8867-4", display="Pulse")
        check = client.validation_result(answer)
        ```
        """
        return _validation_result(answer)

    def designations(
        self, lookup: LookupResult, *, use: DesignationUse | None = None, language: str | None = None
    ) -> list[Designation]:
        """The designations of a lookup with a use and in a language, in answer order, with no request.

        A use matches by its system and code together; a language as RFC 4647's basic filtering says, so
        `en` finds `en-US`. A field left out matches every designation. The plain function
        `haau3.designations()` does the same with no client.

        ```python
        lookup = client.lookup_result(await client.code_system_lookup(system="http://hl7.org/fhir/sid/icd-10-cm", code="E08.37X1"))
        short_title = client.designations(lookup, use=haau3.SYNONYM_USE)
        ```
        """
        return _designations(lookup, use=use, language=language)

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

    async def get(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Sends a GET to a path on the API and returns its answer, or raises, as `Haau3.get` does."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return (
            await self._engine.call_path("GET", path, path_params, (query or {}).items(), None, expects, options)
        ).data

    async def post(
        self,
        path: str,
        *,
        path_params: Mapping[str, str] | None = None,
        query: Mapping[str, QueryValue] | None = None,
        body: object = None,
        expects: Sequence[str] = (),
        timeout: float | None = None,
        max_retries: int | None = None,
        correlation_id: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Sends a POST to a path on the API, with a body, and returns its answer or raises, as `Haau3.post` does."""
        options = Options(timeout, max_retries, correlation_id, extra_headers)
        return (
            await self._engine.call_path("POST", path, path_params, (query or {}).items(), body, expects, options)
        ).data

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
