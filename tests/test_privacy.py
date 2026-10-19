"""A body never shows where an error is printed or logged, and the key never shows anywhere.

An answer's body can repeat what the request carried, and for some operations that is a patient's
data. These tests plant a marker in every body and render each error every way it is commonly
printed, including a traceback with each frame's local variables (`traceback`'s `capture_locals`,
which is what error reporters that record locals do). The marker and the key never appear.

The marker lives at module level, never in a test's own local variable, since a test's frame is in
the traceback too; and each SDK call is made here, not through a helper that holds its arguments.
"""

from __future__ import annotations

import io
import json
import logging
import pickle
import pprint
import traceback
from typing import Any

import httpx2
import pytest

import haau3._body as body_module
from haau3 import AsyncHaau3, BatchError, Haau3, Haau3Error, NoAnswerError, calls

from .helpers import KEY, Recorder, answer, async_client, sync_client

MARKER = "PHI-MARKER-0c7e Jane Q Example MRN 4455667"


def outcome() -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": "invalid", "diagnostics": MARKER}],
    }


def problem() -> dict[str, Any]:
    return {
        "type": "https://docs.haau3.com/errors#invalid-request",
        "title": "t",
        "status": 400,
        "detail": MARKER,
        "instance": "urn:uuid:x",
    }


def records() -> dict[str, Any]:
    patient = {"resourceType": "MedicationStatement", "id": "m1", "note": [{"text": MARKER}]}
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": patient}]}


def rendered(error: BaseException) -> str:
    log = io.StringIO()
    logger = logging.getLogger("privacy-test")
    handler = logging.StreamHandler(log)
    logger.addHandler(handler)
    try:
        logger.error(
            "failed", exc_info=error, extra={"haau3": error.to_dict() if isinstance(error, Haau3Error) else None}
        )
    finally:
        logger.removeHandler(handler)
    return "\n".join(
        [
            "".join(traceback.TracebackException.from_exception(error, capture_locals=True).format()),
            "".join(traceback.format_exception(error)),
            repr(error),
            str(error),
            json.dumps(error.to_dict()) if isinstance(error, Haau3Error) else "",
            repr(vars(error)),
            pprint.pformat(vars(error)),
            log.getvalue(),
        ]
    )


def assert_hidden(error: BaseException) -> None:
    text = rendered(error)
    assert "PHI-MARKER" not in text
    assert KEY not in text


# --- each way a call fails, sync -----------------------------------------------------------------


