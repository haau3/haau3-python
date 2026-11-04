"""lookup_result(), validation_result() and designations(): a terminology answer, typed, with no request,
as client methods and as plain functions. The spec's own examples are read with "every part is typed or
kept" checks, so a change to their values cannot break these tests and a change to their shape does.
The answers of a code system the API holds are written here, as the API writes them: an ICD-10-CM code
with its short title, and a made-up drug in RxNorm's shape."""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx2
import pytest

import haau3
from haau3 import (
    PREFERRED_FOR_LANGUAGE_USE,
    RXNORM_SYSTEM,
    SYNONYM_USE,
    AsyncHaau3,
    BooleanProperty,
    CodingProperty,
    DecimalProperty,
    Designation,
    DesignationUse,
    Haau3,
    Haau3Error,
    IntegerProperty,
    LookupResult,
    TextProperty,
    ValidationResult,
    designations,
    lookup_result,
    validation_result,
)

from .helpers import SPEC, async_client, first_example, sync_client


def _example(path: str) -> Any:
    return first_example(SPEC["paths"][path]["get"]["responses"]["200"]["content"]["application/fhir+json"])


ICD10CM = "http://hl7.org/fhir/sid/icd-10-cm"
LONG = (
    "Diabetes mellitus due to underlying condition with diabetic macular edema, resolved following treatment, right eye"
)
SHORT = "Diab with diabetic macular edema, resolved fol trtmt, r eye"


def _use(use: DesignationUse) -> dict[str, Any]:
    return {"name": "use", "valueCoding": {"system": use.system, "code": use.code, "display": use.display}}


def _answer(*parameter: dict[str, Any]) -> Any:
    return {"resourceType": "Parameters", "parameter": list(parameter)}


# An ICD-10-CM lookup, as the API answers one: the head, both titles, then notSelectable.
ICD10CM_LOOKUP: Any = _answer(
    {"name": "name", "valueString": "ICD-10-CM"},
    {"name": "code", "valueCode": "E08.37X1"},
    {"name": "system", "valueUri": ICD10CM},
    {"name": "version", "valueString": "2026-10-01"},
    {"name": "display", "valueString": LONG},
    {"name": "abstract", "valueBoolean": False},
    {
        "name": "designation",
        "part": [
            {"name": "language", "valueCode": "en"},
            _use(PREFERRED_FOR_LANGUAGE_USE),
            {"name": "value", "valueString": LONG},
        ],
    },
    {
        "name": "designation",
        "part": [{"name": "language", "valueCode": "en"}, _use(SYNONYM_USE), {"name": "value", "valueString": SHORT}],
    },
    {
        "name": "property",
        "part": [{"name": "code", "valueCode": "notSelectable"}, {"name": "value", "valueBoolean": False}],
    },
)

WARNING = "RxNorm is held in English only, so the display and designations are in English."

# A made-up drug in RxNorm's shape: names with no use, and properties of three types.
RXNORM_LOOKUP: Any = _answer(
    {"name": "name", "valueString": "RxNorm"},
    {"name": "code", "valueCode": "R1000"},
    {"name": "system", "valueUri": RXNORM_SYSTEM},
    {"name": "version", "valueString": "09082026"},
    {"name": "display", "valueString": "fake drug 10 mg tablet"},
    {"name": "abstract", "valueBoolean": False},
    {"name": "warning", "valueString": WARNING},
    {
        "name": "designation",
        "part": [
            {"name": "language", "valueCode": "en"},
            _use(PREFERRED_FOR_LANGUAGE_USE),
            {"name": "value", "valueString": "fake drug 10 mg tablet"},
        ],
    },
    {
        "name": "designation",
        "part": [{"name": "language", "valueCode": "en"}, {"name": "value", "valueString": "fake drug 10 mg"}],
    },
    {"name": "property", "part": [{"name": "code", "valueCode": "TTY"}, {"name": "value", "valueCode": "SCD"}]},
    {"name": "property", "part": [{"name": "code", "valueCode": "inactive"}, {"name": "value", "valueBoolean": False}]},
    {
        "name": "property",
        "part": [
            {"name": "code", "valueCode": "has_dose_form"},
            {"name": "value", "valueCoding": {"system": RXNORM_SYSTEM, "code": "R1002", "display": "fake form"}},
        ],
    },
)


def _accounted_for(read: LookupResult | ValidationResult) -> int:
    """Every top-level part of an answer, as the reader typed or kept it, so none is lost."""
    fields = {field.name: getattr(read, field.name) for field in dataclasses.fields(read)}
    lists = ("designations", "properties", "other")
    typed = sum(1 for name, value in fields.items() if name not in lists and value is not None)
    kept = sum(len(fields[name]) for name in lists if name in fields)
    return typed + kept


