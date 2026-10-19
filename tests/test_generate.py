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


def _keys(node: ast.ClassDef) -> dict[str, bool]:
    """Each key and whether it is Required."""
    return {
        s.target.id: ast.unparse(s.annotation).startswith("Required[")
        for s in node.body
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
    }


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
    assert named == every - set(generate.Spec(SPEC).fhir)


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


def test_both_operations_a_batch_refuses_are_in_the_spec() -> None:
    ids = {op.id for op in generate.Spec(SPEC).ops}
    assert set(generate.NOT_IN_A_BATCH) <= ids


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


def test_the_generator_refuses_a_spec_without_a_batch_exclusion() -> None:
    spec = json.loads(json.dumps(SPEC))
    del spec["paths"]["/v1/fhir/r4/$reconcile-medications"]
    with pytest.raises(ValueError, match="reconcileMedications is no longer in the spec"):
        generate.Spec(spec)
