"""What each answer means: the three errors, their fields and their messages."""

from __future__ import annotations

import copy
import json
import pickle
from typing import Any

import httpx2
import pytest

from haau3 import AuthError, ForbiddenError, Haau3Error, NoAnswerError, RefusedError

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
    assert error.outcome == OUTCOME
    assert error.request_id == REQUEST_ID


@pytest.mark.anyio
async def test_401_says_the_key_was_not_accepted(kind: str) -> None:
    error = await Client(kind, Recorder(answer(401, OUTCOME))).raises("read_insurance_plan", "H1")
    assert isinstance(error, AuthError)
    assert not isinstance(error, ForbiddenError)
    assert error.message == "GET /v1/fhir/r4/InsurancePlan/{id} was refused: the API key was not accepted (401)."


@pytest.mark.anyio
async def test_403_is_a_forbidden_error_that_says_the_key_was_accepted(kind: str) -> None:
    error = await Client(kind, Recorder(answer(403, OUTCOME))).raises("read_insurance_plan", "H1")
    assert isinstance(error, ForbiddenError)
    assert error.message == (
        "GET /v1/fhir/r4/InsurancePlan/{id} was refused: "
        "the API key was accepted, and its organization may not make this request (403)."
    )
    assert "not accepted" not in error.message


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


# A 403 names its reason when the API names it by a code of its own: in an OperationOutcome's
# `api-error` coding on a FHIR base, or in a problem's `code` elsewhere. The message says it in words
# chosen by the code, never the body's.

API_ERROR = "https://haau3.com/fhir/CodeSystem/api-error"


def coded(code: str, system: str = API_ERROR) -> dict[str, object]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "forbidden",
                "details": {"coding": [{"system": system, "code": code}], "text": "the API's own words"},
            }
        ],
    }


@pytest.mark.anyio
async def test_a_403_names_a_test_key_outside_the_sandbox(kind: str) -> None:
    error = await Client(kind, Recorder(answer(403, coded("test-key-outside-sandbox")))).raises(
        "read_insurance_plan", "H1"
    )
    assert isinstance(error, ForbiddenError)
    assert isinstance(error, AuthError)
    assert error.code == "test-key-outside-sandbox"
    assert error.message == (
        "GET /v1/fhir/r4/InsurancePlan/{id} was refused: this is a test key, which works only in the Sandbox (403)."
    )
    assert "the API's own words" not in error.message
    assert error.to_dict()["code"] == "test-key-outside-sandbox"


@pytest.mark.anyio
async def test_a_403_names_a_live_key_in_the_sandbox(kind: str) -> None:
    bundle = {"resourceType": "Bundle", "type": "message", "entry": []}
    error = await Client(kind, Recorder(answer(403, coded("live-key-in-sandbox")))).raises(
        "sandbox_process_message", bundle
    )
    assert isinstance(error, ForbiddenError)
    assert error.code == "live-key-in-sandbox"
    assert error.message == (
        "POST /v1/sandbox/fhir/r4/$process-message was refused: "
        "this is a live key, which does not work in the Sandbox (403)."
    )


@pytest.mark.anyio
async def test_a_403_reads_its_code_from_a_problem_off_the_fhir_bases(kind: str) -> None:
    problem = {
        **PROBLEM,
        "status": 403,
        "type": "https://docs.haau3.com/errors#forbidden",
        "code": "test-key-outside-sandbox",
    }
    error = await Client(kind, Recorder(answer(403, problem, fhir=False))).raises("search_slots", state="MA")
    assert isinstance(error, ForbiddenError)
    assert error.code == "test-key-outside-sandbox"
    assert error.message == (
        "GET /v1/scheduling/slots was refused: this is a test key, which works only in the Sandbox (403)."
    )


@pytest.mark.anyio
async def test_a_403_carries_a_code_it_has_no_sentence_for_under_the_general_message(kind: str) -> None:
    error = await Client(kind, Recorder(answer(403, coded("phi-agreement-required")))).raises(
        "read_insurance_plan", "H1"
    )
    assert isinstance(error, ForbiddenError)
    assert error.code == "phi-agreement-required"
    assert error.message.endswith("the API key was accepted, and its organization may not make this request (403).")


# Negative controls: no code, and a code from another system, are not the API's reason.
@pytest.mark.anyio
@pytest.mark.parametrize("body", [OUTCOME, coded("test-key-outside-sandbox", "http://example.org/other")])
async def test_a_403_has_no_code_when_the_api_names_none(kind: str, body: dict[str, object]) -> None:
    error = await Client(kind, Recorder(answer(403, body))).raises("read_insurance_plan", "H1")
    assert isinstance(error, ForbiddenError)
    assert error.code is None
    assert error.message.endswith("the API key was accepted, and its organization may not make this request (403).")
    assert "code" not in error.to_dict()


# Every answer and every error the API answered carries its status's text: the answer's own, or the
# standard one when it came with none, as HTTP/2 answers do.


def with_phrase(status: int, body: object, phrase: bytes) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status,
            headers={"x-request-id": REQUEST_ID, "content-type": "application/fhir+json"},
            content=json.dumps(body).encode(),
            extensions={"reason_phrase": phrase},
        )

    return handler


@pytest.mark.anyio
async def test_a_typed_answer_carries_its_reason_phrase(kind: str) -> None:
    plan = {"resourceType": "InsurancePlan", "id": "H1"}
    standard = await Client(kind, Recorder(answer(200, plan))).with_response("read_insurance_plan", "H1")
    assert standard.reason_phrase == "OK"
    own = await Client(kind, Recorder(with_phrase(200, plan, b"Fine"))).with_response("read_insurance_plan", "H1")
    assert own.reason_phrase == "Fine"


@pytest.mark.anyio
async def test_each_error_the_api_answered_carries_its_reason_phrase(kind: str) -> None:
    auth = await Client(kind, Recorder(answer(401, OUTCOME))).raises("read_insurance_plan", "H1")
    forbidden = await Client(kind, Recorder(answer(403, OUTCOME))).raises("read_insurance_plan", "H1")
    refused = await Client(kind, Recorder(with_phrase(404, OUTCOME, b"Nothing Here"))).raises(
        "read_insurance_plan", "H1"
    )
    none = await Client(kind, Recorder(answer(503, OUTCOME)), max_retries=0).raises("read_insurance_plan", "H1")
    assert isinstance(auth, AuthError)
    assert isinstance(forbidden, ForbiddenError)
    assert isinstance(refused, RefusedError)
    assert isinstance(none, NoAnswerError)
    assert [auth.reason_phrase, forbidden.reason_phrase, refused.reason_phrase, none.reason_phrase] == [
        "Unauthorized",
        "Forbidden",
        "Nothing Here",
        "Service Unavailable",
    ]
    assert none.to_dict()["reason_phrase"] == "Service Unavailable"


# Negative control: a request that got no answer has no status, so no reason phrase either.
@pytest.mark.anyio
async def test_no_reason_phrase_when_no_answer_came(kind: str) -> None:
    error = await Client(kind, Recorder(httpx2.ConnectError("refused")), max_retries=0).raises(
        "read_insurance_plan", "H1"
    )
    assert isinstance(error, NoAnswerError)
    assert error.reason == "network"
    assert error.status is None
    assert error.reason_phrase is None
    assert "reason_phrase" not in error.to_dict()
