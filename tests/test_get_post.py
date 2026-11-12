"""get() and post(): any operation by its path, judged as every generated method's answer is.

Each value is encoded into the path, a message names the path as written and never a value, and a
path that does not match its values is refused before anything is sent. Both clients, and both
clients' `with_response`.
"""

from __future__ import annotations

import json
import pickle
import re
from typing import Any

import pytest

from haau3 import AuthError, ForbiddenError, Haau3Error, NoAnswerError, RefusedError

from .helpers import KEY, REQUEST_ID, Client, Recorder, answer

PRACTITIONER = "/v1/fhir/r4/Practitioner/{id}"
VALUE = "VALUE-NEVER-IN-A-MESSAGE"
"""A value a message must never repeat: what a caller put in the path."""


def outcome(code: str, api_code: str | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {"text": "The API's own words."}
    if api_code is not None:
        details["coding"] = [{"system": "https://haau3.com/fhir/CodeSystem/api-error", "code": api_code}]
    return {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "details": details}]}


def printed(error: BaseException) -> str:
    """Every way an error commonly reaches a log line."""
    as_dict: object = error.to_dict() if isinstance(error, Haau3Error) else {}
    return "\n".join([str(error), repr(error), json.dumps(as_dict, default=str), repr(pickle.dumps(error))])


# --- get() ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_sends_the_path_with_each_value_encoded_the_query_and_the_key(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Practitioner"}))
    client = Client(kind, recorder, max_retries=0)
    got = await client("get", PRACTITIONER, path_params={"id": "a b/c"}, query={"_format": "json", "npi": ["1", "2"]})
    assert got == {"resourceType": "Practitioner"}
    sent = recorder.last
    assert sent.method == "GET"
    assert sent.url.raw_path.split(b"?")[0] == b"/v1/fhir/r4/Practitioner/a%20b%2Fc"
    assert recorder.query() == [("_format", "json"), ("npi", "1"), ("npi", "2")]
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert sent.headers["accept"] == "application/fhir+json"
    assert sent.content == b""


@pytest.mark.anyio
async def test_get_takes_a_path_with_no_name_and_json_outside_the_fhir_base(kind: str) -> None:
    recorder = Recorder(answer(200, {"publishers": []}, fhir=False))
    assert await Client(kind, recorder)("get", "/v1/scheduling/publishers") == {"publishers": []}
    assert recorder.last.headers["accept"] == "application/json"


@pytest.mark.anyio
async def test_with_response_gives_the_request_id_status_and_timing(kind: str) -> None:
    client = Client(kind, Recorder(answer(200, {"resourceType": "CapabilityStatement"})))
    response = await client.with_response("get", "/v1/fhir/r4/metadata", expects=["CapabilityStatement"])
    assert response.data == {"resourceType": "CapabilityStatement"}
    assert response.request_id == REQUEST_ID
    assert response.status == 200
    assert response.attempts == 1


@pytest.mark.anyio
async def test_get_passes_every_per_call_option_on(kind: str) -> None:
    correlation = "0b6f2d4e-8c1a-4e3b-9f5d-2a7c6e1b4d90"
    recorder = Recorder(answer(503, outcome("transient")))
    client = Client(kind, recorder)
    await client.raises(
        "get", "/v1/fhir/r4/metadata", correlation_id=correlation, extra_headers={"x-extra": "1"}, max_retries=0
    )
    assert len(recorder.requests) == 1
    assert recorder.last.headers["x-request-id"] == correlation
    assert recorder.last.headers["x-extra"] == "1"


@pytest.mark.anyio
async def test_get_sends_no_key_with_none_in_the_sandbox(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "CapabilityStatement"}))
    await Client(kind, recorder, api_key="", environment="sandbox")("get", "/v1/fhir/r4/metadata")
    assert recorder.last.url.host == "sandbox.haau3.com"
    assert "authorization" not in recorder.last.headers


