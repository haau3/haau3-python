"""escape_search_value(): a value a person typed, with FHIR's special characters escaped.

search_token(): a coding as the value a token search parameter takes.
"""

from __future__ import annotations

from urllib.parse import parse_qsl

import pytest

import haau3
from haau3 import Haau3Error, fhir

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


HL7 = "http://terminology.hl7.org/CodeSystem/service-type"


def test_search_token_writes_a_coding_as_system_bar_code() -> None:
    assert haau3.search_token({"system": HL7, "code": "124"}) == f"{HL7}|124"


def test_search_token_takes_a_coding_and_an_expansion_entry_as_they_come() -> None:
    coding: fhir.Coding = {"system": HL7, "code": "124", "display": "General Practice", "version": "1"}
    entry: fhir.ValueSetContains = {"system": HL7, "code": "124", "display": "General Practice"}
    assert haau3.search_token(coding) == f"{HL7}|124"
    assert haau3.search_token(entry) == f"{HL7}|124"


def test_search_token_escapes_each_part_and_keeps_the_bar_between_them() -> None:
    assert haau3.search_token({"system": "urn:a|b", "code": "x,y$z\\w"}) == "urn:a\\|b|x\\,y\\$z\\\\w"


def test_search_token_is_any_of_several_codings_joined_by_commas_in_order() -> None:
    two = haau3.search_token({"system": HL7, "code": "124"}, {"system": "urn:a", "code": "1,2"})
    assert two == f"{HL7}|124,urn:a|1\\,2"
    entries: list[fhir.ValueSetContains] = [{"system": HL7, "code": "124"}, {"system": HL7, "code": "57"}]
    assert haau3.search_token(*entries) == f"{HL7}|124,{HL7}|57"


def test_search_token_raises_naming_what_is_missing() -> None:
    with pytest.raises(Haau3Error, match=r"^search_token\(\) needs a system: the coding has none\.$"):
        haau3.search_token({"code": "124"})
    with pytest.raises(Haau3Error, match=r"^search_token\(\) needs a code: the coding has none\.$"):
        haau3.search_token({"system": HL7})
    with pytest.raises(Haau3Error, match="needs a system"):
        haau3.search_token({"system": "", "code": "124"})
    with pytest.raises(Haau3Error, match=r"needs a code: coding 2 has none\.$"):
        haau3.search_token({"system": HL7, "code": "124"}, {"system": HL7, "code": ""})


def test_search_token_reaches_the_api_as_one_token() -> None:
    recorder = Recorder(answer(body=EMPTY))
    with sync_client(recorder) as client:
        client.search_slot(schedule_service_type=haau3.search_token({"system": "urn:a", "code": "1,2"}))
    sent = parse_qsl(recorder.requests[0].url.query.decode())
    assert [value for name, value in sent if name == "schedule.service-type"] == ["urn:a|1\\,2"]
