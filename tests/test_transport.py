"""Retries, waits, timeouts and what is sent: the transport both clients share."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

import haau3._judge as judge
from haau3 import AsyncHaau3, Haau3, NoAnswerError, RefusedError

from .helpers import KEY, Client, Recorder, answer, async_client, sync_client

PLAN = {"resourceType": "InsurancePlan", "id": "H1"}


@pytest.mark.anyio
async def test_a_5xx_is_retried_once_then_answers(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(503), answer(200, PLAN))
    response = await Client(kind, recorder).with_response("read_insurance_plan", "H1")
    assert response.data == PLAN
    assert response.attempts == 2
    assert len(recorder.requests) == 2
    assert len(waits) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "reason", "detail"),
    [
        (httpx2.ConnectError("refused"), "network", "ConnectError"),
        (httpx2.ReadTimeout("slow"), "timeout", "ReadTimeout"),
        (httpx2.RemoteProtocolError("closed"), "network", "RemoteProtocolError"),
    ],
)
async def test_a_failure_with_no_answer_is_retried_then_raised(
    kind: str, waits: list[float], failure: Exception, reason: str, detail: str
) -> None:
    recorder = Recorder(failure)
    error = await Client(kind, recorder).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.attempts, error.detail, error.status) == (reason, 2, detail, None)
    assert error.message == f"GET /v1/fhir/r4/InsurancePlan/{{id}} got no answer ({reason}) after 2 attempts."
    assert len(recorder.requests) == 2
    # Raised outside the `except`, so no chained traceback carries the request.
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.anyio
async def test_429_is_retried_and_a_short_retry_after_is_waited(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(429, headers={"retry-after": "2"}), answer(200, PLAN))
    assert await Client(kind, recorder)("read_insurance_plan", "H1") == PLAN
    assert waits == [2.0]


@pytest.mark.anyio
async def test_retry_after_is_honoured_on_a_5xx_too(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(503, headers={"retry-after": "0"}), answer(200, PLAN))
    assert await Client(kind, recorder)("read_insurance_plan", "H1") == PLAN
    assert waits == [0.0]


@pytest.mark.anyio
async def test_a_retry_after_over_60_seconds_fails_at_once(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(429, headers={"retry-after": "86400"}))
    error = await Client(kind, recorder).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.retry_after, error.attempts, error.status) == ("rate-limited", 86400, 1, 429)
    assert waits == []
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_an_http_date_retry_after_is_not_read(
    kind: str, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge, "_random", lambda: 0.0)
    recorder = Recorder(answer(503, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), answer(200, PLAN))
    await Client(kind, recorder)("read_insurance_plan", "H1")
    assert waits == [0.25]


@pytest.mark.anyio
async def test_max_retries_zero_on_the_client_or_the_call_sends_once(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(503))
    assert isinstance(await Client(kind, recorder, max_retries=0).raises("read_insurance_plan", "H1"), NoAnswerError)
    assert len(recorder.requests) == 1
    recorder = Recorder(answer(503))
    assert isinstance(await Client(kind, recorder).raises("read_insurance_plan", "H1", max_retries=0), NoAnswerError)
    assert len(recorder.requests) == 1
    recorder = Recorder(answer(503))
    await Client(kind, recorder).raises("read_insurance_plan", "H1", max_retries=3)
    assert len(recorder.requests) == 4


@pytest.mark.anyio
async def test_a_4xx_is_never_retried(kind: str, waits: list[float]) -> None:
    recorder = Recorder(answer(404, {"resourceType": "OperationOutcome", "issue": []}))
    assert isinstance(await Client(kind, recorder).raises("read_insurance_plan", "H1"), RefusedError)
    assert len(recorder.requests) == 1
    assert waits == []


@pytest.mark.parametrize(
    ("attempt", "low", "high"), [(1, 0.25, 0.5), (2, 0.5, 1.0), (3, 1.0, 2.0), (5, 4.0, 8.0), (9, 4.0, 8.0)]
)
def test_backoff_grows_and_stops_at_8_seconds(
    monkeypatch: pytest.MonkeyPatch, attempt: int, low: float, high: float
) -> None:
    monkeypatch.setattr(judge, "_random", lambda: 0.0)
    assert judge.backoff(attempt) == low
    monkeypatch.setattr(judge, "_random", lambda: 1.0)
    assert judge.backoff(attempt) == high


@pytest.mark.anyio
async def test_a_redirect_is_not_followed_and_is_not_an_answer(kind: str) -> None:
    recorder = Recorder(answer(302, headers={"location": "https://example.com/elsewhere"}))
    error = await Client(kind, recorder).raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert (error.reason, error.status) == ("unreadable", 302)
    assert error.message == "GET /v1/fhir/r4/InsurancePlan/{id} answered 302, which is not an answer."
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_a_redirect_is_not_followed_even_by_a_client_that_follows_them(kind: str) -> None:
    recorder = Recorder(
        answer(302, headers={"location": "https://api.haau3.com/v1/fhir/r4/InsurancePlan/H2"}), answer(200, PLAN)
    )
    transport = httpx2.MockTransport(recorder)
    client: Haau3 | AsyncHaau3
    if kind == "sync":
        client = Haau3(api_key=KEY, http_client=httpx2.Client(transport=transport, follow_redirects=True))
        with pytest.raises(NoAnswerError):
            client.read_insurance_plan("H1")
    else:
        client = AsyncHaau3(api_key=KEY, http_client=httpx2.AsyncClient(transport=transport, follow_redirects=True))
        with pytest.raises(NoAnswerError):
            await client.read_insurance_plan("H1")
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_the_timeout_reaches_the_request(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    client = Client(kind, recorder, timeout=12)
    await client("read_insurance_plan", "H1")
    assert recorder.last.extensions["timeout"] == {"connect": 12.0, "read": 12.0, "write": 12.0, "pool": 12.0}
    await client("read_insurance_plan", "H1", timeout=3)
    assert recorder.last.extensions["timeout"]["read"] == 3.0


@pytest.mark.anyio
async def test_extra_headers_are_sent(kind: str) -> None:
    recorder = Recorder(answer(200, PLAN))
    await Client(kind, recorder)("read_insurance_plan", "H1", extra_headers={"X-Trace": "abc"})
    assert recorder.last.headers["x-trace"] == "abc"
    assert recorder.last.headers["authorization"] == f"Bearer {KEY}"


def test_defaults() -> None:
    client = sync_client(Recorder(answer(200, PLAN)))
    assert (client.base_url, client.timeout, client.max_retries) == ("https://api.haau3.com", 65.0, 1)
    client = sync_client(Recorder(answer(200, PLAN)), base_url="https://example.test/api///", timeout=5, max_retries=0)
    assert (client.base_url, client.timeout, client.max_retries) == ("https://example.test/api", 5.0, 0)


def test_a_client_you_pass_is_used_and_never_closed() -> None:
    recorder = Recorder(answer(200, PLAN))
    http = httpx2.Client(transport=httpx2.MockTransport(recorder))
    with Haau3(api_key=KEY, http_client=http) as client:
        client.read_insurance_plan("H1")
    assert len(recorder.requests) == 1
    assert not http.is_closed


def test_a_client_the_sdk_made_is_closed() -> None:
    client = Haau3(api_key=KEY)
    client.close()
    assert client._engine._http.is_closed  # pyright: ignore[reportPrivateUsage]


@pytest.mark.anyio
async def test_an_async_client_the_sdk_made_is_closed() -> None:
    async with AsyncHaau3(api_key=KEY) as client:
        pass
    assert client._engine._http.is_closed  # pyright: ignore[reportPrivateUsage]


# --- real sockets -----------------------------------------------------------------------------


class _Server:
    """A local server that answers each connection with `respond(conn)`."""

    def __init__(self, respond: Any) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.respond = respond
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        self.sock.settimeout(0.1)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.recv(65536)
            self.respond(conn, self.stop)
        except OSError:
            pass
        finally:
            conn.close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


@pytest.fixture
def hanging() -> Iterator[_Server]:
    server = _Server(lambda conn, stop: stop.wait(5))
    yield server
    server.close()


@pytest.fixture
def dripping() -> Iterator[_Server]:
    def respond(conn: socket.socket, stop: threading.Event) -> None:
        body = b'{"resourceType":"InsurancePlan","id":"H1"}' + b" " * 40
        conn.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/fhir+json\r\ncontent-length: %d\r\n\r\n" % len(body)
        )
        for byte in body:
            if stop.wait(0.05):
                return
            conn.sendall(bytes([byte]))

    server = _Server(respond)
    yield server
    server.close()


@pytest.mark.anyio
async def test_an_attempt_that_never_answers_times_out(kind: str, hanging: _Server) -> None:
    client = Client(kind, None, base_url=hanging.url, timeout=0.3, max_retries=0)  # type: ignore[arg-type]
    client.inner = (Haau3 if kind == "sync" else AsyncHaau3)(
        api_key=KEY, base_url=hanging.url, timeout=0.3, max_retries=0
    )
    started = time.monotonic()
    error = await client.raises("read_insurance_plan", "H1")
    assert isinstance(error, NoAnswerError)
    assert error.reason == "timeout"
    assert time.monotonic() - started < 2


@pytest.mark.anyio
async def test_an_answer_that_keeps_dripping_times_out(kind: str, dripping: _Server) -> None:
    # Every read arrives within the timeout; the attempt as a whole does not.
    inner = (Haau3 if kind == "sync" else AsyncHaau3)(api_key=KEY, base_url=dripping.url, timeout=0.5, max_retries=0)
    started = time.monotonic()
    with pytest.raises(NoAnswerError) as raised:
        result = inner.read_insurance_plan("H1")
        if kind == "async":
            await result  # type: ignore[misc]
    elapsed = time.monotonic() - started
    assert raised.value.reason == "timeout"
    assert elapsed < (1.2 if kind == "sync" else 0.9)


@pytest.mark.anyio
async def test_a_refused_connection_says_so(kind: str) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    inner = (Haau3 if kind == "sync" else AsyncHaau3)(api_key=KEY, base_url=f"http://127.0.0.1:{port}", max_retries=0)
    with pytest.raises(NoAnswerError) as raised:
        result = inner.read_insurance_plan("H1")
        if kind == "async":
            await result  # type: ignore[misc]
    assert raised.value.reason == "network"
    assert raised.value.detail == "ConnectError: Connection refused"
    assert "127.0.0.1" not in raised.value.message


def test_the_async_client_works_without_a_running_test_harness() -> None:
    import anyio

    async def main() -> object:
        client = async_client(Recorder(answer(200, PLAN)))
        return await client.read_insurance_plan("H1")

    assert anyio.run(main) == PLAN
    assert anyio.run(main, backend="trio") == PLAN
