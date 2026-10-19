"""Your own request id: refused before sending unless a UUID, sent as X-Request-Id, read back on everything."""

from __future__ import annotations

import pytest

from haau3 import Haau3Error, NoAnswerError, RefusedError

from .helpers import Client, Recorder, answer

ID = "0b6f2d4e-8c1a-4e3b-9f5d-2a7c6e1b4d90"
BACK = {"x-correlation-id": ID}
PLAN = {"resourceType": "InsurancePlan", "id": "H1"}


@pytest.mark.anyio
async def test_it_is_sent_as_x_request_id_and_comes_back(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN, headers=BACK))
    response = await Client(kind, recorder).with_response("read_insurance_plan", "H1", correlation_id=ID)
    assert recorder.last.headers["x-request-id"] == ID
    assert response.correlation_id == ID


@pytest.mark.anyio
async def test_it_wins_over_a_raw_header(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    await Client(kind, recorder)("read_insurance_plan", "H1", correlation_id=ID, extra_headers={"X-Request-Id": "mine"})
    assert recorder.last.headers["x-request-id"] == ID


@pytest.mark.anyio
async def test_it_is_unset_when_not_sent(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    response = await Client(kind, recorder).with_response("read_insurance_plan", "H1")
    assert "x-request-id" not in recorder.last.headers
    assert response.correlation_id is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad",
    [
        "job-42",
        "",
        "0b6f2d4e8c1a4e3b9f5d2a7c6e1b4d90",
        ID + " ",
        ID + "\n",
        " " + ID,
        "0b6f2d4e-8c1a-4e3b-9f5d-2a7c6e1b4dZZ",
    ],
)
async def test_anything_but_a_uuid_is_refused_before_sending(kind: str, bad: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    error = await Client(kind, recorder).raises("read_insurance_plan", "H1", correlation_id=bad)
    assert type(error) is Haau3Error
    assert error.args[0] == (
        "GET /v1/fhir/r4/InsurancePlan/{id} was not sent: correlation_id must be a UUID, which is all the API keeps (it drops anything else)."
    )
    assert recorder.requests == []


@pytest.mark.anyio
async def test_an_upper_case_uuid_is_a_uuid(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    await Client(kind, recorder)("read_insurance_plan", "H1", correlation_id=ID.upper())
    assert recorder.last.headers["x-request-id"] == ID.upper()


@pytest.mark.anyio
async def test_it_comes_back_on_every_error_the_api_answered(kind: str, waits: list[float]) -> None:
    refused = await Client(kind, Recorder(answer(404, headers=BACK))).raises(
        "read_insurance_plan", "H1", correlation_id=ID
    )
    assert isinstance(refused, RefusedError) and refused.correlation_id == ID
    unanswered = await Client(kind, Recorder(answer(503, headers=BACK))).raises(
        "read_insurance_plan", "H1", correlation_id=ID
    )
    assert isinstance(unanswered, NoAnswerError) and unanswered.correlation_id == ID
    assert unanswered.to_dict()["correlation_id"] == ID


@pytest.mark.anyio
async def test_on_request_and_on_a_batch(kind: str) -> None:
    from haau3 import calls

    recorder = Recorder(answer(200, {"publishers": []}, headers=BACK, fhir=False))
    raw = await Client(kind, recorder)("request", "GET", "/v1/scheduling/publishers", correlation_id=ID)
    assert (raw.correlation_id, recorder.last.headers["x-request-id"]) == (ID, ID)
    batch = {"resourceType": "Bundle", "type": "batch-response", "entry": [{"response": {"status": "404"}}]}
    recorder = Recorder(answer(200, batch, headers=BACK))
    (result,) = await Client(kind, recorder)("batch", calls.read_insurance_plan("H1"), correlation_id=ID)
    assert isinstance(result.error, RefusedError) and result.error.correlation_id == ID
    assert recorder.last.headers["x-request-id"] == ID
