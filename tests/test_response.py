"""What comes back: the answer checked for its resource type, and the request id and timing with it."""

from __future__ import annotations

import pytest

from haau3 import NoAnswerError, Response

from .helpers import REQUEST_ID, Client, Recorder, answer

PLAN = {"resourceType": "InsurancePlan", "id": "H1"}


@pytest.mark.anyio
async def test_with_response_gives_the_answer_and_what_came_with_it(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(503), answer(200, PLAN, headers={"x-extra": "1"}))
    response = await Client(kind, recorder).with_response("read_insurance_plan", "H1")
    assert isinstance(response, Response)
    assert (response.data, response.status, response.request_id, response.attempts) == (PLAN, 200, REQUEST_ID, 2)
    assert response.headers["x-extra"] == "1"
    assert isinstance(response.duration_ms, int) and response.duration_ms >= 0
    assert (
        repr(response)
        == f"Response(request_id='{REQUEST_ID}', correlation_id=None, duration_ms={response.duration_ms}, attempts=2, status=200, reason_phrase='OK')"
    )
    with pytest.raises(AttributeError):
        response.status = 1  # type: ignore[misc]


@pytest.mark.anyio
async def test_with_response_raises_as_the_method_does(kind: str) -> None:
    error = await Client(kind, Recorder(answer(200, {"resourceType": "Bundle"}))).raises("read_insurance_plan", "H1")
    client = Client(kind, Recorder(answer(200, {"resourceType": "Bundle"})))
    with pytest.raises(NoAnswerError) as raised:
        await client.with_response("read_insurance_plan", "H1")
    assert raised.value.message == error.args[0]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({"resourceType": "Bundle"}, "answered a Bundle where an InsurancePlan was expected."),
        ({"resourceType": "OperationOutcome"}, "answered an OperationOutcome where an InsurancePlan was expected."),
        (
            {"resourceType": "not a type; Jane Doe"},
            "answered something that is not a FHIR resource where an InsurancePlan was expected.",
        ),
        ({"id": "no type"}, "answered something that is not a FHIR resource where an InsurancePlan was expected."),
        (["a", "list"], "answered something that is not a FHIR resource where an InsurancePlan was expected."),
        (None, "answered 200 with a body that is not JSON."),
    ],
)
async def test_a_fhir_answer_of_another_type_is_unreadable(kind: str, body: object, said: str) -> None:
    error = await Client(kind, Recorder(answer(200, body))).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.status) == ("unreadable", 200)
    assert error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} {said}"


@pytest.mark.anyio
async def test_metadata_answers_either_of_its_two_types(kind: str) -> None:
    for type_ in ("CapabilityStatement", "TerminologyCapabilities"):
        assert await Client(kind, Recorder(answer(200, {"resourceType": type_})))("fhir_metadata") == {
            "resourceType": type_
        }
    error = await Client(kind, Recorder(answer(200, {"resourceType": "Bundle"}))).raises("fhir_metadata")
    assert (
        error.args[0]
        == "GET /v1/fhir/r4/metadata answered a Bundle where a CapabilityStatement or a TerminologyCapabilities was expected."
    )


@pytest.mark.anyio
async def test_scheduling_answers_are_not_checked_for_a_type(kind: str) -> None:
    assert await Client(kind, Recorder(answer(200, {"anything": 1}, fhir=False)))("list_publishers") == {"anything": 1}
    import httpx2

    json_null = httpx2.Response(200, content=b"null", headers={"content-type": "application/json"})
    assert await Client(kind, Recorder(json_null))("list_publishers") is None


@pytest.mark.anyio
async def test_request_returns_any_status_with_its_text(kind: str) -> None:
    recorder = Recorder(answer(404, {"type": "x", "status": 404}, fhir=False))
    raw = await Client(kind, recorder)(
        "request", "GET", "/v1/scheduling/publishers", query={"a": "1 2", "b": [True, 3]}
    )
    assert (raw.status, raw.reason_phrase, raw.body, raw.request_id, raw.attempts) == (
        404,
        "Not Found",
        {"type": "x", "status": 404},
        REQUEST_ID,
        1,
    )
    assert recorder.last.url.raw_path == b"/v1/scheduling/publishers?a=1+2&b=true&b=3"
    assert "body" not in repr(raw)
    import httpx2

    text = await Client(kind, Recorder(httpx2.Response(200, text="plain")))("request", "GET", "/v1/x")
    assert text.body == "plain"


@pytest.mark.anyio
async def test_request_sends_a_str_as_it_is_and_anything_else_as_json(kind: str) -> None:
    recorder = Recorder(answer(200, {}))
    client = Client(kind, recorder)
    await client("request", "POST", "/v1/fhir/r4/CodeSystem/$lookup", body='{"resourceType":"Parameters"}')
    assert recorder.last.content == b'{"resourceType":"Parameters"}'
    assert recorder.last.headers["content-type"] == "application/fhir+json"
    await client("request", "POST", "/v1/other", body={"é": [1, None]})
    assert recorder.last.content == '{"é":[1,null]}'.encode()
    assert recorder.last.headers["content-type"] == "application/json"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/v1/sandbox/fhir/r4/$process-message", "/v1/sandbox/fhir", "/v1/fhir/r4/$process-message", "/v1/fhir"]
)
async def test_a_call_to_either_fhir_base_asks_for_and_sends_fhir_json(kind: str, path: str) -> None:
    # The API reads a path equal to or under /v1/fhir or /v1/sandbox/fhir as FHIR, and nothing else.
    for base_url in ("https://api.haau3.com", "https://proxy.example.test/haau3/"):
        recorder = Recorder(answer(200, {"resourceType": "Bundle"}))
        await Client(kind, recorder, base_url=base_url)("request", "POST", path, body={"resourceType": "Bundle"})
        assert recorder.last.headers["accept"] == "application/fhir+json"
        assert recorder.last.headers["content-type"] == "application/fhir+json"


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/v1/sandbox", "/v1/sandbox/fhirish", "/v1/scheduling/slots"])
async def test_any_other_path_asks_for_and_sends_plain_json(kind: str, path: str) -> None:
    recorder = Recorder(answer(200, {}, fhir=False))
    await Client(kind, recorder)("request", "POST", path, body={})
    assert recorder.last.headers["accept"] == "application/json"
    assert recorder.last.headers["content-type"] == "application/json"
