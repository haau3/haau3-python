"""escape_search_value(): a value a person typed, with FHIR's special characters escaped."""

from __future__ import annotations

from urllib.parse import parse_qsl

import haau3

from .helpers import Recorder, answer, sync_client

EMPTY = {"resourceType": "Bundle", "type": "searchset", "total": 0}


def test_it_puts_a_backslash_before_each_of_fhirs_special_characters() -> None:
    assert haau3.escape_search_value("Austin,") == "Austin\\,"
    assert haau3.escape_search_value("a|b") == "a\\|b"
    assert haau3.escape_search_value("$5") == "\\$5"
    assert haau3.escape_search_value("C:\\temp") == "C:\\\\temp"
    assert haau3.escape_search_value("Austin, TX | $ \\") == "Austin\\, TX \\| \\$ \\\\"


def test_it_leaves_every_other_character_as_it_is() -> None:
    plain = "San José St. Mary's (East) 123-456 ü 😀"
    assert haau3.escape_search_value(plain) == plain
    assert haau3.escape_search_value("") == ""


def test_it_escapes_a_backslash_once() -> None:
    assert haau3.escape_search_value("\\,") == "\\\\\\,"


def test_it_reaches_the_api_as_the_escaped_value() -> None:
    recorder = Recorder(answer(body=EMPTY))
    with sync_client(recorder) as client:
        client.search_slot(location_address_city=haau3.escape_search_value("Austin, TX"))
    assert dict(parse_qsl(recorder.requests[0].url.query.decode()))["location-address-city"] == "Austin\\, TX"
