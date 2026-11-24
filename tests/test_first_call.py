"""The first call's query (scripts/generate.py's first_call_params): the slot search's example less its
includes, which the example must send exactly as search_slot_with_includes does, so the sample is the
request the Sandbox answers."""

from __future__ import annotations

import json
from typing import Any

import generate
import pytest

NPI: dict[str, Any] = {"provider-identifier": "http://hl7.org/fhir/sid/us-npi|2900000227", "_count": 1}
HELPERS: dict[str, Any] = {
    "_include": "Slot:schedule",
    "_include:iterate": ["Schedule:actor", "PractitionerRole:practitioner", "HealthcareService:location"],
}


def test_the_first_calls_query_is_the_examples_less_the_includes_the_helper_sends() -> None:
    assert generate.first_call_params("searchSlot", {**NPI, **HELPERS}) == NPI


def test_it_takes_the_includes_in_any_order_one_value_or_a_list() -> None:
    reordered = {**NPI, "_include": ["Slot:schedule"], "_include:iterate": list(reversed(HELPERS["_include:iterate"]))}
    assert generate.first_call_params("searchSlot", reordered) == NPI


def test_it_is_refused_when_the_example_sends_one_include_fewer_or_one_more_than_the_helper() -> None:
    fewer = {**NPI, **HELPERS, "_include:iterate": ["Schedule:actor", "PractitionerRole:practitioner"]}
    with pytest.raises(ValueError) as raised:
        generate.first_call_params("searchSlot", fewer)
    assert str(raised.value) == (
        "searchSlot: the example's _include:iterate is not what search_slot_with_includes sends, "
        "so the first call would not be the example"
    )
    more = {**NPI, **HELPERS, "_include": ["Slot:schedule", "Slot:other"]}
    with pytest.raises(ValueError, match="the example's _include is not"):
        generate.first_call_params("searchSlot", more)
    with pytest.raises(ValueError, match="the example's _include is not"):
        generate.first_call_params("searchSlot", NPI)


def test_it_is_refused_when_the_example_sends_no_query_at_all() -> None:
    with pytest.raises(ValueError, match=r"^searchSlot: the first call's example sends no query parameters$"):
        generate.first_call_params("searchSlot", {})


def test_the_committed_first_call_is_the_examples_through_the_helper() -> None:
    """The pinned spec's own example passes, so the generator wrote the sample from it."""
    spec = generate.Spec(json.loads(generate.SPEC.read_text()))
    op = next(o for o in spec.ops if o.id == generate.FIRST_CALL)
    source = generate._first_call_sample(spec, op, spec.spec["paths"][op.path]["get"])
    assert "client.search_slot_with_includes(" in source
    assert "include" not in source.split("search_slot_with_includes(")[1].split(")")[0]
