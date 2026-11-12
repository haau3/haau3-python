"""Which environment a client calls, and with which key: the key's mode picks the host, no key is allowed
only in the Sandbox, a mismatch is refused before anything is sent, and an explicit base URL still decides
where requests go."""

from __future__ import annotations

import pickle
from typing import Any

import httpx2
import pytest

from haau3 import AsyncHaau3, Haau3, Haau3Error

from .helpers import KEY, TEST_KEY, Client, Recorder, answer

OLDER_KEY = "haau3_sk_older_key_that_must_never_be_printed"
"""A key made before test and live keys: no mode in its prefix."""
PRODUCTION = "https://api.haau3.com"
SANDBOX = "https://sandbox.haau3.com"
CAPABILITY = {"resourceType": "CapabilityStatement"}
MESSAGE = {"resourceType": "Bundle", "type": "message"}


def _origin(request: httpx2.Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc.decode()}"


def _refused(cls: type, **options: Any) -> Haau3Error:
    with pytest.raises(Haau3Error) as caught:
        cls(**options)
    return caught.value


# --- the key's mode picks the host -------------------------------------------------------------


@pytest.mark.anyio
async def test_a_test_key_sends_every_call_to_the_sandbox_and_none_to_production(kind: str) -> None:
    recorder = Recorder(answer(200, CAPABILITY))
    client = Client(kind, recorder, api_key=TEST_KEY)
    assert client.inner.environment == "sandbox"
    assert client.inner.base_url == SANDBOX
    await client("fhir_metadata")
    await client("fhir_metadata")
    assert [_origin(r) for r in recorder.requests] == [SANDBOX, SANDBOX]
    assert recorder.last.headers["authorization"] == f"Bearer {TEST_KEY}"


@pytest.mark.anyio
@pytest.mark.parametrize("key", [KEY, OLDER_KEY])
async def test_a_live_key_and_one_made_before_modes_go_to_production(kind: str, key: str) -> None:
    recorder = Recorder(answer(200, CAPABILITY))
    client = Client(kind, recorder, api_key=key)
    assert client.inner.environment == "production"
    assert client.inner.base_url == PRODUCTION
    await client("fhir_metadata")
    assert _origin(recorder.last) == PRODUCTION


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_the_keys_mode_is_read_from_haau3_api_key_too(monkeypatch: pytest.MonkeyPatch, cls: type) -> None:
    monkeypatch.setenv("HAAU3_API_KEY", TEST_KEY)
    assert cls().base_url == SANDBOX


# --- no key -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_key_calls_the_sandbox_with_no_authorization_header_when_the_environment_names_it(kind: str) -> None:
    recorder = Recorder(answer(200, CAPABILITY))
    client = Client(kind, recorder, api_key=None, environment="sandbox")
    assert client.inner.environment == "sandbox"
    assert client.inner.base_url == SANDBOX
    await client("fhir_metadata")
    assert _origin(recorder.last) == SANDBOX
    assert "authorization" not in recorder.last.headers