def sync_cases() -> dict[str, Any]:
    def refused_401() -> None:
        sync_client(Recorder(answer(401, outcome()))).read_insurance_plan("H1")

    def refused_404() -> None:
        sync_client(Recorder(answer(404, outcome()))).read_insurance_plan("H1")

    def problem_400() -> None:
        sync_client(Recorder(answer(400, problem(), fhir=False))).search_slots(state="ZZ")

    def no_answer_503() -> None:
        sync_client(Recorder(answer(503, outcome())), max_retries=0).read_insurance_plan("H1")

    def no_answer_429_retried() -> None:
        sync_client(Recorder(answer(429, outcome(), headers={"retry-after": "0"}))).read_insurance_plan("H1")

    def answer_then_network() -> None:
        sync_client(
            Recorder(answer(503, outcome(), headers={"retry-after": "0"}), httpx2.ConnectError("refused"))
        ).read_insurance_plan("H1")

    def wrong_type() -> None:
        sync_client(
            Recorder(answer(200, {"resourceType": "Bundle", "entry": [{"resource": outcome()}]}))
        ).read_insurance_plan("H1")

    def request_body_refused() -> None:
        sync_client(Recorder(answer(400, outcome()))).reconcile_medications(records())  # type: ignore[arg-type]

    def request_body_no_answer() -> None:
        sync_client(Recorder(httpx2.ConnectError("refused")), max_retries=0).reconcile_medications(records())  # type: ignore[arg-type]

    def batch_unreadable() -> None:
        sync_client(
            Recorder(answer(200, {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": outcome()}]}))
        ).batch(calls.read_insurance_plan("H1"))

    def pages_unreadable() -> None:
        first = {
            "resourceType": "Bundle",
            "link": [{"relation": "next", "url": "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"}],
        }
        list(sync_client(Recorder(answer(200, outcome()))).pages(first))  # type: ignore[arg-type]

    return {
        f.__name__: f
        for f in (
            refused_401,
            refused_404,
            problem_400,
            no_answer_503,
            no_answer_429_retried,
            answer_then_network,
            wrong_type,
            request_body_refused,
            request_body_no_answer,
            batch_unreadable,
            pages_unreadable,
        )
    }


SYNC = sync_cases()


@pytest.mark.parametrize("case", sorted(SYNC))
def test_no_sync_error_shows_a_body(case: str, waits: list[float]) -> None:
    try:
        SYNC[case]()
    except Haau3Error as caught:
        error = caught
    else:
        raise AssertionError("no error")
    assert_hidden(error)


# --- and async ------------------------------------------------------------------------------------


async def async_case(case: str) -> None:
    def client(*answers: Any, **options: Any) -> AsyncHaau3:
        return async_client(Recorder(*answers), **options)

    if case == "refused_404":
        await client(answer(404, outcome())).read_insurance_plan("H1")
    elif case == "no_answer_503":
        await client(answer(503, outcome()), max_retries=0).read_insurance_plan("H1")
    elif case == "answer_then_network":
        await client(
            answer(503, outcome(), headers={"retry-after": "0"}), httpx2.ConnectError("refused")
        ).read_insurance_plan("H1")
    elif case == "wrong_type":
        await client(answer(200, {"resourceType": "Bundle", "entry": [{"resource": outcome()}]})).read_insurance_plan(
            "H1"
        )
    elif case == "request_body_refused":
        await client(answer(400, outcome())).reconcile_medications(records())  # type: ignore[arg-type]
    elif case == "pages_unreadable":
        first = {
            "resourceType": "Bundle",
            "link": [{"relation": "next", "url": "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"}],
        }
        async for _ in client(answer(200, outcome())).pages(first):  # type: ignore[arg-type]
            pass


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    ["refused_404", "no_answer_503", "answer_then_network", "wrong_type", "request_body_refused", "pages_unreadable"],
)
async def test_no_async_error_shows_a_body(case: str, waits: list[float]) -> None:
    try:
        await async_case(case)
    except Haau3Error as caught:
        error = caught
    else:
        raise AssertionError("no error")
    assert_hidden(error)


# --- what else could show one ---------------------------------------------------------------------


def test_a_failed_batch_entry_shows_no_body() -> None:
    batch = {
        "resourceType": "Bundle",
        "type": "batch-response",
        "entry": [{"response": {"status": "404", "outcome": outcome()}}],
    }
    results = sync_client(Recorder(answer(200, batch))).batch(calls.read_insurance_plan("H1"))
    (result,) = results
    assert isinstance(result, BatchError)
    assert "PHI-MARKER" in json.dumps(result.error.outcome)  # readable by code
    assert "PHI-MARKER" not in repr(results) + str(result.error) + pprint.pformat(vars(result.error)) + repr(result)


def test_an_answer_shows_no_body_through_with_response() -> None:
    response = sync_client(
        Recorder(answer(200, {"resourceType": "InsurancePlan", "name": MARKER}))
    ).with_response.read_insurance_plan("H1")
    assert "PHI-MARKER" not in repr(response) + str(response)
    raw = sync_client(Recorder(answer(200, outcome()))).request("GET", "/v1/x")
    assert "PHI-MARKER" not in repr(raw)


def test_a_pickled_error_carries_no_body(waits: list[float]) -> None:
    try:
        SYNC["refused_404"]()
    except Haau3Error as caught:
        assert b"PHI-MARKER" not in pickle.dumps(caught)


def test_the_key_never_shows_and_is_never_pickled() -> None:
    for client in (Haau3(api_key=KEY), AsyncHaau3(api_key=KEY)):
        shown = (
            repr(client) + str(client) + repr(vars(client)) + pprint.pformat(vars(client)) + repr(client.with_response)
        )
        assert KEY not in shown
        with pytest.raises(TypeError):
            pickle.dumps(client._engine.config.key)  # pyright: ignore[reportPrivateUsage]


# --- controls: the check above would catch a leak -------------------------------------------------


def test_control_a_careless_frame_is_caught() -> None:
    def careless(text: str) -> None:
        parsed = json.loads(text)
        raise NoAnswerError(f"failed with {len(parsed)} keys", reason="unreadable", attempts=1)

    try:
        careless(json.dumps(outcome()))
    except NoAnswerError as error:
        assert "PHI-MARKER" in rendered(error)
    else:
        raise AssertionError("no error")


def test_control_the_holder_is_what_hides_the_body(monkeypatch: pytest.MonkeyPatch, waits: list[float]) -> None:
    monkeypatch.setattr(body_module.Hidden, "__repr__", lambda self: repr(self.reveal()))
    try:
        SYNC["refused_404"]()
    except Haau3Error as error:
        assert "PHI-MARKER" in rendered(error)
    else:
        raise AssertionError("no error")