# --- post() --------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_sends_the_body_as_json_with_fhir_media_type(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Parameters"}))
    body = {"resourceType": "Parameters", "parameter": []}
    got = await Client(kind, recorder)("post", "/v1/fhir/r4/$structure-dosage", body=body, expects=["Parameters"])
    assert got == {"resourceType": "Parameters"}
    assert recorder.last.method == "POST"
    assert recorder.last.url.path == "/v1/fhir/r4/$structure-dosage"
    assert recorder.last.headers["content-type"] == "application/fhir+json"
    assert json.loads(recorder.last.content) == body


@pytest.mark.anyio
async def test_post_sends_a_str_body_as_it_is(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Bundle"}))
    await Client(kind, recorder)("post", "/v1/fhir/r4", body='{"resourceType":"Bundle"}')
    assert recorder.last.content == b'{"resourceType":"Bundle"}'


@pytest.mark.anyio
async def test_with_response_post(kind: str) -> None:
    client = Client(kind, Recorder(answer(200, {"resourceType": "Parameters"})))
    response = await client.with_response("post", "/v1/fhir/r4/$structure-dosage", body={}, expects=["Parameters"])
    assert response.data == {"resourceType": "Parameters"}
    assert response.request_id == REQUEST_ID


# --- the errors every operation's own method raises --------------------------------------------


@pytest.mark.anyio
async def test_401_is_an_auth_error(kind: str) -> None:
    error = await Client(kind, Recorder(answer(401, outcome("login"))), max_retries=0).raises(
        "get", "/v1/fhir/r4/metadata"
    )
    assert isinstance(error, AuthError)
    assert error.status == 401
    assert error.request_id == REQUEST_ID


@pytest.mark.anyio
async def test_403_carries_the_code_and_not_open_to_customers_says_so(kind: str) -> None:
    client = Client(kind, Recorder(answer(403, outcome("forbidden", "not-open-to-customers"))), max_retries=0)
    error = await client.raises("post", "/v1/fhir/r4/$process-message", body={})
    assert isinstance(error, ForbiddenError)
    assert error.code == "not-open-to-customers"
    assert (
        str(error) == "POST /v1/fhir/r4/$process-message was refused: the operation is not yet open to customers (403)."
    )


@pytest.mark.anyio
async def test_a_403_without_a_code_keeps_the_general_sentence(kind: str) -> None:
    error = await Client(kind, Recorder(answer(403, outcome("forbidden"))), max_retries=0).raises(
        "get", "/v1/fhir/r4/metadata"
    )
    assert isinstance(error, ForbiddenError)
    assert error.code is None
    assert str(error).endswith("its organization may not make this request (403).")


@pytest.mark.anyio
async def test_another_4xx_is_a_refused_error(kind: str) -> None:
    error = await Client(kind, Recorder(answer(404, outcome("not-found"))), max_retries=0).raises(
        "get", PRACTITIONER, path_params={"id": "x"}
    )
    assert isinstance(error, RefusedError)
    assert error.status == 404


@pytest.mark.anyio
async def test_429_is_rate_limited_with_retry_after(kind: str) -> None:
    client = Client(kind, Recorder(answer(429, outcome("throttled"), headers={"retry-after": "12"})), max_retries=0)
    error = await client.raises("get", "/v1/fhir/r4/metadata")
    assert isinstance(error, NoAnswerError)
    assert error.reason == "rate-limited"
    assert error.retry_after == 12


@pytest.mark.anyio
async def test_503_is_unavailable(kind: str) -> None:
    error = await Client(kind, Recorder(answer(503, outcome("transient"))), max_retries=0).raises(
        "get", "/v1/fhir/r4/metadata"
    )
    assert isinstance(error, NoAnswerError)
    assert error.reason == "unavailable"


@pytest.mark.anyio
async def test_an_answer_of_another_type_than_expects_names_is_unreadable(kind: str) -> None:
    client = Client(kind, Recorder(answer(200, {"resourceType": "OperationOutcome"})))
    error = await client.raises("get", PRACTITIONER, path_params={"id": "x"}, expects=["Practitioner"])
    assert isinstance(error, NoAnswerError)
    assert error.reason == "unreadable"


@pytest.mark.anyio
async def test_without_expects_any_answer_is_returned(kind: str) -> None:
    client = Client(kind, Recorder(answer(200, {"resourceType": "OperationOutcome"})))
    assert await client("get", PRACTITIONER, path_params={"id": "x"}) == {"resourceType": "OperationOutcome"}


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_a_message_names_the_path_as_written_never_a_value_or_the_key(kind: str, status: int) -> None:
    client = Client(kind, Recorder(answer(status, outcome("x"))), max_retries=0)
    error = await client.raises("get", PRACTITIONER, path_params={"id": VALUE})
    assert PRACTITIONER in str(error)
    assert VALUE not in printed(error)
    assert KEY not in printed(error)


# --- a path that does not match its values -----------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "path_params", "message"),
    [
        (PRACTITIONER, {}, r"path_params has no value for \{id\}"),
        (PRACTITIONER, None, r"path_params has no value for \{id\}"),
        ("/v1/fhir/r4/metadata", {"id": VALUE}, r"the path has no \{id\} for path_params' value"),
        (PRACTITIONER, {"id": 7}, r"the value for \{id\} in path_params must be a str"),
    ],
)
async def test_a_path_that_does_not_match_its_values_is_refused_before_sending(
    kind: str, path: str, path_params: dict[str, Any] | None, message: str
) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Practitioner"}))
    error = await Client(kind, recorder).raises("get", path, path_params=path_params)
    assert isinstance(error, Haau3Error)
    assert not isinstance(error, (AuthError, ForbiddenError, RefusedError, NoAnswerError))
    assert re.search(message, str(error))
    assert VALUE not in printed(error)
    assert recorder.requests == []


