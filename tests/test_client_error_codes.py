"""Why a client could not be made, as a code: every refusal `Haau3()` and `AsyncHaau3()` raise before
anything is sent carries one, so a caller branches on the code and never on the message. The messages
are the ones the client gave before the codes, and refusals raised by a call carry none."""

from __future__ import annotations

import copy
import pickle
import re
import sys
from collections.abc import Callable
from typing import Any, get_args

import httpx2
import pytest

from haau3 import AsyncHaau3, ClientErrorCode, ForbiddenError, Haau3, Haau3Error

from .helpers import KEY, TEST_KEY, Client, Recorder, answer

OLDER_KEY = "haau3_sk_older_key_that_must_never_be_printed"
"""A key made before test and live keys: no mode in its prefix."""
SANDBOX = "https://sandbox.haau3.com"
MARKER = "key_that_must_never_be_printed"

Make = Callable[[type, pytest.MonkeyPatch], object]


def _set(mp: pytest.MonkeyPatch, name: str, value: str) -> pytest.MonkeyPatch:
    """Sets a variable for one case, inside the lambda that makes its client."""
    mp.setenv(name, value)
    return mp


# One construction per way a client can be refused: its code, and its message as it was before codes
# (a string is the whole message, a pattern part of it).
CASES: list[tuple[str, ClientErrorCode, Make, str | re.Pattern[str]]] = [
    (
        "no key, and the Sandbox not named",
        "no-api-key",
        lambda cls, mp: cls(),
        re.compile(r"^No API key\. Set HAAU3_API_KEY"),
    ),
    ("a key that is not a string", "invalid-api-key", lambda cls, mp: cls(api_key=42), "api_key takes a string."),
    (
        "quotes an env file kept around the key",
        "api-key-not-alone",
        lambda cls, mp: cls(api_key=f'"{KEY}"'),
        'The key in api_key has something before its haau3_sk_ prefix, such as quotes or "Bearer ". Set the key alone.',
    ),
    (
        "Bearer before the key, from the variable",
        "api-key-not-alone",
        lambda cls, mp: (_set(mp, "HAAU3_API_KEY", f"Bearer {KEY}"), cls())[1],
        re.compile(r"^The key in HAAU3_API_KEY "),
    ),
    (
        "a test key with production named",
        "test-key-outside-sandbox",
        lambda cls, mp: cls(api_key=TEST_KEY, environment="production"),
        "The key in api_key is a test key (haau3_sk_test_…), which works only in the Sandbox, and environment "
        "names production. Use a live key there, or name the Sandbox.",
    ),
    (
        "a test key with HAAU3_ENVIRONMENT naming production",
        "test-key-outside-sandbox",
        lambda cls, mp: (_set(mp, "HAAU3_ENVIRONMENT", "production"), cls(api_key=TEST_KEY))[1],
        re.compile(r"and HAAU3_ENVIRONMENT names production"),
    ),
    (
        "a live key with the Sandbox named",
        "live-key-in-sandbox",
        lambda cls, mp: cls(api_key=KEY, environment="sandbox"),
        re.compile(r"is a live key \(haau3_sk_live_…\)"),
    ),
    (
        "a live key at the Sandbox's address",
        "live-key-in-sandbox",
        lambda cls, mp: cls(api_key=KEY, base_url=SANDBOX),
        re.compile(r"which takes test keys only"),
    ),
    (
        "an older key with the Sandbox named",
        "older-key-in-sandbox",
        lambda cls, mp: cls(api_key=OLDER_KEY, environment="sandbox"),
        re.compile(r"was made before test and live keys"),
    ),
    (
        "an older key at the Sandbox's address",
        "older-key-in-sandbox",
        lambda cls, mp: cls(api_key=OLDER_KEY, base_url=SANDBOX),
        re.compile(r"which takes test keys only"),
    ),
    (
        "an environment that is neither",
        "invalid-environment",
        lambda cls, mp: cls(api_key=KEY, environment="staging"),
        'environment takes "production" or "sandbox", and this is neither.',
    ),
    (
        "HAAU3_ENVIRONMENT that is neither",
        "invalid-environment",
        lambda cls, mp: (_set(mp, "HAAU3_ENVIRONMENT", "staging"), cls(api_key=KEY))[1],
        re.compile(r"^HAAU3_ENVIRONMENT takes"),
    ),
    (
        "a base URL that is not http or https",
        "invalid-base-url",
        lambda cls, mp: cls(api_key=KEY, base_url="ftp://api.example.com"),
        re.compile(r"^base_url must be an http or https URL"),
    ),
    (
        "HAAU3_BASE_URL with no host",
        "invalid-base-url",
        lambda cls, mp: (_set(mp, "HAAU3_BASE_URL", "https://"), cls(api_key=KEY))[1],
        re.compile(r"^HAAU3_BASE_URL must be"),
    ),
    (
        "production named with the Sandbox's address",
        "environment-conflicts-with-base-url",
        lambda cls, mp: cls(api_key=KEY, environment="production", base_url=SANDBOX),
        "environment names production, and base_url is the Sandbox's address, https://sandbox.haau3.com. "
        "Name one environment, or a base URL that is neither's.",
    ),
    (
        "an app_info name with a space",
        "invalid-app-info",
        lambda cls, mp: cls(api_key=KEY, app_info={"name": "my plugin"}),
        re.compile(r'^app_info\["name"\] must be'),
    ),
    (
        "an app_info with an unknown field",
        "invalid-app-info",
        lambda cls, mp: cls(api_key=KEY, app_info={"name": "a", "partner": "b"}),
        re.compile(r"^app_info takes name, version and url only"),
    ),
    (
        "an http_client of the other kind",
        "invalid-http-client",
        lambda cls, mp: cls(api_key=KEY, http_client=httpx2.AsyncClient() if cls is Haau3 else httpx2.Client()),
        re.compile(r"^http_client is an httpx2\."),
    ),
    (
        "a client made in a browser",
        "in-browser",
        lambda cls, mp: (mp.setattr(sys, "platform", "emscripten"), cls(api_key=KEY))[1],
        re.compile(r"^haau3 runs on a server only"),
    ),
]


