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
    every = {n for n, s in SPEC["components"]["schemas"].items() if generate.Spec.fhir_type(s)}
    assert set(spec.fhir) == every
    classes = _classes("src/haau3/fhir.py")
    for name in spec.fhir:
        assert _keys(classes[name])["resourceType"] is True


def test_a_component_is_a_fhir_root_when_its_resource_type_is_a_const() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    schemas = spec["components"]["schemas"]
    schemas["Device"] = {"type": "object", "properties": {"resourceType": {"const": "Device"}}}
    schemas["Tally"] = {"type": "object", "properties": {"total": {"type": "integer"}}}
    built = generate.Spec(spec)
    assert "Device" in built.fhir
    assert "Device" not in built.own
    # Control: a component with no resourceType is one of the spec's own.
    assert "Tally" in built.own
    assert "Tally" not in built.fhir
    # And one named for another resource type stops the generator.
    schemas["Gadget"] = {"type": "object", "properties": {"resourceType": {"const": "Device"}}}
    with pytest.raises(ValueError, match="named for another resource type"):
        generate.Spec(spec)


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


def _components_of(spec: dict[str, Any]) -> str:
    pytest.importorskip("datamodel_code_generator")
    return generate.components_module(generate.Spec(spec))


def test_the_components_are_the_specs_own() -> None:
    """Each of the spec's own components as a TypedDict with its keys, Required as the spec says.

    Held on a copy of the spec given one, since whether the pinned spec has any is up to the API."""
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["components"]["schemas"]["Tally"] = {
        "type": "object",
        "properties": {"total": {"type": "integer"}, "note": {"type": "string"}},
        "required": ["total"],
    }
    tree = ast.parse(_components_of(spec))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert "Tally" in classes
    for name in generate.Spec(spec).own:
        schema = spec["components"]["schemas"][name]
        declared = {
            s.target.id: not ast.unparse(s.annotation).startswith("NotRequired[")
            for s in classes[name].body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        }
        assert set(declared) == set(schema["properties"]), name
        assert {k for k, required in declared.items() if required} == set(schema.get("required", [])), name


def _without_own_components(spec: dict[str, Any]) -> dict[str, Any]:
    """A copy of the spec with none of its own components, and no operation answering with one."""
    copy: dict[str, Any] = json.loads(json.dumps(spec))
    own = set(generate.Spec(copy).own)
    copy["components"]["schemas"] = {k: v for k, v in copy["components"]["schemas"].items() if k not in own}
    for path in list(copy["paths"]):
        if any(f'/schemas/{name}"' in json.dumps(copy["paths"][path]) for name in own):
            del copy["paths"][path]
    return copy


def test_a_spec_with_no_components_of_its_own_generates_modules_that_import_none() -> None:
    spec = generate.Spec(_without_own_components(SPEC))
    assert spec.own == []
    components = _components_of(_without_own_components(SPEC))
    assert not [node for node in ast.parse(components).body if isinstance(node, ast.ClassDef)]
    for module in (generate.methods_module(spec), generate.operations_module(spec), generate.types_module(spec)):
        assert "_components" not in module
        compile(module, "generated.py", "exec")


def test_a_spec_with_a_component_of_its_own_imports_it_where_an_answer_is_one() -> None:
    """The control: an operation answering with one of the spec's own components imports it."""
    spec: dict[str, Any] = _without_own_components(SPEC)
    spec["components"]["schemas"]["Tally"] = {"type": "object", "properties": {"total": {"type": "integer"}}}
    spec["paths"]["/v1/tally"] = {
        "get": {
            "operationId": "getTally",
            "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Tally"}}}}},
        }
    }
    built = generate.Spec(spec)
    assert built.own == ["Tally"]
    assert "from ._components import Tally\n" in generate.methods_module(built)
    assert "from ._components import Tally\n" in generate.operations_module(built)
    assert "from ._components import Tally as Tally\n" in generate.types_module(built)


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
    spec["paths"]["/v1/example"] = {"get": _example_operation([])}
    spec["paths"]["/v1/example"]["get"][generate.BATCHABLE] = False
    with pytest.raises(ValueError, match="not on the FHIR base"):
        generate.Spec(spec)


