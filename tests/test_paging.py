"""next_cursor(): the cursor in a search page's next link, for a search continued in a later request."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

import haau3
from haau3 import AsyncHaau3, Haau3, Haau3Error

from .helpers import SPEC, Recorder, answer, async_client, sync_client

BASE = "https://api.haau3.com/v1/fhir/r4"


def page(**links: str) -> Any:
    """A search page with the links given, by relation."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "link": [{"relation": relation, "url": url} for relation, url in links.items()],
    }


def test_it_is_the_next_links_page_value() -> None:
    assert (
        haau3.next_cursor(
            page(self=f"{BASE}/Slot?status=free", next=f"{BASE}/Slot?status=free&_count=25&page=eyJ2IjoxfQ")
        )
        == "eyJ2IjoxfQ"
    )


def test_it_is_none_on_the_last_page_which_has_no_next_link() -> None:
    assert haau3.next_cursor(page(self=f"{BASE}/Slot?status=free&page=eyJ2IjoxfQ")) is None
    assert haau3.next_cursor({"resourceType": "Bundle", "type": "searchset"}) is None


@pytest.mark.parametrize("url", [f"{BASE}/Slot?status=free", f"{BASE}/Slot?status=free&page="])
def test_it_raises_on_a_next_link_with_no_cursor_rather_than_reading_it_as_the_last_page(url: str) -> None:
    with pytest.raises(Haau3Error, match="carries no page cursor"):
        haau3.next_cursor(page(next=url))


def test_it_reads_a_relative_next_link() -> None:
    assert haau3.next_cursor(page(next="Slot?status=free&page=abc")) == "abc"
    assert haau3.next_cursor(page(next="?page=abc")) == "abc"


def test_it_raises_on_a_next_link_that_is_no_url() -> None:
    with pytest.raises(Haau3Error, match="is not a URL"):
        haau3.next_cursor(page(next="https://[not-a-host"))
    with pytest.raises(Haau3Error, match="is not a URL"):
        not_a_url: Any = {"resourceType": "Bundle", "link": [{"relation": "next", "url": 7}]}
        haau3.next_cursor(not_a_url)


def test_it_raises_on_two_cursors_rather_than_choosing_one() -> None:
    with pytest.raises(Haau3Error, match="carries 2 page cursors"):
        haau3.next_cursor(page(next=f"{BASE}/Slot?page=one&page=two"))


def test_it_reads_the_first_next_link_as_pages_follows_it() -> None:
    two: Any = {
        "resourceType": "Bundle",
        "link": [{"relation": "next", "url": "?page=first"}, {"relation": "next", "url": "?page=second"}],
    }
    assert haau3.next_cursor(two) == "first"


def test_a_percent_encoded_cursor_is_decoded_and_sent_back_as_it_was() -> None:
    cursor = haau3.next_cursor(page(next=f"{BASE}/Slot?page=a%2Fb%3D%3D"))
    assert cursor == "a/b=="
    recorder = Recorder(answer(body=page()))
    with sync_client(recorder) as client:
        client.search_slot(page=cursor)
    assert dict(parse_qsl(recorder.requests[0].url.query.decode()))["page"] == "a/b=="


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_each_client_reads_it_with_no_request(cls: type) -> None:
    recorder = Recorder()
    client = sync_client(recorder) if cls is Haau3 else async_client(recorder)
    assert client.next_cursor(page(next=f"{BASE}/Slot?page=abc")) == "abc"
    assert recorder.requests == []


# The API's own example answer to a Slot search, with the links it wrote: the search as sent in
# `self`, and the same search with the cursor in `next`.
_OP = SPEC["paths"]["/v1/fhir/r4/Slot"]["get"]
_CONTENT = next(iter(_OP["responses"]["200"]["content"].values()))
EXAMPLE: Any = _CONTENT.get("example") or next(iter(_CONTENT["examples"].values()))["value"]


def _link(relation: str) -> str:
    return str(next(link["url"] for link in EXAMPLE["link"] if link["relation"] == relation))


def _sorted(url: str) -> list[tuple[str, str]]:
    return sorted(parse_qsl(urlsplit(url).query, keep_blank_values=True))


# The search as its caller keeps it: every parameter `self` shows, but the includes the helper sends,
# in the Python names the helper takes.
_PYTHON_NAMES = {"provider-identifier": "provider_identifier", "_count": "count"}


def _search() -> dict[str, Any]:
    search: dict[str, Any] = {}
    for name, value in parse_qsl(urlsplit(_link("self")).query):
        if name.startswith("_include"):
            continue
        key = _PYTHON_NAMES.get(name, name)
        if key == "start":
            search.setdefault("start", []).append(value)
        elif key == "count":
            search[key] = int(value)
        else:
            search[key] = value
    return search


def test_a_search_continued_with_the_cursor_sends_the_request_the_apis_next_link_names() -> None:
    recorder = Recorder(answer(body=EXAMPLE), answer(body=page()))
    search = _search()
    with sync_client(recorder) as client:
        first = client.search_slot_with_includes(**search)
        assert _sorted(str(recorder.requests[0].url)) == _sorted(_link("self"))
        cursor = client.next_cursor(first)
        assert cursor == dict(parse_qsl(urlsplit(_link("next")).query))["page"]
        second = client.search_slot_with_includes(**search, page=cursor)
        assert client.next_cursor(second) is None
    assert recorder.requests[1].url.path == urlsplit(_link("next")).path
    assert _sorted(str(recorder.requests[1].url)) == _sorted(_link("next"))