@pytest.mark.anyio
async def test_expects_as_a_str_is_refused(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Bundle"}))
    error = await Client(kind, recorder).raises("get", "/v1/fhir/r4/metadata", expects="Bundle")
    assert isinstance(error, Haau3Error)
    assert "expects is a list" in str(error)
    assert recorder.requests == []


@pytest.mark.anyio
async def test_a_whole_url_on_another_host_is_refused_before_sending(kind: str) -> None:
    recorder = Recorder(answer(200, {}))
    error = await Client(kind, recorder).raises("get", "https://elsewhere.example/v1/fhir/r4/metadata")
    assert isinstance(error, Haau3Error)
    assert recorder.requests == []


# --- a path value never changes the path -------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["", ".", ".."])
async def test_a_value_that_would_change_the_path_is_refused_everywhere(kind: str, value: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "Practitioner"}))
    client = Client(kind, recorder)
    for error in [
        await client.raises("get", PRACTITIONER, path_params={"id": value}),
        await client.raises("read_practitioner", value),
    ]:
        assert isinstance(error, Haau3Error)
        assert re.search(
            r'the value for \{id\} is empty, "\." or "\.\.", which would send the request to another path\.', str(error)
        )
    assert recorder.requests == []


@pytest.mark.anyio
async def test_dots_inside_a_value_twice_written_names_and_dashed_names(kind: str) -> None:
    recorder = Recorder(answer(200, {}, fhir=False))
    client = Client(kind, recorder)
    await client("get", PRACTITIONER, path_params={"id": "a..b"})
    await client("get", "/v1/a/{id}/b/{id}", path_params={"id": "x"})
    await client("get", "/v1/a/{my-id}", path_params={"my-id": "y"})
    assert [r.url.raw_path.decode() for r in recorder.requests] == [
        "/v1/fhir/r4/Practitioner/a..b",
        "/v1/a/x/b/x",
        "/v1/a/y",
    ]
