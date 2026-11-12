"""The errors the SDK raises: `Haau3Error`, and the three kinds of failure under it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from typing_extensions import Required, TypedDict

from ._body import Hidden

if TYPE_CHECKING:
    from ._components import Problem
    from .fhir import OperationOutcome

ClientErrorCode = Literal[
    "no-api-key",
    "invalid-api-key",
    "api-key-not-alone",
    "test-key-outside-sandbox",
    "live-key-in-sandbox",
    "older-key-in-sandbox",
    "invalid-environment",
    "invalid-base-url",
    "environment-conflicts-with-base-url",
    "invalid-app-info",
    "invalid-http-client",
    "in-browser",
]
"""Why `Haau3()` or `AsyncHaau3()` refused to make a client, before anything was sent: the `code` of the
`Haau3Error` it raises. Branch on the code; the message says the same for a person, and names where the
value came from (the keyword or the variable).

- `no-api-key`: no key, and the Sandbox is not named as the environment.
- `invalid-api-key`: the key is not a string.
- `api-key-not-alone`: the key has something before its `haau3_sk_` prefix, such as the quotes an env
  file kept, or `Bearer `.
- `test-key-outside-sandbox`: a test key with production named. The API's own name for it.
- `live-key-in-sandbox`: a live key with the Sandbox named, or with the Sandbox's address as the base
  URL. The API's own name for it.
- `older-key-in-sandbox`: the same for a key made before test and live keys.
- `invalid-environment`: an environment other than `"production"` or `"sandbox"`.
- `invalid-base-url`: a base URL that is not an http or https URL with a host.
- `environment-conflicts-with-base-url`: a named environment, and a base URL on the other environment's
  own address.
- `invalid-app-info`: an `app_info` the User-Agent cannot carry.
- `invalid-http-client`: an `http_client` that is not the httpx2 client the class takes.
- `in-browser`: the client was made in a browser (Pyodide), where an API key is public.
"""

NoAnswerReason = Literal["timeout", "network", "rate-limited", "unavailable", "server-error", "unreadable"]
"""Why a request got no answer.

- `timeout`: an attempt ran past its timeout, or the API answered 504.
- `network`: the request never reached the API, or the connection failed.
- `rate-limited`: the API answered 429. `retry_after` says when to try again.
- `unavailable`: the API, or a source it reads, could not answer (502, 503).
- `server-error`: the API failed (500).
- `unreadable`: the API answered with something that is not an answer: a body that is not JSON, or
  a FHIR resource of another type than the operation answers with.
"""


class ErrorDict(TypedDict, total=False):
    """What `Haau3Error.to_dict()` gives: the fields that are safe to log, never a body or a cause."""

    name: Required[str]
    message: Required[str]
    status: int
    reason_phrase: str
    code: str
    reason: NoAnswerReason
    retry_after: int
    attempts: int
    duration_ms: int
    request_id: str
    correlation_id: str
    detail: str


# The fields `to_dict()` copies when they are set, in this order.
_LOGGED = (
    "status",
    "reason_phrase",
    "code",
    "reason",
    "retry_after",
    "attempts",
    "duration_ms",
    "request_id",
    "correlation_id",
    "detail",
)
# The bodies an error holds, which are never pickled or copied.
_BODIES = ("_outcome", "_problem")


def _rebuild(cls: type[Haau3Error], message: str, state: dict[str, Any]) -> Haau3Error:
    error = cls.__new__(cls)
    Exception.__init__(error, message)
    error.__dict__.update(state)
    if isinstance(error, _Answered):
        error.__dict__.update({name: None for name in _BODIES})
    return error


class Haau3Error(Exception):
    """What every error this package raises is.

    Raised as itself when the SDK refuses to send a request: no API key, a URL on another host than
    the client's `base_url`, a `correlation_id` that is not a UUID, an entry a batch cannot take.
    The three kinds below are raised when a request was sent.
    """

    request_id: str | None
    """The `X-Request-Id` the API sent, when it answered. Quote it when you ask for help."""
    correlation_id: str | None
    """The `correlation_id` you sent, as the API returned it in `X-Correlation-Id`, when it answered."""
    code: str | None
    """Why, as a code to branch on, when there is one: a `ClientErrorCode` when the client could not be
    made, or the API's own code on a `ForbiddenError` or a `RefusedError`."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
        code: ClientErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.code = code

    @property
    def message(self) -> str:
        """What happened. It names the request by its method and the operation's path, never by a value you sent."""
        return str(self.args[0]) if self.args else ""

    def to_dict(self) -> ErrorDict:
        """The error as a dict that is safe to log: its name, its message and every field that is set.

        Never the body the API answered with (`outcome`, `problem`) and never the error's cause.

        ```python
        logger.warning("the plan read failed", extra={"error": error.to_dict()})
        ```
        """
        out: dict[str, object] = {"name": type(self).__name__, "message": self.message}
        for key in _LOGGED:
            value = getattr(self, key, None)
            if value is not None:
                out[key] = value
        return cast("ErrorDict", out)

    def __reduce__(self) -> tuple[Any, ...]:
        # Pickled and copied with every field but the body, which is never pickled.
        state = {k: v for k, v in self.__dict__.items() if k not in _BODIES}
        return (_rebuild, (type(self), self.message, state))