def _sandbox_metadata_sample(security: object = None) -> str:
    """The Sandbox metadata's sample, from the pinned spec with the operation given `security`, or with
    no security of its own when that is None, so it takes the document's whichever spec is pinned."""
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    operation = spec["paths"]["/v1/sandbox/fhir/r4/metadata"]["get"]
    if security is None:
        operation.pop("security", None)
    else:
        operation["security"] = security
    built = generate.Spec(spec)
    return generate._sample(built, next(op for op in built.ops if op.id == "sandboxMetadata"))


def test_a_sample_reads_a_live_key_where_its_operation_takes_the_documents_security() -> None:
    assert "client = Haau3()  # reads HAAU3_API_KEY\n" in _sandbox_metadata_sample()


def test_a_sample_reads_a_test_key_where_its_operation_names_test_key_auth() -> None:
    assert "client = Haau3()  # reads HAAU3_API_KEY, a test key\n" in _sandbox_metadata_sample([{"testKeyAuth": []}])


@pytest.mark.parametrize(
    "security",
    [
        [{"apiKeyAuth": []}],
        [],
        [{"bearerAuth": []}, {"testKeyAuth": []}],
        [{"bearerAuth": [], "testKeyAuth": []}],
    ],
)
def test_a_security_the_samples_have_no_words_for_stops_the_generator(security: object) -> None:
    with pytest.raises(ValueError, match=r"^sandboxMetadata: security .* names no key a sample knows$"):
        _sandbox_metadata_sample(security)


def test_every_committed_sample_says_the_key_its_operations_security_names() -> None:
    overlay = json.loads((ROOT / "openapi" / "code-samples.json").read_text(encoding="utf-8"))
    spec = generate.Spec(SPEC)
    for op in spec.ops:
        method = op.method.lower()
        [action] = [a for a in overlay["actions"] if a["target"] == f"$.paths['{op.path}'].{method}"]
        source = action["update"]["x-codeSamples"][0]["source"]
        assert generate._client(spec, op, SPEC["paths"][op.path][method]) + "\n" in source, op.id


def _example_operation(parameters: list[dict[str, object]]) -> dict[str, Any]:
    """An operation of the test's own, off the FHIR base, so none of the spec's has to stand in."""
    return {
        "operationId": "getExample",
        "parameters": parameters,
        "responses": {
            "200": {"content": {"application/fhir+json": {"schema": {"$ref": "#/components/schemas/Bundle"}}}}
        },
    }


def _mini(parameters: list[dict[str, object]]) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    spec["paths"]["/v1/example"] = {"get": _example_operation(parameters)}
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
        if o.id == "getExample"
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


def test_the_generator_refuses_a_first_call_the_spec_no_longer_has(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generate, "FIRST_CALL", "searchNothing")
    with pytest.raises(ValueError, match="The first call is searchNothing, which the spec no longer has"):
        generate.code_samples_json(generate.Spec(SPEC))


def _search(spec: dict[str, Any], op_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        i["get"] for i in spec["paths"].values() if i.get("get", {}).get("operationId") == op_id
    )
    return found


def test_the_generator_refuses_a_search_helper_whose_search_is_gone() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    path = next(p for p, item in spec["paths"].items() if item.get("get", {}).get("operationId") == "searchSchedule")
    del spec["paths"][path]
    with pytest.raises(ValueError, match="The search helper of searchSchedule: the spec no longer has searchSchedule"):
        generate.Spec(spec)


def test_the_generator_refuses_a_search_helper_whose_search_takes_no_include() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    search = _search(spec, "searchSlot")
    search["parameters"] = [p for p in search["parameters"] if p.get("name") != "_include:iterate"]
    with pytest.raises(ValueError, match="The search helper of searchSlot: searchSlot takes no _include:iterate"):
        generate.Spec(spec)