def _sends_nothing(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("lookup_result(), validation_result() and designations() send nothing")


@pytest.fixture(params=["sync", "async"])
def client(request: pytest.FixtureRequest) -> Haau3 | AsyncHaau3:
    return sync_client(_sends_nothing) if request.param == "sync" else async_client(_sends_nothing)


def test_lookup_result_reads_an_icd10cm_lookup(client: Haau3 | AsyncHaau3) -> None:
    assert client.lookup_result(ICD10CM_LOOKUP) == LookupResult(
        name="ICD-10-CM",
        code="E08.37X1",
        system=ICD10CM,
        version="2026-10-01",
        display=LONG,
        abstract=False,
        warning=None,
        designations=(
            Designation(language="en", use=PREFERRED_FOR_LANGUAGE_USE, value=LONG, other=()),
            Designation(language="en", use=SYNONYM_USE, value=SHORT, other=()),
        ),
        properties=(BooleanProperty("notSelectable", "boolean", False, None, (), ()),),
        other=(),
    )


def test_lookup_result_types_each_property_by_its_value_and_reads_the_warning() -> None:
    read = lookup_result(RXNORM_LOOKUP)
    assert read.warning == WARNING
    assert read.properties == (
        TextProperty("TTY", "code", "SCD", None, (), ()),
        BooleanProperty("inactive", "boolean", False, None, (), ()),
        CodingProperty(
            "has_dose_form", "Coding", {"system": RXNORM_SYSTEM, "code": "R1002", "display": "fake form"}, None, (), ()
        ),
    )
    form = read.properties[2]
    # Checking type narrows value.
    assert form.type == "Coding"
    assert form.value.get("code") == "R1002"
    assert read.designations[1] == Designation(language="en", use=None, value="fake drug 10 mg", other=())


def test_lookup_result_reads_the_specs_own_example_typing_or_keeping_every_part() -> None:
    answer = _example("/v1/fhir/r4/CodeSystem/$lookup")
    read = lookup_result(answer)
    assert _accounted_for(read) == len(answer["parameter"])
    # The example names its system as a valueString, which reads as the system all the same.
    assert read.system == "http://loinc.org"


def test_lookup_result_reads_subproperties_a_description_and_the_parts_it_does_not_know() -> None:
    read = lookup_result(
        _answer(
            {"name": "definition", "valueString": "A made-up definition"},
            {
                "name": "designation",
                "part": [
                    {"name": "language", "valueCode": "en"},
                    {"name": "additionalUse", "valueCoding": {"system": "http://example.org/use", "code": "extra"}},
                    {"name": "value", "valueString": "a name"},
                ],
            },
            {
                "name": "property",
                "part": [
                    {"name": "code", "valueCode": "parent"},
                    {"name": "value", "valueInteger": 3},
                    {"name": "description", "valueString": "how many"},
                    {"name": "source", "valueUri": "http://example.org/source"},
                    {
                        "name": "subproperty",
                        "part": [{"name": "code", "valueCode": "child"}, {"name": "value", "valueDecimal": 1.5}],
                    },
                    {
                        "name": "subproperty",
                        "part": [
                            {"name": "code", "valueCode": "dated"},
                            {"name": "value", "valueDateTime": "2026-11-04"},
                        ],
                    },
                ],
            },
        )
    )
    assert read.other == ({"name": "definition", "valueString": "A made-up definition"},)
    assert read.designations[0].other == (
        {"name": "additionalUse", "valueCoding": {"system": "http://example.org/use", "code": "extra"}},
    )
    assert read.properties == (
        IntegerProperty(
            "parent",
            "integer",
            3,
            "how many",
            (
                DecimalProperty("child", "decimal", 1.5, None, (), ()),
                TextProperty("dated", "dateTime", "2026-11-04", None, (), ()),
            ),
            ({"name": "source", "valueUri": "http://example.org/source"},),
        ),
    )


def test_lookup_result_keeps_a_part_it_cannot_read_in_other_in_answer_order() -> None:
    no_value = {"name": "designation", "part": [{"name": "language", "valueCode": "en"}]}
    other_type = {
        "name": "property",
        "part": [{"name": "code", "valueCode": "x"}, {"name": "value", "valueAttachment": {}}],
    }
    text_abstract = {"name": "abstract", "valueString": "false"}
    no_code = {"name": "property", "part": [{"name": "value", "valueString": "y"}]}
    read = lookup_result(_answer(no_value, {"name": "display", "valueString": "A"}, other_type, text_abstract, no_code))
    assert read.other == (no_value, other_type, text_abstract, no_code)
    assert read.abstract is None
    assert read.display == "A"
    assert read.designations == ()
    assert read.properties == ()


def test_lookup_result_keeps_a_use_with_no_code_in_the_designations_other() -> None:
    no_code: Any = {"name": "use", "valueCoding": {"system": "http://example.org/use"}}
    read = lookup_result(
        _answer({"name": "designation", "part": [no_code, {"name": "value", "valueString": "a name"}]})
    )
    assert read.designations == (Designation(language=None, use=None, value="a name", other=(no_code,)),)


def test_lookup_result_reads_a_missing_part_as_none() -> None:
    assert lookup_result({"resourceType": "Parameters"}) == LookupResult(
        name=None,
        code=None,
        system=None,
        version=None,
        display=None,
        abstract=None,
        warning=None,
        designations=(),
        properties=(),
        other=(),
    )


def test_lookup_result_raises_when_a_part_fhir_allows_once_comes_twice() -> None:
    two = _answer({"name": "display", "valueString": "A"}, {"name": "display", "valueString": "B"})
    with pytest.raises(
        Haau3Error, match=r"The Parameters holds 2 parts named display, .* Read them with parameters\(\)\."
    ):
        lookup_result(two)
    two_values = _answer(
        {"name": "designation", "part": [{"name": "value", "valueString": "A"}, {"name": "value", "valueString": "B"}]}
    )
    with pytest.raises(
        Haau3Error, match=r"^A designation holds 2 parts named value, so which one is meant is ambiguous\.$"
    ):
        lookup_result(two_values)


def test_validation_result_reads_a_display_that_did_not_match(client: Haau3 | AsyncHaau3) -> None:
    answer = _answer(
        {"name": "result", "valueBoolean": False},
        {"name": "display", "valueString": "Heart rate"},
        {"name": "message", "valueString": "The display sent is not the code's."},
    )
    assert client.validation_result(answer) == ValidationResult(
        result=False, display="Heart rate", message="The display sent is not the code's.", other=()
    )


@pytest.mark.parametrize("path", ["/v1/fhir/r4/CodeSystem/$validate-code", "/v1/fhir/r4/ValueSet/$validate-code"])
def test_validation_result_reads_the_specs_own_examples_typing_or_keeping_every_part(path: str) -> None:
    answer = _example(path)
    read = validation_result(answer)
    assert read.result is True
    assert _accounted_for(read) == len(answer["parameter"])


def test_validation_result_raises_on_two_results() -> None:
    with pytest.raises(Haau3Error):
        validation_result(_answer({"name": "result", "valueBoolean": True}, {"name": "result", "valueBoolean": False}))


def test_designations_finds_the_short_title_by_its_use_and_language(client: Haau3 | AsyncHaau3) -> None:
    read = lookup_result(ICD10CM_LOOKUP)
    assert [d.value for d in client.designations(read, use=SYNONYM_USE, language="en")] == [SHORT]
    assert client.designations(read, use=PREFERRED_FOR_LANGUAGE_USE)[0].value == LONG
    assert client.designations(read) == list(read.designations)


def test_designations_matches_a_use_by_its_system_and_code_together() -> None:
    read = lookup_result(ICD10CM_LOOKUP)
    assert designations(read, use=DesignationUse(system="http://snomed.info/sct", code="synonym")) == []
    assert (
        len(designations(read, use=DesignationUse(system=SYNONYM_USE.system, code="synonym", display="another"))) == 1
    )


def test_designations_matches_a_language_as_rfc_4647_basic_filtering_does() -> None:
    read = lookup_result(
        _answer(
            *(
                {
                    "name": "designation",
                    "part": [{"name": "language", "valueCode": tag}, {"name": "value", "valueString": tag}],
                }
                for tag in ("en", "en-US", "EN-gb", "eng", "de")
            ),
            {"name": "designation", "part": [{"name": "value", "valueString": "no language"}]},
        )
    )

    def found(language: str) -> list[str]:
        return [d.value for d in designations(read, language=language)]

    assert found("en") == ["en", "en-US", "EN-gb"]
    assert found("EN-US") == ["en-US"]
    assert found("*") == ["en", "en-US", "EN-gb", "eng", "de"]
    assert "no language" in [d.value for d in designations(read)]


def test_designations_finds_one_with_no_use_only_when_no_use_is_asked_for() -> None:
    read = lookup_result(RXNORM_LOOKUP)
    assert "fake drug 10 mg" in [d.value for d in designations(read, language="en")]
    assert designations(read, use=SYNONYM_USE) == []


def test_the_plain_functions_read_what_the_methods_read_with_no_client(client: Haau3 | AsyncHaau3) -> None:
    assert lookup_result(ICD10CM_LOOKUP) == client.lookup_result(ICD10CM_LOOKUP)
    assert validation_result(ICD10CM_LOOKUP) == client.validation_result(ICD10CM_LOOKUP)
    read = lookup_result(ICD10CM_LOOKUP)
    assert designations(read, use=SYNONYM_USE) == client.designations(read, use=SYNONYM_USE)
    assert haau3.designations is designations


def test_the_constants_cannot_be_changed() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        SYNONYM_USE.code = "another"  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]
