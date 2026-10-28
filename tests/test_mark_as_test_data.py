"""mark_as_test_data labels exactly the resources the API reads for the test data label.

The request, each Bundle entry, each Parameters resource part at any depth, and the same again
inside them.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from haau3 import mark_as_test_data

from .helpers import operations

HTEST = {"system": "http://terminology.hl7.org/CodeSystem/v3-ActReason", "code": "HTEST", "display": "test health data"}


def _is_htest(value: object) -> bool:
    return isinstance(value, dict) and value.get("system") == HTEST["system"] and value.get("code") == HTEST["code"]


def _nodes(value: object) -> list[object]:
    """Every dict and list in a value, walked with a stack."""
    found: list[object] = []
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found.append(node)
            stack.extend(node.values())
        elif isinstance(node, list):
            found.append(node)
            stack.extend(node)
    return found


def labels(value: object) -> int:
    """How many HTEST labels a value holds, anywhere in it."""
    return sum(1 for node in _nodes(value) if _is_htest(node))


def strip(value: Any) -> Any:
    """A copy with every HTEST label taken out, and a `meta` or `security` it leaves empty with it."""
    stripped = copy.deepcopy(value)
    for node in _nodes(stripped):
        if isinstance(node, dict) and isinstance(node.get("meta"), dict):
            meta = node["meta"]
            if isinstance(meta.get("security"), list):
                meta["security"] = [coding for coding in meta["security"] if not _is_htest(coding)]
                if not meta["security"]:
                    del meta["security"]
            if not meta:
                del node["meta"]
    return stripped


def security(resource: Any) -> Any:
    return resource.get("meta", {}).get("security")


def _labelled_examples() -> list[tuple[str, Any]]:
    found = []
    for operation_id, _, _, op in operations():
        for media in op.get("requestBody", {}).get("content", {}).values():
            for name, example in media.get("examples", {}).items():
                if labels(example["value"]) > 0:
                    found.append((f"{operation_id}: {name}", example["value"]))
    return found


LABELLED_EXAMPLES = _labelled_examples()


def test_finds_the_spec_s_labelled_requests_to_check_against() -> None:
    names = {name.split(":")[0] for name, _ in LABELLED_EXAMPLES}
    assert {"reconcileMedications", "sandboxProcessMessage"} <= names


@pytest.mark.parametrize(("name", "example"), LABELLED_EXAMPLES, ids=[name for name, _ in LABELLED_EXAMPLES])
def test_labels_each_spec_example_exactly_as_the_spec_labels_it(name: str, example: Any) -> None:
    unlabelled = strip(example)
    # The control: stripping took every label out, so the match below is the helper's work.
    assert labels(unlabelled) == 0
    assert mark_as_test_data(unlabelled) == example


def test_labels_each_resource_part_at_any_depth_and_the_entries_of_a_bundle_sent_as_a_part() -> None:
    patient = {"resourceType": "Patient"}
    parameters: dict[str, Any] = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "bundle",
                "resource": {
                    "resourceType": "Bundle",
                    "type": "collection",
                    "entry": [{"resource": patient}, {"fullUrl": "urn:uuid:no-resource"}],
                },
            },
            {"name": "outer", "part": [{"name": "inner", "part": [{"name": "resource", "resource": patient}]}]},
            {"name": "asOf", "valueDateTime": "2026-10-28"},
        ],
    }
    marked = mark_as_test_data(parameters)
    bundle = marked["parameter"][0]["resource"]
    assert security(marked) == [HTEST]
    assert security(bundle) == [HTEST]
    assert security(bundle["entry"][0]["resource"]) == [HTEST]
    assert security(marked["parameter"][1]["part"][0]["part"][0]["resource"]) == [HTEST]
    assert bundle["entry"][1] == {"fullUrl": "urn:uuid:no-resource"}
    assert marked["parameter"][2] == {"name": "asOf", "valueDateTime": "2026-10-28"}
    assert labels(marked) == 4


def test_leaves_a_contained_resource_to_the_one_that_contains_it() -> None:
    order: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/p"},
        "medicationReference": {"reference": "#med"},
        "contained": [{"resourceType": "Medication", "id": "med"}],
    }
    body: dict[str, Any] = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": order}]}
    marked = mark_as_test_data(body)
    labelled = marked["entry"][0]["resource"]
    assert security(labelled) == [HTEST]
    assert labelled["contained"][0] == {"resourceType": "Medication", "id": "med"}


def test_keeps_the_labels_and_the_meta_already_there_and_never_adds_a_second_htest() -> None:
    confidential = {"system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality", "code": "N"}
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "collection",
        "meta": {"security": [{"system": HTEST["system"], "code": "HTEST"}]},
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "meta": {"profile": ["http://example.org/p"], "security": [confidential]},
                }
            }
        ],
    }
    marked = mark_as_test_data(bundle)
    assert security(marked) == [{"system": HTEST["system"], "code": "HTEST"}]
    assert marked["entry"][0]["resource"]["meta"] == {
        "profile": ["http://example.org/p"],
        "security": [confidential, HTEST],
    }


def test_never_changes_the_request_it_is_given() -> None:
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": {"resourceType": "Patient"}}],
    }
    before = copy.deepcopy(bundle)
    marked = mark_as_test_data(bundle)
    assert bundle == before
    assert marked is not bundle
    assert marked["entry"] is not bundle["entry"]
    assert labels(marked) == 2


def test_gives_the_same_request_when_it_marks_one_already_marked() -> None:
    once = mark_as_test_data(LABELLED_EXAMPLES[0][1])
    assert mark_as_test_data(once) == once


def test_labels_a_request_of_any_other_resource_type_itself() -> None:
    assert security(mark_as_test_data({"resourceType": "Patient"})) == [HTEST]


# A request nested deeper than the JSON encoder can encode could not be sent anyway, so the depth
# here is one that encodes; the copy and the walk use a stack, not recursion.
def test_labels_every_level_of_a_deeply_nested_request() -> None:
    depth = 200
    root: dict[str, Any] = {"resourceType": "Parameters", "parameter": []}
    current = root
    for _ in range(depth):
        nested: dict[str, Any] = {"resourceType": "Parameters", "parameter": []}
        current["parameter"].append({"name": "level", "resource": nested})
        current = nested
    marked = mark_as_test_data(root)
    assert labels(marked) == depth + 1
    json.dumps(marked)


def test_reads_a_tuple_as_the_json_encoder_sends_it() -> None:
    # The JSON encoder sends a tuple as an array, so the API reads its entries, and so does the helper.
    entries = ({"resource": {"resourceType": "Patient"}},)
    body: dict[str, Any] = {"resourceType": "Bundle", "type": "collection", "entry": entries}
    marked = mark_as_test_data(body)
    assert security(marked["entry"][0]["resource"]) == [HTEST]
    assert entries == ({"resource": {"resourceType": "Patient"}},)


def test_keeps_a_value_that_is_not_json_as_it_is() -> None:
    born = object()
    body: dict[str, Any] = {"resourceType": "Patient", "birthDate": born}
    marked = mark_as_test_data(body)
    assert marked["birthDate"] is born