class _Answered(Haau3Error):
    """An error the API answered with, which may carry the body it answered."""

    attempts: int
    """How many attempts were made."""
    duration_ms: int | None
    """Milliseconds from the first attempt to the answer, waits between attempts included."""
    _outcome: Hidden[OperationOutcome] | None
    _problem: Hidden[Problem] | None

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        duration_ms: int | None,
        request_id: str | None,
        correlation_id: str | None,
        outcome: Hidden[OperationOutcome] | None,
        problem: Hidden[Problem] | None,
    ) -> None:
        super().__init__(message, request_id=request_id, correlation_id=correlation_id)
        self.attempts = attempts
        self.duration_ms = duration_ms
        self._outcome = outcome
        self._problem = problem

    @property
    def outcome(self) -> OperationOutcome | None:
        """The OperationOutcome the FHIR base answered with. Never printed when the error is."""
        return self._outcome.reveal() if self._outcome is not None else None

    @property
    def problem(self) -> Problem | None:
        """The problem (RFC 9457) the other services answered with. Never printed when the error is."""
        return self._problem.reveal() if self._problem is not None else None


class AuthError(_Answered):
    """The API did not accept the key (401).

    None was sent, it is not one the API knows, or it was revoked. A 403 is never this error: the
    key was accepted, and the answer is a `ForbiddenError`.
    """

    status: int
    reason_phrase: str
    """The status's text: `"Unauthorized"`."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        reason_phrase: str = "",
        attempts: int = 1,
        duration_ms: int | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        outcome: Hidden[OperationOutcome] | None = None,
        problem: Hidden[Problem] | None = None,
    ) -> None:
        super().__init__(
            message,
            attempts=attempts,
            duration_ms=duration_ms,
            request_id=request_id,
            correlation_id=correlation_id,
            outcome=outcome,
            problem=problem,
        )
        self.status = status
        self.reason_phrase = reason_phrase


API_ERROR_SYSTEM = "https://haau3.com/fhir/CodeSystem/api-error"
SANDBOX_ERROR_SYSTEM = "https://haau3.com/fhir/CodeSystem/sandbox-error"
"""The Sandbox's own code system: `not-the-example`, for a request that is not an operation's documented example."""
"""The code system the API names its own reasons in, inside an OperationOutcome's `details.coding`."""


class ForbiddenError(_Answered):
    """The API accepted the key and refused the request (403).

    The key itself is real, so there is nothing to rotate. `code` says why when the API names it,
    and the message says it in words:

    - `test-key-outside-sandbox`: a test key works only in the Sandbox. Send a live key.
    - `live-key-in-sandbox`: a live key does not work in the Sandbox. Send a test key.
    - `phi-agreement-required`: the organization may not send patient data, and not every resource
      in the request is labelled as test data. `mark_as_test_data` labels made-up data.
    - `not-open-to-customers`: the operation is not yet open to customers, so it did not run. An
      expected answer, not a fault in the request.

    Without a code, the key's organization may not make the request; `outcome` (or `problem`) says
    why, in the API's words. Not an `AuthError`, which is
    a key the API did not accept (401).
    """

    status: int
    reason_phrase: str
    """The status's text: `"Forbidden"`."""
    code: str | None
    """The API's code for why, from its `api-error` code system, when it names one."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        reason_phrase: str = "",
        attempts: int = 1,
        duration_ms: int | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        outcome: Hidden[OperationOutcome] | None = None,
        problem: Hidden[Problem] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            attempts=attempts,
            duration_ms=duration_ms,
            request_id=request_id,
            correlation_id=correlation_id,
            outcome=outcome,
            problem=problem,
        )
        self.status = status
        self.reason_phrase = reason_phrase
        self.code = code


class RefusedError(_Answered):
    """The API answered and refused the request.

    A parameter it does not take, a record that does not exist (404), a page that no longer exists
    (410), a body that is too large (413). `status` says which, and the body says why. `code` says
    why when the API names it: `not-the-example` from the Sandbox, which answers each operation's
    documented example only and refuses anything else with a 400 naming it.
    """

    status: int
    reason_phrase: str
    """The status's text: `"Not Found"`."""
    code: str | None
    """The API's code for why, from its `api-error` or `sandbox-error` code system, when it names one."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        reason_phrase: str = "",
        attempts: int = 1,
        duration_ms: int | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        outcome: Hidden[OperationOutcome] | None = None,
        problem: Hidden[Problem] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(
            message,
            attempts=attempts,
            duration_ms=duration_ms,
            request_id=request_id,
            correlation_id=correlation_id,
            outcome=outcome,
            problem=problem,
        )
        self.status = status
        self.reason_phrase = reason_phrase
        self.code = code


class NoAnswerError(_Answered):
    """The request got no answer, after every attempt the client was allowed.

    This never means "no results": an empty search answers with an empty Bundle or list, not with
    this error.
    """

    reason: NoAnswerReason
    status: int | None
    """The last status the API answered with, when it answered at all."""
    reason_phrase: str | None
    """That status's text, when it answered at all: `"Service Unavailable"`."""
    retry_after: int | None
    """Seconds to wait before trying again, when the API said."""
    detail: str | None
    """For a timeout or a network failure, what failed, safe to log: `"ConnectError: Connection refused"`."""

    def __init__(
        self,
        message: str,
        *,
        reason: NoAnswerReason,
        attempts: int,
        duration_ms: int | None = None,
        status: int | None = None,
        reason_phrase: str | None = None,
        retry_after: int | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        detail: str | None = None,
        outcome: Hidden[OperationOutcome] | None = None,
        problem: Hidden[Problem] | None = None,
    ) -> None:
        super().__init__(
            message,
            attempts=attempts,
            duration_ms=duration_ms,
            request_id=request_id,
            correlation_id=correlation_id,
            outcome=outcome,
            problem=problem,
        )
        self.status = status
        self.reason_phrase = reason_phrase if status is not None else None
        self.reason = reason
        self.retry_after = retry_after
        self.detail = detail
