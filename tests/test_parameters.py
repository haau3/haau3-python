"""parameters(), parameter() and review_flags(): a Parameters answer's named parts, and the review flags
on a structured Dosage, with no request, as client methods and as plain functions; and
structure_dosage_request(), the request $structure-dosage takes. Read from the spec's own examples of
$structure-dosage's request and answer, so a change to their shape fails here."""

from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx2
import pytest

import haau3
from haau3 import AsyncHaau3, BatchOk, DosageReview, Haau3, Haau3Error, ReviewFlag, calls

from .helpers import SPEC, Recorder, answer, async_client, first_example, sync_client

ANSWER: Any = first_example(
    SPEC["paths"]["/v1/fhir/r4/$structure-dosage"]["post"]["responses"]["200"]["content"]["application/fhir+json"]
)


def _sends_nothing(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("parameters(), parameter() and review_flags() send nothing")


@pytest.fixture(params=["sync", "async"])
def client(request: pytest.FixtureRequest) -> Haau3 | AsyncHaau3:
    return sync_client(_sends_nothing) if request.param == "sync" else async_client(_sends_nothing)


def test_parameters_and_parameter_give_the_parts_with_a_name(client: Haau3 | AsyncHaau3) -> None:
    assert len(client.parameters(ANSWER, "dosage")) == 1
    part = client.parameter(ANSWER, "dosage")
    assert part is not None
    assert part["valueDosage"]["text"] == "Instill 2 drops in the affected ear twice daily"


def test_they_give_nothing_for_a_name_the_answer_does_not_hold(client: Haau3 | AsyncHaau3) -> None:
    assert client.parameters(ANSWER, "display") == []
    assert client.parameter(ANSWER, "display") is None
    assert client.parameters({"resourceType": "Parameters"}, "dosage") == []


def test_parameter_raises_when_two_parts_share_the_name(client: Haau3 | AsyncHaau3) -> None:
    two: Any = {
        "resourceType": "Parameters",
        "parameter": [{"name": "property", "valueString": "a"}, {"name": "property", "valueString": "b"}],
    }
    with pytest.raises(Haau3Error, match="2 parts named property"):
        client.parameter(two, "property")
    assert [part["valueString"] for part in client.parameters(two, "property")] == ["a", "b"]


def test_review_flags_reads_the_examples_flag_with_its_words_and_its_versions(client: Haau3 | AsyncHaau3) -> None:
    part = client.parameter(ANSWER, "dosage")
    assert part is not None
    review = client.review_flags(part["valueDosage"])
    assert review.flags == (
        ReviewFlag(code="route-form-mismatch", display="Route and dose form differ", words="in the affected ear"),
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.v\d+", cast(str, review.engine_version))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.v\d+", cast(str, review.vocabulary_version))
    assert re.fullmatch(r"\d{8}", cast(str, review.rxnorm_release))


def test_review_flags_gives_none_for_a_dosage_that_carries_none(client: Haau3 | AsyncHaau3) -> None:
    # The control: no extensions, no flags and no versions.
    assert client.review_flags({"text": "Take 1 tablet daily"}) == DosageReview(
        flags=(), engine_version=None, vocabulary_version=None, rxnorm_release=None
    )


def test_review_flags_leaves_out_other_extensions_and_a_flag_with_no_code(client: Haau3 | AsyncHaau3) -> None:
    flag = "https://haau3.com/fhir/StructureDefinition/structured-sig-review-flag"
    dosage: Any = {
        "extension": [
            {"url": "https://example.org/other", "valueString": "x"},
            {"url": flag, "extension": [{"url": "words", "valueString": "x"}]},
            {
                "url": flag,
                "extension": [{"url": "code", "valueCoding": {"code": "as-directed", "display": "As directed"}}],
            },
        ]
    }
    assert client.review_flags(dosage).flags == (ReviewFlag(code="as-directed", display="As directed", words=None),)


def test_the_plain_functions_read_what_the_clients_methods_read_with_no_client(client: Haau3 | AsyncHaau3) -> None:
    assert haau3.parameters(ANSWER, "dosage") == client.parameters(ANSWER, "dosage")
    assert haau3.parameter(ANSWER, "dosage") == client.parameter(ANSWER, "dosage")
    dosage = ANSWER["parameter"][0]["valueDosage"]
    assert haau3.review_flags(dosage) == client.review_flags(dosage)
    two: Any = {"resourceType": "Parameters", "parameter": [{"name": "property"}, {"name": "property"}]}
    with pytest.raises(Haau3Error, match="2 parts named property"):
        haau3.parameter(two, "property")


REQUEST: Any = first_example(
    SPEC["paths"]["/v1/fhir/r4/$structure-dosage"]["post"]["requestBody"]["content"]["application/fhir+json"]
)


def test_structure_dosage_request_builds_the_specs_own_example_request() -> None:
    text = next(part["valueString"] for part in REQUEST["parameter"] if part["name"] == "text")
    medication = next(part["valueCodeableConcept"] for part in REQUEST["parameter"] if part["name"] == "medication")
    assert medication["coding"][0]["system"] == haau3.RXNORM_SYSTEM
    example = {key: value for key, value in REQUEST.items() if key != "id"}
    assert haau3.structure_dosage_request(text, medication=medication) == example


def test_structure_dosage_request_sends_a_text_part_alone_when_there_is_no_medication() -> None:
    assert haau3.structure_dosage_request("Take 1 tablet by mouth twice daily") == {
        "resourceType": "Parameters",
        "parameter": [{"name": "text", "valueString": "Take 1 tablet by mouth twice daily"}],
    }


def test_structure_dosage_request_keeps_every_coding_and_checks_nothing() -> None:
    medication: Any = {
        "coding": [
            {"system": haau3.NDC_SYSTEM, "code": "0378-0018-01"},
            {"system": haau3.RXNORM_SYSTEM, "code": "197361"},
        ]
    }
    assert haau3.structure_dosage_request("Take 1 tablet daily", medication=medication)["parameter"][1] == {
        "name": "medication",
        "valueCodeableConcept": medication,
    }
    # The API's refusals are the one rule: an empty text is sent as it is.
    assert haau3.structure_dosage_request("")["parameter"] == [{"name": "text", "valueString": ""}]


def test_structure_dosage_request_is_what_structure_dosage_sends_alone_and_in_a_batch() -> None:
    body = haau3.structure_dosage_request("Take 1 tablet by mouth twice daily")
    recorder = Recorder(
        answer(200, ANSWER),
        answer(
            200,
            {
                "resourceType": "Bundle",
                "type": "batch-response",
                "entry": [{"resource": ANSWER, "response": {"status": "200"}}],
            },
        ),
    )
    with sync_client(recorder) as client:
        client.structure_dosage(body)
        (entry,) = client.batch(calls.structure_dosage(body))
    assert json.loads(recorder.requests[0].content) == body
    sent = json.loads(recorder.requests[1].content)
    assert sent["entry"][0]["request"] == {"method": "POST", "url": "$structure-dosage"}
    assert sent["entry"][0]["resource"] == body
    assert isinstance(entry, BatchOk)
