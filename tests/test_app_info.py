"""app_info: the caller's app in the User-Agent, after the SDK's own, refused when the header cannot carry it.

A call's own user-agent in extra_headers still replaces the whole value.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx2
import pytest

import haau3
from haau3 import AppInfo, AsyncHaau3, Haau3, Haau3Error, calls

from .helpers import KEY, Client, Recorder, answer

PLAN = {"resourceType": "InsurancePlan", "id": "H1"}
BATCH = {
    "resourceType": "Bundle",
    "type": "batch-response",
    "entry": [{"resource": PLAN, "response": {"status": "200 OK"}}],
}
SDK = (
    f"haau3-python/{haau3.__version__} (python {sys.version_info[0]}.{sys.version_info[1]};"
    f" httpx2 {httpx2.__version__})"
)
"""The SDK's own part, as it is sent with no app_info."""


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("app_info", "sent"),
    [
        ({"name": "my-plugin"}, f"{SDK} my-plugin"),
        ({"name": "my-plugin", "version": "1.2.0"}, f"{SDK} my-plugin/1.2.0"),
        (
            {"name": "my-plugin", "version": "1.2.0", "url": "https://example.com/my-plugin"},
            f"{SDK} my-plugin/1.2.0 (https://example.com/my-plugin)",
        ),
        ({"name": "my-plugin", "version": "", "url": None}, f"{SDK} my-plugin"),
        (None, SDK),
    ],
    ids=["name", "version", "url", "empty or None sends none", "none given (control)"],
)
async def test_the_app_is_added_after_the_sdks_own_part(kind: str, app_info: AppInfo | None, sent: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    await Client(kind, recorder, app_info=app_info)("read_insurance_plan", "H1")
    assert recorder.last.headers["user-agent"] == sent


@pytest.mark.anyio
async def test_it_is_sent_on_every_attempt_of_a_retried_call(kind: str) -> None:
    recorder = Recorder(answer(503, {}, headers={"retry-after": "0"}), answer(200, PLAN))
    client = Client(kind, recorder, max_retries=1, app_info={"name": "my-plugin", "version": "1.2.0"})
    await client("read_insurance_plan", "H1")
    assert [r.headers["user-agent"] for r in recorder.requests] == [f"{SDK} my-plugin/1.2.0"] * 2


@pytest.mark.anyio
async def test_it_is_sent_by_request_and_batch_too(kind: str) -> None:
    recorder = Recorder(answer(200, {}, fhir=False), answer(200, BATCH))
    client = Client(kind, recorder, app_info={"name": "my-plugin", "version": "1.2.0"})
    await client("request", "GET", "/v1/scheduling/publishers")
    await client("batch", calls.read_insurance_plan("H1"))
    assert [r.headers["user-agent"] for r in recorder.requests] == [f"{SDK} my-plugin/1.2.0"] * 2


@pytest.mark.anyio
async def test_a_user_agent_in_extra_headers_still_replaces_it_whole(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    client = Client(kind, recorder, app_info={"name": "my-plugin", "version": "1.2.0"})
    await client("read_insurance_plan", "H1", extra_headers={"User-Agent": "something-else/9"})
    assert recorder.last.headers["user-agent"] == "something-else/9"


REFUSED: list[tuple[str, Any, str]] = [
    ("not a dict", "my-plugin", r"^app_info must be a dict with a name, .*: 'my-plugin' is not one\.$"),
    ("a list", [{"name": "my-plugin"}], r"^app_info must be a dict with a name"),
    (
        "an unknown key",
        {"name": "my-plugin", "partner_id": "pp_1"},
        r"^app_info takes name, version and url only: 'partner_id' is not one of them\.$",
    ),
    ("no name", {"version": "1.2.0"}, r"^app_info\[\"name\"\] is required: your app's name, such as \"my-plugin\"\.$"),
    ("an empty name", {"name": ""}, r"^app_info\[\"name\"\] is required"),
    (
        "a name with a space",
        {"name": "my plugin"},
        r"^app_info\[\"name\"\] must be letters, digits .*: 'my plugin' is not one\.$",
    ),
    ("a name with a slash", {"name": "my/plugin"}, r"^app_info\[\"name\"\] must be .*: 'my/plugin' is not one\.$"),
    ("a name with a line break", {"name": "my-plugin\r\nX-Injected: 1"}, r"^app_info\[\"name\"\] must be "),
    ("a name ending in a line break", {"name": "my-plugin\n"}, r"^app_info\[\"name\"\] must be "),
    ("a name that is not text", {"name": 42}, r"^app_info\[\"name\"\] must be .*: 42 is not one\.$"),
    (
        "a version with a space",
        {"name": "my-plugin", "version": "1.2 beta"},
        r"^app_info\[\"version\"\] must be .*such as \"1\.2\.0\": '1\.2 beta' is not one\.$",
    ),
    ("a version with a slash", {"name": "my-plugin", "version": "1/2"}, r"^app_info\[\"version\"\] must be "),
    (
        "a url that is not http or https",
        {"name": "my-plugin", "url": "ftp://example.com"},
        r"^app_info\[\"url\"\] must be an http or https URL .*: 'ftp://example\.com' is not one\.$",
    ),
    ("a url that is not a URL", {"name": "my-plugin", "url": "example.com"}, r"^app_info\[\"url\"\] must be "),
    (
        "a url with a parenthesis",
        {"name": "my-plugin", "url": "https://example.com/a(b)"},
        r"^app_info\[\"url\"\] must be ",
    ),
    ("a url with a space", {"name": "my-plugin", "url": "https://example.com/a b"}, r"^app_info\[\"url\"\] must be "),
    (
        "a url with a backslash",
        {"name": "my-plugin", "url": "https://example.com/a\\b"},
        r"^app_info\[\"url\"\] must be ",
    ),
    ("a url beyond ASCII", {"name": "my-plugin", "url": "https://example.com/é"}, r"^app_info\[\"url\"\] must be "),
]


@pytest.mark.parametrize(("app_info", "message"), [(a, m) for _, a, m in REFUSED], ids=[r[0] for r in REFUSED])
@pytest.mark.parametrize("client_class", [Haau3, AsyncHaau3])
def test_what_the_header_cannot_carry_is_refused_when_the_client_is_made(
    client_class: type[Haau3] | type[AsyncHaau3], app_info: Any, message: str
) -> None:
    with pytest.raises(Haau3Error, match=message):
        client_class(api_key=KEY, app_info=app_info)


@pytest.mark.anyio
async def test_nothing_is_sent_when_it_is_refused(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    with pytest.raises(Haau3Error):
        Client(kind, recorder, app_info={"name": "my plugin"})
    assert recorder.requests == []


@pytest.mark.anyio
async def test_every_character_a_token_may_hold_is_taken(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    client = Client(kind, recorder, app_info={"name": "a-Z_0.9~!#$%&'*+^`|", "version": "1.0.0-rc.1+build.5"})
    await client("read_insurance_plan", "H1")
    assert recorder.last.headers["user-agent"] == f"{SDK} a-Z_0.9~!#$%&'*+^`|/1.0.0-rc.1+build.5"