def _refused(cls: type, make: Make, monkeypatch: pytest.MonkeyPatch) -> Haau3Error:
    with pytest.raises(Haau3Error) as caught:
        make(cls, monkeypatch)
    return caught.value


def test_every_code_the_type_lists_has_a_case_and_every_case_is_one_of_them() -> None:
    assert {code for _, code, _, _ in CASES} == set(get_args(ClientErrorCode))


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
@pytest.mark.parametrize(("name", "code", "make", "message"), CASES, ids=[c[0] for c in CASES])
def test_a_client_that_cannot_be_made_is_refused_with_its_code(
    cls: type,
    name: str,
    code: ClientErrorCode,
    make: Make,
    message: str | re.Pattern[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _refused(cls, make, monkeypatch)
    assert type(error) is Haau3Error
    assert error.code == code
    if isinstance(message, str):
        assert error.message == message
    else:
        assert message.search(error.message), error.message


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
@pytest.mark.parametrize(("name", "code", "make", "message"), CASES, ids=[c[0] for c in CASES])
def test_the_code_is_where_a_log_reads_it_and_the_key_is_not(
    cls: type,
    name: str,
    code: ClientErrorCode,
    make: Make,
    message: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _refused(cls, make, monkeypatch)
    assert error.to_dict() == {"name": "Haau3Error", "message": error.message, "code": code}
    for again in (pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)):
        assert again.code == code
    for text in (str(error), repr(error), error.message, str(error.to_dict())):
        assert MARKER not in text


def test_a_refusal_made_before_anything_was_sent_has_no_request_fields() -> None:
    with pytest.raises(Haau3Error) as caught:
        Haau3(api_key=KEY, environment="sandbox")
    assert caught.value.request_id is None
    assert caught.value.correlation_id is None


# --- what keeps no code ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_refusal_raised_by_a_call_carries_no_code(kind: str) -> None:
    client = Client(kind, Recorder(answer(200, {"resourceType": "CapabilityStatement"})), api_key=KEY)
    with pytest.raises(Haau3Error) as caught:
        await client("fhir_metadata", correlation_id="not-a-uuid")
    assert caught.value.code is None
    assert "code" not in caught.value.to_dict()


def test_an_error_made_with_no_code_logs_none() -> None:
    error = Haau3Error("made by hand")
    assert error.code is None
    assert error.to_dict() == {"name": "Haau3Error", "message": "made by hand"}


@pytest.mark.anyio
async def test_a_403_still_carries_the_apis_own_code(kind: str) -> None:
    outcome = {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "forbidden",
                "details": {
                    "coding": [
                        {"system": "https://haau3.com/fhir/CodeSystem/api-error", "code": "phi-agreement-required"}
                    ]
                },
            }
        ],
    }
    client = Client(kind, Recorder(answer(403, outcome)), api_key=KEY)
    with pytest.raises(ForbiddenError) as caught:
        await client("fhir_metadata")
    assert caught.value.code == "phi-agreement-required"


def test_a_client_that_can_be_made_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    made: Any = Haau3(environment="sandbox")
    assert made.environment == "sandbox"