def test_the_generator_refuses_a_search_helper_that_sends_an_include_the_search_does_not_serve() -> None:
    spec: dict[str, Any] = json.loads(json.dumps(SPEC))
    for p in _search(spec, "searchSchedule")["parameters"]:
        if p.get("name") == "_include:iterate":
            p["schema"]["items"]["enum"] = ["Schedule:actor", "PractitionerRole:practitioner"]
    with pytest.raises(
        ValueError,
        match="The search helper of searchSchedule sends _include:iterate=HealthcareService:location, which searchSchedule does not serve",
    ):
        generate.Spec(spec)
    # The control: the pinned spec serves every include each helper sends.
    assert set(generate.Spec(SPEC).helpers) == set(generate.WITH_INCLUDES)


def test_a_search_helpers_query_is_its_searchs_less_the_includes() -> None:
    spec = generate.Spec(SPEC)
    for op_id, helper in spec.helpers.items():
        search = next(o for o in spec.ops if o.id == op_id)
        assert [p.wire for p in helper.query] == [p.wire for p in search.query if p.wire not in generate.INCLUDE_PARAMS]
        assert helper.name == f"{search.name}_with_includes"


def test_a_list_parameter_takes_one_value_or_a_list() -> None:
    spec = generate.Spec(
        _mini(
            [
                {
                    "name": "_include",
                    "in": "query",
                    "schema": {"type": "array", "items": {"type": "string", "enum": ["A:b"]}},
                },
                {"name": "tag", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                {"name": "one", "in": "query", "schema": {"type": "string"}},
            ]
        )
    )
    op = next(o for o in spec.ops if o.id == "getExample")
    assert [p.type for p in op.query] == [
        "Literal['A:b'] | Sequence[Literal['A:b']]",
        "str | Sequence[str]",
        # Control: a parameter that is not a list takes one value only.
        "str",
    ]


def test_the_generator_refuses_a_spec_without_the_batch() -> None:
    spec = json.loads(json.dumps(SPEC))
    del spec["paths"]["/v1/fhir/r4"]
    with pytest.raises(ValueError, match="no batch at POST /v1/fhir/r4"):
        generate.Spec(spec)


# --- deprecation ---------------------------------------------------------------------------------

_IDS = {"oldThing", "newThing", "otherOldThing"}
_DEPRECATED = {"oldThing", "otherOldThing"}


def _op(**fields: object) -> dict[str, Any]:
    return {"operationId": "oldThing", "description": "Does a thing.", **fields}


def test_a_deprecated_operations_words_name_its_replacement_as_a_python_method() -> None:
    assert generate.deprecation(_op(), _IDS, _DEPRECATED) is None
    assert generate.deprecation(_op(deprecated=False), _IDS, _DEPRECATED) is None
    found = generate.deprecation(
        _op(deprecated=True, description="Does a thing. Deprecated: call `newThing`\ninstead."), _IDS, _DEPRECATED
    )
    assert found == ("Does a thing.", "Call `new_thing` instead.")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"deprecated": "yes"}, "where the spec defines only true or false"),
        ({"deprecated": None}, "where the spec defines only true or false"),
        ({"deprecated": True}, "needs exactly one passage"),
        ({"deprecated": True, "description": "Deprecated: a. Deprecated: `newThing`."}, "needs exactly one passage"),
        ({"deprecated": True, "description": "Deprecated: call `somethingElse`."}, "that is not deprecated"),
        ({"deprecated": True, "description": "Deprecated: call `oldThing`."}, "that is not deprecated"),
        ({"deprecated": True, "description": "Deprecated: call `otherOldThing`."}, "that is not deprecated"),
    ],
    ids=["a-string", "null", "no-passage", "two-passages", "unknown", "itself", "another-deprecated"],
)
def test_anything_else_stops_the_generator(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        generate.deprecation(_op(**fields), _IDS, _DEPRECATED)
