"""Several operations in one request: the Bundle sent, and each entry judged as the call alone is."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import naming
import pytest

from haau3 import AuthError, BatchError, BatchOk, Haau3Error, NoAnswerError, RefusedError, calls

from .helpers import REQUEST_ID, Client, Recorder, answer, example_body, operations, sync_client
from .test_operations import _case

PLAN = {"resourceType": "InsurancePlan", "id": "H1"}
OUTCOME = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found"}]}


def batch_response(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"resourceType": "Bundle", "type": "batch-response", "entry": list(entries)}


@pytest.mark.anyio
async def test_the_bundle_sent_and_the_results_in_order(kind: str) -> None:
    recorder = Recorder(
        answer(
            200,
            batch_response(
                {"resource": PLAN, "response": {"status": "200 OK"}},
                {"resource": {"resourceType": "Parameters"}, "response": {"status": "200"}},
                {"response": {"status": "404 Not Found", "outcome": OUTCOME}},
            ),
        )
    )
    results = await Client(kind, recorder)(
        "batch",
        calls.read_insurance_plan("H1"),
        calls.code_system_lookup(system="http://loinc.org", code="2345-7", property="CLASSTYPE"),
        calls.read_practitioner("a/b c"),
    )
    sent = json.loads(recorder.last.content)
    assert recorder.last.method == "POST" and recorder.last.url.path == "/v1/fhir/r4"
    assert recorder.last.headers["content-type"] == "application/fhir+json"
    assert sent == {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {"request": {"method": "GET", "url": "InsurancePlan/H1"}},
            {
                "request": {
                    "method": "GET",
                    "url": "CodeSystem/$lookup?code=2345-7&system=http%3A%2F%2Floinc.org&property=CLASSTYPE",
                }
            },
            {"request": {"method": "GET", "url": "Practitioner/a%2Fb%20c"}},
        ],
    }
    assert isinstance(results, tuple) and len(results) == 3
    first, second, third = results
    assert isinstance(first, BatchOk) and (first.data, first.status, first.ok) == (PLAN, 200, True)
    assert isinstance(second, BatchOk) and second.data == {"resourceType": "Parameters"}
    assert isinstance(third, BatchError) and third.ok is False
    assert isinstance(third.error, RefusedError)
    assert (third.error.status, third.error.outcome, third.error.request_id) == (404, OUTCOME, REQUEST_ID)
    assert third.error.message == "GET /v1/fhir/r4/Practitioner/{id} (entry 3 of 3) was refused (404)."


@pytest.mark.anyio
async def test_a_post_entry_carries_its_body_as_the_resource(kind: str) -> None:
    recorder = Recorder(
        answer(200, batch_response({"resource": {"resourceType": "Parameters"}, "response": {"status": "200"}}))
    )
    body = {"resourceType": "Parameters", "parameter": [{"name": "code", "valueCode": "2345-7"}]}
    await Client(kind, recorder)("batch", calls.code_system_lookup_post(body))  # type: ignore[arg-type,arg-type]
    assert json.loads(recorder.last.content)["entry"] == [
        {"request": {"method": "POST", "url": "CodeSystem/$lookup"}, "resource": body}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "kind_of", "reason"),
    [
        ("201 Created", PLAN, BatchOk, None),
        ("401", OUTCOME, RefusedError, None),
        ("403", None, RefusedError, None),
        ("410", None, RefusedError, None),
        ("429", None, NoAnswerError, "rate-limited"),
        ("500", None, NoAnswerError, "server-error"),
        ("503", None, NoAnswerError, "unavailable"),
        ("302", None, NoAnswerError, "unreadable"),
        ("200", None, NoAnswerError, "unreadable"),
        ("200", {"resourceType": "Bundle"}, NoAnswerError, "unreadable"),
    ],
)
async def test_each_entry_is_judged_as_the_call_alone_is(
    kind: str, status: str, body: Any, kind_of: type, reason: str | None
) -> None:
    entry: dict[str, Any] = {"response": {"status": status}}
    if body is not None:
        entry["resource"] = body
    recorder = Recorder(answer(200, batch_response(entry)))
    (result,) = await Client(kind, recorder)("batch", calls.read_insurance_plan("H1"))
    if kind_of is BatchOk:
        assert isinstance(result, BatchOk)
        return
    assert isinstance(result, BatchError) and isinstance(result.error, kind_of)
    assert not isinstance(result.error, AuthError)  # the key was accepted for the batch
    if reason:
        assert isinstance(result.error, NoAnswerError) and result.error.reason == reason
    assert len(recorder.requests) == 1  # an entry that fails is never retried


@pytest.mark.anyio
async def test_an_entry_with_no_status_is_unreadable(kind: str) -> None:
    recorder = Recorder(
        answer(200, batch_response({"resource": PLAN}, {"resource": PLAN, "response": {"status": "OK"}}))
    )
    first, second = await Client(kind, recorder)(
        "batch", calls.read_insurance_plan("A"), calls.read_insurance_plan("B")
    )
    for result, n in ((first, 1), (second, 2)):
        assert isinstance(result, BatchError) and isinstance(result.error, NoAnswerError)
        assert result.error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} (entry {n} of 2) answered with no status."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "said"),
    [
        (
            {"resourceType": "Bundle", "type": "searchset", "entry": []},
            "POST /v1/fhir/r4 answered a Bundle that is not a batch-response.",
        ),
        (batch_response(), "POST /v1/fhir/r4 answered 0 entries for a batch of 1."),
        (batch_response({}, {}), "POST /v1/fhir/r4 answered 2 entries for a batch of 1."),
        (
            {"resourceType": "Bundle", "type": "batch-response", "entry": {"not": "a list"}},
            "POST /v1/fhir/r4 answered a batch-response whose entries are not a list.",
        ),
    ],
)
async def test_an_answer_that_cannot_be_paired_is_no_answer_for_the_whole_batch(
    kind: str, body: Any, said: str
) -> None:
    error = await Client(kind, Recorder(answer(200, body))).raises("batch", calls.read_insurance_plan("H1"))
    assert isinstance(error, NoAnswerError)
    assert (error.message, error.reason, error.status) == (said, "unreadable", 200)


@pytest.mark.anyio
async def test_the_batch_itself_is_judged_and_retried_as_any_call(kind: str, waits: list[float]) -> None:
    assert isinstance(
        await Client(kind, Recorder(answer(401))).raises("batch", calls.read_insurance_plan("H1")), AuthError
    )
    assert isinstance(
        await Client(kind, Recorder(answer(413))).raises("batch", calls.read_insurance_plan("H1")), RefusedError
    )
    recorder = Recorder(answer(503), answer(200, batch_response({"resource": PLAN, "response": {"status": "200"}})))
    response = await Client(kind, recorder).with_response("batch", calls.read_insurance_plan("H1"))
    assert (response.attempts, response.request_id, len(recorder.requests)) == (2, REQUEST_ID, 2)
    assert isinstance(response.data[0], BatchOk)


@pytest.mark.anyio
async def test_an_empty_batch(kind: str) -> None:
    recorder = Recorder(answer(200, batch_response()))
    assert await Client(kind, recorder)("batch") == ()
    assert json.loads(recorder.last.content) == {"resourceType": "Bundle", "type": "batch", "entry": []}


@pytest.mark.anyio
async def test_an_entry_not_made_by_calls_is_refused_before_sending(kind: str) -> None:
    recorder = Recorder(answer(200, batch_response()))
    error = await Client(kind, recorder).raises("batch", calls.read_insurance_plan("H1"), {"method": "GET", "url": "x"})
    assert type(error) is Haau3Error
    assert error.args[0] == "Entry 2 of the batch was not made by haau3.calls."
    assert recorder.requests == []


def test_a_call_names_its_operation_never_a_value() -> None:
    call = calls.read_insurance_plan("secret-plan")
    assert repr(call) == "<Call GET /v1/fhir/r4/InsurancePlan/{id}>"
    assert (call.method, call.url) == ("GET", "InsurancePlan/secret-plan")


BATCHABLE = [
    o
    for o in sorted(operations())
    if o[2].startswith("/v1/fhir/r4/") and o[0] not in {"fhirBatch", "reconcileMedications"}
]


@pytest.mark.parametrize(("operation_id", "method", "path", "op"), BATCHABLE, ids=[o[0] for o in BATCHABLE])
def test_each_call_makes_the_entry_its_direct_call_sends(
    operation_id: str, method: str, path: str, op: dict[str, Any]
) -> None:
    path_values, kwargs, _ = _case(op)
    body = [example_body(op)] if "requestBody" in op else []
    recorder = Recorder(answer(200, {"resourceType": "x"}))
    # The answer does not matter here, only what was sent.
    with contextlib.suppress(NoAnswerError):
        getattr(sync_client(recorder), naming.method_name(operation_id))(*path_values, *body, **kwargs)
    direct = recorder.last.url.raw_path.decode().removeprefix("/v1/fhir/r4/")
    call = getattr(calls, naming.method_name(operation_id))(*path_values, *body, **kwargs)
    assert (call.method, call.url) == (method, direct)
