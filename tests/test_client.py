"""Making a client, where it sends, and pages."""

from __future__ import annotations

import sys
from typing import Any

import httpx2
import pytest

import haau3
from haau3 import AsyncHaau3, Haau3, Haau3Error, NoAnswerError, RefusedError

from .helpers import KEY, Client, Recorder, answer, sync_client

PLAN = {"resourceType": "InsurancePlan", "id": "H1"}


def page(n: int, next_url: str | None) -> dict[str, object]:
    links = [{"relation": "self", "url": f"https://api.haau3.com/v1/fhir/r4/InsurancePlan?page={n}"}]
    if next_url:
        links.append({"relation": "next", "url": next_url})
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": links,
        "entry": [{"resource": {"resourceType": "InsurancePlan", "id": f"P{n}"}}],
    }


def test_the_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAAU3_API_KEY", KEY)
    recorder = Recorder(answer(200, PLAN))
    Haau3(http_client=httpx2.Client(transport=httpx2.MockTransport(recorder))).read_insurance_plan("H1")
    assert recorder.last.headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_no_key_is_refused(monkeypatch: pytest.MonkeyPatch, cls: type) -> None:
    with pytest.raises(Haau3Error, match=r"^No API key: pass api_key, or set HAAU3_API_KEY\.$"):
        cls()
    monkeypatch.setenv("HAAU3_API_KEY", "")
    with pytest.raises(Haau3Error):
        cls()
    monkeypatch.setenv("HAAU3_API_KEY", KEY)
    with pytest.raises(Haau3Error):
        cls(api_key="")  # an empty key passed is not replaced by the environment's


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_a_browser_is_refused(monkeypatch: pytest.MonkeyPatch, cls: type) -> None:
    monkeypatch.setattr(sys, "platform", "emscripten")
    with pytest.raises(Haau3Error, match="runs on a server only"):
        cls(api_key=KEY)
    monkeypatch.setattr(sys, "platform", "linux")
    cls(api_key=KEY)  # the control: anywhere else it starts


@pytest.mark.parametrize("bad", ["api.haau3.com", "ftp://api.haau3.com", "https://", "https://api.haau3.com:notaport"])
def test_a_bad_base_url_is_refused_when_the_client_is_made(bad: str) -> None:
    with pytest.raises(Haau3Error, match="base_url must be an http or https URL"):
        Haau3(api_key=KEY, base_url=bad)


def test_a_client_of_the_wrong_kind_is_refused() -> None:
    with pytest.raises(Haau3Error, match=r"^http_client is an httpx2\.Client, not a httpx2\.AsyncClient\.$"):
        Haau3(api_key=KEY, http_client=httpx2.AsyncClient())  # type: ignore[arg-type]
    with pytest.raises(Haau3Error, match=r"http_client is an httpx2\.AsyncClient"):
        AsyncHaau3(api_key=KEY, http_client=httpx2.Client())  # type: ignore[arg-type]


def test_the_version_matches_the_package_and_the_release_manifest() -> None:
    import json
    import re

    from .helpers import ROOT

    # pyproject.toml reads the version from _version.py, the one place release-please writes it.
    assert 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text()
    version_file = (ROOT / "src" / "haau3" / "_version.py").read_text()
    match = re.fullmatch(r'__version__ = "([^"]+)"  # x-release-please-version\n', version_file)
    assert match is not None
    assert haau3.__version__ == match.group(1)
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    assert manifest["."] == haau3.__version__


def test_the_user_agent_names_the_language_and_the_runtime() -> None:
    recorder = Recorder(answer(200, PLAN))
    sync_client(recorder).read_insurance_plan("H1")
    major, minor = sys.version_info[:2]
    assert (
        recorder.last.headers["user-agent"]
        == f"haau3-python/{haau3.__version__} (python {major}.{minor}; httpx2 {httpx2.__version__})"
    )


def test_the_key_is_never_shown() -> None:
    client = Haau3(api_key=KEY)
    for shown in (repr(client), str(client), repr(vars(client)), repr(client.with_response), repr(client._engine)):  # pyright: ignore[reportPrivateUsage]
        assert KEY not in shown
    assert repr(client) == "Haau3(base_url='https://api.haau3.com')"


