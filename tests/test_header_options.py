"""A header an operation declares is a keyword of the call's own: process_message's payer_authorization.

It is sent as Payer-Authorization, checked against the spec's schema before anything is sent, wins
over the same header in extra_headers, and is held where nothing prints it, a traceback's local
variables included.
"""

from __future__ import annotations

import pickle
import traceback
from typing import Any

import pytest

from haau3 import Haau3Error, RefusedError
from haau3._body import HeldHeaders

from .helpers import Client, Recorder, answer

MESSAGE: dict[str, Any] = {"resourceType": "Bundle", "type": "message", "entry": []}
ANSWER = {"resourceType": "Bundle", "type": "message", "entry": []}
# A token no error, message or printout may ever carry.
TOKEN = "eyJ0b2tlbi10aGF0LW11c3QtbmV2ZXItYmUtcHJpbnRlZA"


def printed(error: BaseException) -> str:
    """The error as anything would print it, with the local variables of every frame of the SDK's own.

    The caller's frames are left out: a value the caller holds in a variable of its own is the caller's
    to keep out of a log, and the test's helpers hold the keyword in theirs.
    """
    tb = error.__traceback__
    while tb is not None and "/src/haau3/" not in tb.tb_frame.f_code.co_filename:
        tb = tb.tb_next
    assert tb is not None, "the error was raised in no frame of the SDK's"
    sdk = traceback.TracebackException(type(error), error, tb, capture_locals=True)
    return "\n".join(["".join(sdk.format()), repr(error), str(error), repr(vars(error))])


@pytest.mark.anyio
async def test_it_is_sent_as_payer_authorization_beside_the_key(kind: str) -> None:
    recorder = Recorder(answer(200, ANSWER))
    await Client(kind, recorder)("process_message", MESSAGE, payer_authorization=f"Bearer {TOKEN}")
    assert recorder.last.headers["payer-authorization"] == f"Bearer {TOKEN}"
    assert recorder.last.headers["authorization"].startswith("Bearer haau3_sk_")


@pytest.mark.anyio
async def test_nothing_is_sent_when_it_is_not_given(kind: str) -> None:
    # The control: the header is the keyword's doing.
    recorder = Recorder(answer(200, ANSWER))
    client = Client(kind, recorder)
    await client("process_message", MESSAGE)
    await client("process_message", MESSAGE, timeout=5)
    assert len(recorder.requests) == 2
    assert all("payer-authorization" not in r.headers for r in recorder.requests)


@pytest.mark.anyio
async def test_any_case_of_the_scheme_is_taken_as_the_api_takes_it(kind: str) -> None:
    recorder = Recorder(answer(200, ANSWER))
    await Client(kind, recorder)("process_message", MESSAGE, payer_authorization=f"bearer  {TOKEN}")
    assert recorder.last.headers["payer-authorization"] == f"bearer  {TOKEN}"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("value", "words"),
    [
        (TOKEN, "is not in the form the API takes in Payer-Authorization"),
        (f"Basic {TOKEN}", "is not in the form the API takes in Payer-Authorization"),
        ("", "is not in the form the API takes in Payer-Authorization"),
        (f"Bearer {TOKEN}\n", "is not in the form the API takes in Payer-Authorization"),
        ("Bearer " + "a" * 4090, "is longer than the 4096 characters the API takes in Payer-Authorization"),
    ],
    ids=["a bare token", "another scheme", "an empty value", "a final line break", "4,097 characters"],
)
async def test_a_value_the_api_would_refuse_is_refused_before_sending(kind: str, value: str, words: str) -> None:
    recorder = Recorder(answer(200, ANSWER))
    error = await Client(kind, recorder).raises("process_message", MESSAGE, payer_authorization=value)
    assert isinstance(error, Haau3Error)
    assert recorder.requests == []
    assert str(error).startswith(f"POST /v1/fhir/r4/$process-message was not sent: payer_authorization {words}")
    if value:
        assert value.strip()[-20:] not in printed(error)
    assert TOKEN not in printed(error)
    # The control: the SDK's frames are printed with their locals, the held keyword among them.
    assert "declared = <headers hidden>" in printed(error)


@pytest.mark.anyio
async def test_4096_characters_the_apis_own_limit_are_taken(kind: str) -> None:
    recorder = Recorder(answer(200, ANSWER))
    at_limit = "Bearer " + "a" * 4089
    assert len(at_limit) == 4096
    await Client(kind, recorder)("process_message", MESSAGE, payer_authorization=at_limit)
    assert recorder.last.headers["payer-authorization"] == at_limit


@pytest.mark.anyio
async def test_it_wins_over_the_same_header_in_extra_headers(kind: str) -> None:
    recorder = Recorder(answer(200, ANSWER))
    await Client(kind, recorder)(
        "process_message",
        MESSAGE,
        payer_authorization=f"Bearer {TOKEN}",
        extra_headers={"PAYER-AUTHORIZATION": "Bearer from-extra-headers"},
    )
    assert recorder.last.headers.get_list("payer-authorization") == [f"Bearer {TOKEN}"]


@pytest.mark.anyio
async def test_it_is_sent_again_on_a_retry(kind: str) -> None:
    recorder = Recorder(answer(503, None), answer(200, ANSWER))
    await Client(kind, recorder)("process_message", MESSAGE, payer_authorization=f"Bearer {TOKEN}", max_retries=1)
    assert [r.headers["payer-authorization"] for r in recorder.requests] == [f"Bearer {TOKEN}"] * 2


@pytest.mark.anyio
async def test_no_error_the_api_answers_carries_it_however_it_is_printed(kind: str) -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "invalid"}]}
    recorder = Recorder(answer(400, outcome))
    error = await Client(kind, recorder).raises("process_message", MESSAGE, payer_authorization=f"Bearer {TOKEN}")
    assert isinstance(error, RefusedError)
    assert TOKEN not in printed(error)
    assert "declared = <headers hidden>" in printed(error)


def test_the_holder_never_prints_or_pickles_what_it_holds() -> None:
    held = HeldHeaders({"payer_authorization": f"Bearer {TOKEN}"})
    for text in (repr(held), str(held), f"{held}", f"{held!r:>40}"):
        assert TOKEN not in text
        assert "<headers hidden>" in text
    with pytest.raises(TypeError):
        pickle.dumps(held)


@pytest.mark.anyio
async def test_only_the_operation_that_declares_it_takes_the_keyword(kind: str) -> None:
    recorder = Recorder(answer(200, {"resourceType": "InsurancePlan", "id": "H1"}))
    with pytest.raises(TypeError):
        await Client(kind, recorder)("read_insurance_plan", "H1", payer_authorization=f"Bearer {TOKEN}")
    assert recorder.requests == []