@pytest.mark.anyio
async def test_haau3_environment_names_the_sandbox_and_an_empty_key_is_none(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAAU3_ENVIRONMENT", "sandbox")
    recorder = Recorder(answer(200, CAPABILITY))
    await Client(kind, recorder, api_key="")("fhir_metadata")
    assert _origin(recorder.last) == SANDBOX
    assert "authorization" not in recorder.last.headers


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
@pytest.mark.parametrize(
    "options",
    [{}, {"environment": "production"}, {"base_url": "http://localhost:3001"}],
    ids=["none", "production", "base_url"],
)
def test_no_key_is_refused_with_production_or_nothing_named(cls: type, options: dict[str, Any]) -> None:
    message = str(_refused(cls, **options))
    assert message.startswith("No API key. Set HAAU3_API_KEY, or pass it as api_key. ")
    assert "https://platform.haau3.com" in message
    assert 'pass environment="sandbox"' in message
    assert "HAAU3_ENVIRONMENT=sandbox" in message


# --- a key of the other mode --------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
@pytest.mark.parametrize(
    ("key", "environment", "begins"),
    [
        (
            TEST_KEY,
            "production",
            "The key in api_key is a test key (haau3_sk_test_…), which works only in the Sandbox, and environment"
            " names production. Use a live key there, or name the Sandbox.",
        ),
        (
            KEY,
            "sandbox",
            "The key in api_key is a live key (haau3_sk_live_…), which does not work in the Sandbox, and environment"
            " names the Sandbox. Use a test key there, or pass no key to call it with none.",
        ),
        (
            OLDER_KEY,
            "sandbox",
            "The key in api_key was made before test and live keys, so it does not work in the Sandbox",
        ),
    ],
    ids=["test-in-production", "live-in-sandbox", "older-in-sandbox"],
)
def test_a_key_of_the_other_mode_is_refused_before_any_request(
    cls: type, key: str, environment: str, begins: str
) -> None:
    error = _refused(cls, api_key=key, environment=environment)
    assert str(error).startswith(begins)
    for planted in (KEY, TEST_KEY, OLDER_KEY):
        assert planted not in str(error)
        assert planted not in repr(error)
        assert planted not in repr(error.to_dict())


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_the_error_names_haau3_api_key_and_says_to_unset_it(monkeypatch: pytest.MonkeyPatch, cls: type) -> None:
    monkeypatch.setenv("HAAU3_API_KEY", KEY)
    assert str(_refused(cls, environment="sandbox")) == (
        "The key in HAAU3_API_KEY is a live key (haau3_sk_live_…), which does not work in the Sandbox, and environment"
        " names the Sandbox. Use a test key there, or unset HAAU3_API_KEY to call it with no key."
    )
    monkeypatch.setenv("HAAU3_ENVIRONMENT", "sandbox")
    monkeypatch.delenv("HAAU3_API_KEY")
    assert "and HAAU3_ENVIRONMENT names the Sandbox" in str(_refused(cls, api_key=KEY))


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_the_environment_keyword_wins_over_haau3_environment(monkeypatch: pytest.MonkeyPatch, cls: type) -> None:
    monkeypatch.setenv("HAAU3_ENVIRONMENT", "sandbox")
    assert cls(api_key=KEY, environment="production").environment == "production"


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_a_key_of_the_same_mode_goes_with_its_environment(cls: type) -> None:
    """The control."""
    assert cls(api_key=TEST_KEY, environment="sandbox").base_url == SANDBOX
    assert cls(api_key=KEY, environment="production").base_url == PRODUCTION
    assert cls(api_key=OLDER_KEY, environment="production").base_url == PRODUCTION


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_an_environment_that_is_neither_is_refused_by_its_name_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, cls: type
) -> None:
    assert str(_refused(cls, api_key=KEY, environment="staging")) == (
        'environment takes "production" or "sandbox", and this is neither.'
    )
    monkeypatch.setenv("HAAU3_ENVIRONMENT", TEST_KEY)
    message = str(_refused(cls, api_key=KEY))
    assert message == 'HAAU3_ENVIRONMENT takes "production" or "sandbox", and this is neither.'
    assert TEST_KEY not in message


# --- an explicit base URL -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_explicit_base_url_decides_where_requests_go(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(answer(200, CAPABILITY))
    client = Client(kind, recorder, api_key=TEST_KEY, base_url="http://localhost:3001")
    assert client.inner.environment == "sandbox"
    await client("fhir_metadata")
    assert _origin(recorder.last) == "http://localhost:3001"
    monkeypatch.setenv("HAAU3_BASE_URL", "http://localhost:3002")
    assert Client(kind, recorder, api_key=TEST_KEY).inner.base_url == "http://localhost:3002"


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_an_explicit_base_url_keeps_the_environments_rules_for_the_key(cls: type) -> None:
    assert str(_refused(cls, api_key=KEY, environment="sandbox", base_url="http://localhost:3001")).startswith(
        "The key in api_key is a live key"
    )
    assert cls(environment="sandbox", base_url="http://localhost:3001").base_url == "http://localhost:3001"


# --- the first address's deprecated methods -----------------------------------------------------


