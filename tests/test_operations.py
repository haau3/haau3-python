"""Every operation of the pinned spec sends its own example request and returns its example answer."""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import naming
import pytest

import haau3
from haau3 import AsyncHaau3, Haau3, calls

from .helpers import KEY, REQUEST_ID, SPEC, Client, Recorder, answer, example_answer, example_body, operations

OPERATIONS = sorted(operations())
NOT_IN_A_BATCH = {"fhirBatch", "reconcileMedications"}


def _wire(value: Any) -> str:
    # As JavaScript's String() writes a query value, which is what both SDKs send.
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _case(op: dict[str, Any]) -> tuple[list[str], dict[str, Any], list[tuple[str, str]]]:
    params = [p for p in op.get("parameters", []) if "$ref" not in p]
    path_values = [str(p["example"]) for p in params if p["in"] == "path"]
    kwargs: dict[str, Any] = {}
    sent: list[tuple[str, str]] = []
    for p in params:
        if p["in"] == "query" and "example" in p:
            kwargs[naming.python_name(p["name"])] = p["example"]
            values = p["example"] if isinstance(p["example"], list) else [p["example"]]
            sent += [(p["name"], _wire(v)) for v in values]
    return path_values, kwargs, sent


@pytest.mark.anyio
@pytest.mark.parametrize(("operation_id", "method", "path", "op"), OPERATIONS, ids=[o[0] for o in OPERATIONS])
async def test_each_operation_sends_its_example_and_returns_its_answer(
    kind: str, operation_id: str, method: str, path: str, op: dict[str, Any]
) -> None:
    expected = example_answer(op)
    fhir = path.startswith("/v1/fhir/")
    recorder = Recorder(answer(200, expected, fhir=fhir))
    client = Client(kind, recorder)
    path_values, kwargs, sent = _case(op)
    body = [example_body(op)] if "requestBody" in op else []

    result = await client(naming.method_name(operation_id), *path_values, *body, **kwargs)
    assert result == expected
    assert len(recorder.requests) == 1
    request = recorder.last
    assert request.method == method
    expected_path = path
    for value in path_values:
        expected_path = re.sub(r"\{\w+\}", value, expected_path, count=1)
    assert request.url.raw_path.decode().split("?", 1)[0] == expected_path
    assert sorted(recorder.query()) == sorted(sent)
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["user-agent"].startswith(f"haau3-python/{haau3.__version__} (python ")
    assert request.headers["accept"] == ("application/fhir+json" if fhir else "application/json")
    if body:
        assert request.headers["content-type"] == "application/fhir+json"
        assert json.loads(request.content) == body[0]
    else:
        assert "content-type" not in request.headers
        assert request.content == b""

    response = await client.with_response(naming.method_name(operation_id), *path_values, *body, **kwargs)
    assert response.data == expected
    assert response.status == 200
    assert response.request_id == REQUEST_ID
    assert response.attempts == 1


def _public(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_the_clients_have_one_method_per_operation_and_nothing_else(cls: type) -> None:
    methods = {naming.method_name(o[0]) for o in OPERATIONS}
    assert len(methods) == len(OPERATIONS) == 36
    assert _public(cls) == methods | {
        "batch",
        "pages",
        "resources",
        "request",
        "close",
        "base_url",
        "timeout",
        "max_retries",
    }


@pytest.mark.parametrize("cls", [haau3.SyncWithResponse, haau3.AsyncWithResponse])
def test_with_response_has_every_method_and_the_batch(cls: type) -> None:
    assert _public(cls) == {naming.method_name(o[0]) for o in OPERATIONS} | {"batch"}


def test_async_methods_are_coroutines_and_sync_ones_are_not() -> None:
    for operation_id, *_ in OPERATIONS:
        name = naming.method_name(operation_id)
        assert inspect.iscoroutinefunction(getattr(AsyncHaau3, name))
        assert not inspect.iscoroutinefunction(getattr(Haau3, name))


def test_calls_offers_every_batchable_operation_and_nothing_else() -> None:
    batchable = {
        naming.method_name(o[0]) for o in OPERATIONS if o[2].startswith("/v1/fhir/r4/") and o[0] not in NOT_IN_A_BATCH
    }
    assert len(batchable) == 29
    assert set(calls.__all__) == batchable
    assert {
        n
        for n in dir(calls)
        if not n.startswith("_")
        and inspect.isfunction(getattr(calls, n))
        and getattr(calls, n).__module__ == "haau3.calls"
    } == batchable


def test_the_spec_is_a_preview_version_with_get_and_post_only() -> None:
    assert re.fullmatch(r"1\.0\.0(-preview\.\d+)?", SPEC["info"]["version"])
    assert SPEC["info"]["version"] == haau3.SPEC_VERSION
    assert {o[1] for o in OPERATIONS} == {"GET", "POST"}


def test_a_required_query_parameter_has_no_default() -> None:
    signature = inspect.signature(Haau3.check_insurance_plan_network)
    assert signature.parameters["npi"].default is inspect.Parameter.empty
    assert signature.parameters["npi"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["id"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert inspect.signature(Haau3.search_slots).parameters["state"].default is None