@pytest.mark.anyio
async def test_the_base_url_is_where_requests_go(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    await Client(kind, recorder, base_url="https://proxy.example.test/haau3/")("read_insurance_plan", "H1")
    assert str(recorder.last.url) == "https://proxy.example.test/haau3/v1/fhir/r4/InsurancePlan/H1"
    assert recorder.last.headers["accept"] == "application/fhir+json"


@pytest.mark.anyio
async def test_request_refuses_another_host_and_sends_nothing(kind: str) -> None:
    recorder = Recorder(answer(200, {}))
    error = await Client(kind, recorder).raises("request", "GET", "https://example.com/steal")
    assert type(error) is Haau3Error
    assert (
        error.args[0]
        == "Refusing to send the API key to https://example.com: the client's base_url is https://api.haau3.com."
    )
    assert recorder.requests == []


@pytest.mark.anyio
async def test_haau3_base_url_is_where_requests_go_when_no_base_url_is_passed(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAAU3_BASE_URL", "https://stand-in.example.test/")
    recorder = Recorder(answer(200, PLAN))
    client = Client(kind, recorder)
    assert client.inner.base_url == "https://stand-in.example.test"
    await client("read_insurance_plan", "H1")
    assert str(recorder.last.url) == "https://stand-in.example.test/v1/fhir/r4/InsurancePlan/H1"


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_the_base_url_option_beats_haau3_base_url_and_an_empty_one_is_unset(
    monkeypatch: pytest.MonkeyPatch, cls: type
) -> None:
    monkeypatch.setenv("HAAU3_BASE_URL", "https://stand-in.example.test")
    assert cls(api_key=KEY, base_url="https://other.example.test").base_url == "https://other.example.test"
    monkeypatch.setenv("HAAU3_BASE_URL", "")
    assert cls(api_key=KEY).base_url == "https://api.haau3.com"
    monkeypatch.setenv("HAAU3_BASE_URL", "ftp://stand-in.example.test")
    with pytest.raises(Haau3Error, match="base_url must be an http or https URL"):
        cls(api_key=KEY)


@pytest.mark.anyio
async def test_the_key_goes_to_haau3_base_urls_host_only(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAAU3_BASE_URL", "https://stand-in.example.test")
    recorder = Recorder(answer(200, {}))
    error = await Client(kind, recorder).raises("request", "GET", "https://api.haau3.com/v1/scheduling/publishers")
    assert type(error) is Haau3Error
    assert error.args[0].startswith("Refusing to send the API key to https://api.haau3.com:")
    assert recorder.requests == []


@pytest.mark.anyio
async def test_pages_follows_next_links(kind: str) -> None:
    second = "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"
    third = "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=3"
    recorder = Recorder(answer(200, page(2, third)), answer(200, page(3, None)))
    pages = await Client(kind, recorder).pages(page(1, second))
    assert [p["entry"][0]["resource"]["id"] for p in pages] == ["P1", "P2", "P3"]
    assert [str(r.url) for r in recorder.requests] == [second, third]


@pytest.mark.anyio
async def test_pages_refuses_a_next_link_to_another_host(kind: str) -> None:
    recorder = Recorder(answer(200, page(2, None)))
    client = Client(kind, recorder)
    with pytest.raises(Haau3Error, match=r"Refusing to send the API key to https://example\.com"):
        await client.pages(page(1, "https://example.com/v1/fhir/r4/InsurancePlan?page=2"))
    assert recorder.requests == []


@pytest.mark.anyio
async def test_pages_stops_at_a_next_link_it_already_followed(kind: str) -> None:
    second = "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"
    recorder = Recorder(answer(200, page(2, second)))
    client = Client(kind, recorder)
    with pytest.raises(NoAnswerError) as raised:
        await client.pages(page(1, second))
    assert raised.value.reason == "unreadable"
    assert (
        raised.value.message
        == "GET /v1/fhir/r4/InsurancePlan answered with a next link to a page already read, so paging stopped."
    )
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_each_page_is_checked_as_a_bundle(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    with pytest.raises(NoAnswerError, match="answered an InsurancePlan where a Bundle was expected"):
        await Client(kind, recorder).pages(page(1, "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"))


def _entry(resource_type: str, id_: str, mode: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"resource": {"resourceType": resource_type, "id": id_}}
    if mode is not None:
        entry["search"] = {"mode": mode}
    return entry


@pytest.mark.anyio
async def test_resources_are_the_matches_of_every_page(kind: str) -> None:
    second_page = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            _entry("InsurancePlan", "C", "match"),
            _entry("Organization", "included", "include"),
            _entry("OperationOutcome", "outcome", "outcome"),
            _entry("InsurancePlan", "D"),
            {"search": {"mode": "match"}},
        ],
    }
    first = {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": [{"relation": "next", "url": "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"}],
        "entry": [_entry("InsurancePlan", "A", "match"), _entry("InsurancePlan", "B")],
    }
    recorder = Recorder(answer(200, second_page))
    found = await Client(kind, recorder).resources(first)
    assert [f"{r['resourceType']}/{r['id']}" for r in found] == [
        "InsurancePlan/A",
        "InsurancePlan/B",
        "InsurancePlan/C",
        "InsurancePlan/D",
    ]
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_resources_end_with_the_error_a_page_ended_with(kind: str) -> None:
    first = {
        "resourceType": "Bundle",
        "link": [{"relation": "next", "url": "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"}],
        "entry": [_entry("InsurancePlan", "A")],
    }
    recorder = Recorder(answer(404, {"resourceType": "OperationOutcome", "issue": []}))
    with pytest.raises(RefusedError):
        await Client(kind, recorder).resources(first)


def test_one_client_serves_many_threads() -> None:
    from concurrent.futures import ThreadPoolExecutor

    client = sync_client(
        lambda request: answer(200, {"resourceType": "InsurancePlan", "id": request.url.path.rsplit("/", 1)[-1]})
    )
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda i: client.read_insurance_plan(f"P{i}")["id"], range(64)))
    assert ids == [f"P{i}" for i in range(64)]


def test_what_the_package_exports() -> None:
    assert set(haau3.__all__) <= set(dir(haau3))
    assert (
        haau3.DEFAULT_TIMEOUT == 65.0
        and haau3.DEFAULT_MAX_RETRIES == 1
        and haau3.DEFAULT_BASE_URL == "https://api.haau3.com"
    )