@pytest.mark.anyio
async def test_the_deprecated_methods_call_the_same_operations_on_the_sandboxs_own_host(kind: str) -> None:
    recorder = Recorder(answer(200, MESSAGE))
    client = Client(kind, recorder, api_key=TEST_KEY)
    with pytest.warns(DeprecationWarning, match=r"^Call `process_message` on the Sandbox's own host"):
        await client("sandbox_process_message", MESSAGE, payer_authorization="Bearer abc")
    assert recorder.last.method == "POST"
    assert str(recorder.last.url) == f"{SANDBOX}/v1/fhir/r4/$process-message"
    assert recorder.last.headers["payer-authorization"] == "Bearer abc"
    metadata = Recorder(answer(200, CAPABILITY))
    with pytest.warns(DeprecationWarning, match=r"^Call `fhir_metadata` on the Sandbox's own host"):
        await Client(kind, metadata, api_key=TEST_KEY)("sandbox_metadata")
    assert str(metadata.last.url) == f"{SANDBOX}/v1/fhir/r4/metadata"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("options", "origin"),
    [
        ({"environment": "sandbox"}, SANDBOX),
        ({"environment": "sandbox", "base_url": "http://localhost:3001"}, "http://localhost:3001"),
    ],
    ids=["sandbox", "stand-in"],
)
async def test_the_deprecated_methods_do_the_same_with_no_key(kind: str, options: dict[str, str], origin: str) -> None:
    recorder = Recorder(answer(200, MESSAGE))
    with pytest.warns(DeprecationWarning, match=r"^Call `process_message` on the Sandbox's own host"):
        await Client(kind, recorder, api_key=None, **options)("sandbox_process_message", MESSAGE)
    assert str(recorder.last.url) == f"{origin}/v1/fhir/r4/$process-message"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options",
    [{"api_key": KEY}, {"api_key": OLDER_KEY}, {"api_key": TEST_KEY, "base_url": PRODUCTION}],
    ids=["live", "older", "test-key-with-a-base-url"],
)
async def test_the_deprecated_methods_keep_the_first_address_elsewhere(kind: str, options: dict[str, str]) -> None:
    recorder = Recorder(answer(200, MESSAGE))
    with pytest.warns(DeprecationWarning, match=r"^Call `process_message` on the Sandbox's own host"):
        await Client(kind, recorder, **options)("sandbox_process_message", MESSAGE)
    assert str(recorder.last.url) == f"{PRODUCTION}/v1/sandbox/fhir/r4/$process-message"


@pytest.mark.anyio
async def test_a_refusal_names_the_path_it_was_sent_to(kind: str) -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": []}
    client = Client(kind, Recorder(answer(400, outcome)), api_key=TEST_KEY)
    with pytest.warns(DeprecationWarning, match=r"^Call `process_message` on the Sandbox's own host"):
        error = await client.raises("sandbox_process_message", MESSAGE)
    assert str(error) == "POST /v1/fhir/r4/$process-message was refused (400)."


@pytest.mark.anyio
async def test_request_is_sent_as_it_is_given(kind: str) -> None:
    """The control: only the deprecated methods follow the client."""
    recorder = Recorder(answer(200, MESSAGE))
    await Client(kind, recorder, api_key=TEST_KEY)(
        "request", "POST", "/v1/sandbox/fhir/r4/$process-message", body=MESSAGE
    )
    assert str(recorder.last.url) == f"{SANDBOX}/v1/sandbox/fhir/r4/$process-message"


# --- a client with no key -----------------------------------------------------------------------


@pytest.mark.parametrize("cls", [Haau3, AsyncHaau3])
def test_a_client_with_no_key_prints_and_pickles_no_key(cls: type) -> None:
    client = cls(environment="sandbox")
    assert repr(client) == f"{cls.__name__}(base_url='{SANDBOX}')"
    assert client._engine.config.key is None  # pyright: ignore[reportPrivateUsage]
    assert b"haau3_sk_" not in pickle.dumps(client._engine.config.environment)  # pyright: ignore[reportPrivateUsage]
