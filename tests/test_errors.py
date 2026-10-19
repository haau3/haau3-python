"""What each answer means: the three errors, their fields and their messages."""

from __future__ import annotations

import copy
import pickle

import pytest

from haau3 import AuthError, Haau3Error, NoAnswerError, RefusedError

from .helpers import REQUEST_ID, Client, Recorder, answer

OUTCOME = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "not-found"}]}
PROBLEM = {
    "type": "https://docs.haau3.com/errors#invalid-request",
    "title": "Invalid request",
    "status": 400,
    "detail": "d",
    "instance": "urn:uuid:x",
}


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403])
async def test_401_and_403_are_auth_errors(kind: str, status: int) -> None:
    error = await Client(kind, Recorder(answer(status, OUTCOME))).raises("read_insurance_plan", "H1")
    assert isinstance(error, AuthError)
    assert error.status == status
    assert (
        error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} was refused: the API key was not accepted ({status})."
    )
    assert error.outcome == OUTCOME
    assert error.request_id == REQUEST_ID


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 404, 410, 413, 422])
async def test_other_4xx_are_refusals_carrying_the_outcome(kind: str, status: int) -> None:
    error = await Client(kind, Recorder(answer(status, OUTCOME))).raises("read_insurance_plan", "secret-id")
    assert isinstance(error, RefusedError)
    assert (error.status, error.attempts, error.outcome, error.problem) == (status, 1, OUTCOME, None)
    assert error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} was refused ({status})."
    assert "secret-id" not in error.message


@pytest.mark.anyio
async def test_scheduling_errors_carry_a_problem(kind: str) -> None:
    error = await Client(kind, Recorder(answer(400, PROBLEM, fhir=False))).raises("search_slots", state="ZZ")
    assert isinstance(error, RefusedError)
    assert (error.problem, error.outcome) == (PROBLEM, None)
    assert error.message == "GET /v1/scheduling/slots was refused (400)."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate-limited"),
        (500, "server-error"),
        (501, "server-error"),
        (502, "unavailable"),
        (503, "unavailable"),
        (504, "timeout"),
    ],
)
async def test_429_and_5xx_are_no_answer(kind: str, waits: list[float], status: int, reason: str) -> None:
    error = await Client(kind, Recorder(answer(status, OUTCOME))).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.status, error.attempts, error.outcome) == (reason, status, 2, OUTCOME)
    assert error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} got no answer ({reason}, {status}) after 2 attempts."


@pytest.mark.anyio
async def test_a_2xx_that_is_not_json_is_unreadable(kind: str) -> None:
    import httpx2

    error = await Client(
        kind, Recorder(httpx2.Response(200, text="<html>", headers={"content-type": "text/html"}))
    ).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.status) == ("unreadable", 200)
    assert error.message == "GET /v1/fhir/r4/InsurancePlan/{id} answered 200 with a body that is not JSON."
    broken = httpx2.Response(200, text="{not json", headers={"content-type": "application/fhir+json"})
    assert isinstance(await Client(kind, Recorder(broken)).raises("read_insurance_plan", "H1"), NoAnswerError)


@pytest.mark.anyio
async def test_an_empty_search_is_an_answer_not_an_error(kind: str) -> None:
    empty = {"resourceType": "Bundle", "type": "searchset", "total": 0, "entry": []}
    assert await Client(kind, Recorder(answer(200, empty)))("search_insurance_plan", coverage_area_state="NC") == empty
    assert await Client(kind, Recorder(answer(200, {"slots": [], "total": 0}, fhir=False)))("search_slots") == {
        "slots": [],
        "total": 0,
    }


def test_to_dict_gives_the_fields_that_are_set_in_order_and_never_a_body() -> None:
    from haau3._body import Hidden

    error = NoAnswerError(
        "m",
        reason="rate-limited",
        status=429,
        retry_after=30,
        attempts=2,
        duration_ms=1234,
        request_id="r",
        correlation_id="c",
        outcome=Hidden(OUTCOME),  # type: ignore[arg-type]
    )
    assert list(error.to_dict().items()) == [
        ("name", "NoAnswerError"),
        ("message", "m"),
        ("status", 429),
        ("reason", "rate-limited"),
        ("retry_after", 30),
        ("attempts", 2),
        ("duration_ms", 1234),
        ("request_id", "r"),
        ("correlation_id", "c"),
    ]
    assert Haau3Error("plain").to_dict() == {"name": "Haau3Error", "message": "plain"}
    assert (
        NoAnswerError("n", reason="network", attempts=1, detail="ConnectError: Connection refused").to_dict()["detail"]
        == "ConnectError: Connection refused"
    )


def test_an_error_pickles_and_copies_with_its_fields_and_without_its_body() -> None:
    from haau3._body import Hidden

    error = RefusedError("refused", status=404, attempts=1, duration_ms=5, request_id="r", outcome=Hidden(OUTCOME))  # type: ignore[arg-type]
    error.add_note("a note of yours") if hasattr(error, "add_note") else None
    for again in (pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)):
        assert type(again) is RefusedError
        assert (again.message, again.status, again.request_id, again.duration_ms) == ("refused", 404, "r", 5)
        assert again.outcome is None
    assert b"not-found" not in pickle.dumps(error)


def test_every_error_is_a_haau3_error_and_an_exception() -> None:
    for cls in (AuthError, RefusedError, NoAnswerError):
        assert issubclass(cls, Haau3Error)
    assert issubclass(Haau3Error, Exception)
