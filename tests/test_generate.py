"""The generator: its output is committed and current, its inputs pinned, and its rules hold."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from typing import Any

import fhir_emit
import generate
import pytest

from .helpers import ROOT, SPEC


def test_the_generated_files_are_current() -> None:
    # The generator's own tools are in the `generate` group; CI's checks job runs this with them.
    pytest.importorskip("datamodel_code_generator")
    pytest.importorskip("ruff")
    result = subprocess.run(
        [sys.executable, "scripts/generate.py", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_the_fhir_schema_is_hl7s_pinned_copy() -> None:
    data = (ROOT / "fhir" / "fhir.schema.json.zip").read_bytes()
    assert hashlib.sha256(data).hexdigest() == generate.FHIR_ZIP_SHA256
    with zipfile.ZipFile(ROOT / "fhir" / "fhir.schema.json.zip") as archive:
        assert hashlib.sha256(archive.read("fhir.schema.json")).hexdigest() == generate.FHIR_SCHEMA_SHA256


def _schema() -> dict[str, Any]:
    with zipfile.ZipFile(ROOT / "fhir" / "fhir.schema.json.zip") as archive:
        loaded: dict[str, Any] = json.loads(archive.read("fhir.schema.json"))
    return loaded


def _classes(path: str) -> dict[str, ast.ClassDef]:
    tree = ast.parse((ROOT / path).read_text())
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _keyword_bases(path: str) -> dict[str, dict[str, bool]]:
    """The TypedDicts written as a call, for keys that are Python keywords: each key, and whether it is Required."""
    bases: dict[str, dict[str, bool]] = {}
    for node in ast.parse((ROOT / path).read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.value.args) == 2:
            fields = node.value.args[1]
            if isinstance(fields, ast.Dict):
                name = node.targets[0]
                assert isinstance(name, ast.Name)
                bases[name.id] = {
                    ast.literal_eval(k): ast.literal_eval(v).startswith("Required[")
                    for k, v in zip(fields.keys, fields.values, strict=True)
                    if k is not None
                }
    return bases


KEYWORD_BASES = _keyword_bases("src/haau3/fhir.py")


def _keys(node: ast.ClassDef) -> dict[str, bool]:
    """Each key and whether it is Required, a keyword key from the TypedDict the class extends included."""
    keys = {
        s.target.id: ast.unparse(s.annotation).startswith("Required[")
        for s in node.body
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
    }
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in KEYWORD_BASES:
            keys.update(KEYWORD_BASES[base.id])
    return keys


def test_the_fhir_roots_are_the_specs_fhir_components() -> None:
    spec = generate.Spec(SPEC)
    assert len(spec.fhir) == 15
    classes = _classes("src/haau3/fhir.py")
    for name in spec.fhir:
        assert _keys(classes[name])["resourceType"] is True


def test_every_fhir_key_is_hl7s_own_and_required_as_hl7_says() -> None:
    definitions: dict[str, dict[str, Any]] = _schema()["definitions"]
    classes = _classes("src/haau3/fhir.py")
    written = 0
    for name, definition in definitions.items():
        cls = classes.get(fhir_emit.class_name(name))
        if cls is None or "properties" not in definition:
            continue
        keys = _keys(cls)
        assert set(keys) == set(definition["properties"]), name
        assert {k for k, required in keys.items() if required} == set(definition.get("required", [])), name
        written += 1
    assert written > 100
    outcome = _keys(classes["OperationOutcome"])
    assert outcome["issue"] is True
    assert _keys(classes["Bundle"])["type"] is False  # a required primitive may arrive as `_type` alone
    assert "_birthDate" in _keys(classes["Practitioner"])
    # A key that is a Python keyword is the class's own too, Required as HL7 says.
    assert _keys(classes["Coverage"])["class"] is False
    assert "_class" not in KEYWORD_BASES["_CoverageKeywords"]


def test_every_resource_type_a_request_example_carries_is_written() -> None:
    spec = generate.Spec(SPEC)
    classes = _classes("src/haau3/fhir.py")
    # The pharmacy benefit check's and MedList's records, as the spec's examples carry them.
    assert {"Claim", "Coverage", "MedicationRequest", "MedicationStatement", "MessageHeader", "Patient"} <= set(
        spec.carried
    )
    for name in spec.carried:
        assert _keys(classes[name])["resourceType"] is True, name


def test_other_resource_is_every_other_resource_type() -> None:
    definitions: dict[str, dict[str, Any]] = _schema()["definitions"]
    every = {ref["$ref"].rsplit("/", 1)[-1] for ref in definitions["ResourceList"]["oneOf"]}
    other = _classes("src/haau3/fhir.py")["OtherResource"]
    annotation = next(
        ast.unparse(s.annotation)
        for s in other.body
        if isinstance(s, ast.AnnAssign) and getattr(s.target, "id", "") == "resourceType"
    )
    named = set(ast.literal_eval("[" + annotation.removeprefix("Required[Literal[").removesuffix("]]") + "]"))
    spec = generate.Spec(SPEC)
    assert named == every - set(spec.fhir) - set(spec.carried)


def test_the_components_are_the_specs_own() -> None:
    classes = _classes("src/haau3/_components.py")
    for name in generate.Spec(SPEC).own:
        schema = SPEC["components"]["schemas"][name]
        declared = {
            s.target.id: not ast.unparse(s.annotation).startswith("NotRequired[")
            for s in classes[name].body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        }
        assert set(declared) == set(schema["properties"]), name
        assert {k for k, required in declared.items() if required} == set(schema.get("required", [])), name


def test_the_operations_a_batch_refuses_come_from_the_spec() -> None:
    spec = generate.Spec(SPEC)
    marked = sorted(
        o["operationId"] for item in SPEC["paths"].values() for o in item.values() if o.get(generate.BATCHABLE) is False
    )
    assert "reconcileMedications" in marked
    assert spec.not_in_a_batch() == ["fhirBatch", *marked]
    assert not any(op.batchable for op in spec.ops if op.id in marked)


def test_a_batchable_mark_other_than_false_stops_the_generator() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["paths"]["/v1/fhir/r4/metadata"]["get"][generate.BATCHABLE] = True
    with pytest.raises(ValueError, match="defines only false"):
        generate.Spec(spec)


def test_a_batchable_mark_outside_the_fhir_base_stops_the_generator() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["paths"]["/v1/scheduling/publishers"]["get"][generate.BATCHABLE] = False
    with pytest.raises(ValueError, match="not on the FHIR base"):
        generate.Spec(spec)


def _mini(parameters: list[dict[str, object]]) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["paths"]["/v1/scheduling/publishers"]["get"]["parameters"] = parameters
    return spec


@pytest.mark.parametrize(
    "parameters",
    [
        [
            {"name": "_count", "in": "query", "schema": {"type": "integer"}},
            {"name": "count", "in": "query", "schema": {"type": "integer"}},
        ],
        [{"name": "timeout", "in": "query", "schema": {"type": "integer"}}],
        [{"name": "class", "in": "query", "schema": {"type": "string"}}],
        [{"name": "weird", "in": "query", "schema": {"type": "object"}}],
    ],
)
def test_the_generator_refuses_a_parameter_it_cannot_name_or_type(parameters: list[dict[str, object]]) -> None:
    with pytest.raises(ValueError):
        generate.Spec(_mini(parameters))


def _header(**changes: object) -> dict[str, object]:
    return {"name": "X-Example-Option", "in": "header", "schema": {"type": "string"}, **changes}


def test_an_optional_header_is_a_keyword_of_the_operations_own() -> None:
    op = next(
        o
        for o in generate.Spec(_mini([_header(schema={"type": "string", "maxLength": 9})])).ops
        if o.id == "listPublishers"
    )
    assert [(h.wire, h.python, h.max_length) for h in op.headers] == [("X-Example-Option", "x_example_option", 9)]


@pytest.mark.parametrize(
    ("parameters", "words"),
    [
        ([_header(required=True)], "required header"),
        ([{"name": "session", "in": "cookie", "schema": {"type": "string"}}], "in cookie"),
        ([_header(name="Timeout")], "clash"),
        ([_header(name="Name"), {"name": "name", "in": "query", "schema": {"type": "string"}}], "clash"),
        ([_header(schema={"type": "integer"})], "not a string"),
        ([_header(schema={"type": "string", "pattern": "(unclosed"})], "missing"),
    ],
    ids=["required", "a cookie", "an option's name", "a query parameter's name", "not a string", "a bad pattern"],
)
def test_the_generator_stops_on_a_header_it_cannot_send(parameters: list[dict[str, object]], words: str) -> None:
    with pytest.raises(Exception, match=words):
        generate.Spec(_mini(parameters))


def test_a_header_on_an_operation_a_batch_can_carry_stops_the_generator() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["paths"]["/v1/fhir/r4/metadata"]["get"].setdefault("parameters", []).append(_header())
    with pytest.raises(ValueError, match="whose entries carry no headers"):
        generate.Spec(spec)


def test_the_generator_refuses_a_spec_without_the_batch() -> None:
    spec = json.loads(json.dumps(SPEC))
    del spec["paths"]["/v1/fhir/r4"]
    with pytest.raises(ValueError, match="no batch at POST /v1/fhir/r4"):
        generate.Spec(spec)
