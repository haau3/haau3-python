"""parameters(), parameter() and review_flags(): a Parameters answer's named parts, and the review flags
on a structured Dosage, with no request. Read from the spec's own example of $structure-dosage's answer,
so a change to that answer's shape fails here."""

from __future__ import annotations

import re
from typing import Any, cast

import httpx2
import pytest

from haau3 import AsyncHaau3, DosageReview, Haau3, Haau3Error, ReviewFlag

from .helpers import SPEC, async_client, first_example, sync_client

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
