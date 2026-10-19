"""The errors the SDK raises: `Haau3Error`, and the three kinds of failure under it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from typing_extensions import Required, TypedDict

from ._body import Hidden

if TYPE_CHECKING:
    from ._components import Problem
    from .fhir import OperationOutcome

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
    reason: NoAnswerReason
    retry_after: int
    attempts: int
    duration_ms: int
    request_id: str
    correlation_id: str
    detail: str


# The fields `to_dict()` copies when they are set, in this order.
_LOGGED = ("status", "reason", "retry_after", "attempts", "duration_ms", "request_id", "correlation_id", "detail")
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

    def __init__(self, message: str, *, request_id: str | None = None, correlation_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.correlation_id = correlation_id

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
    """The API refused the key: none was sent, it is not one the API knows, or it was revoked (401, 403)."""

    status: int

    def __init__(
        self,
        message: str,
        *,
        status: int,
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


class RefusedError(_Answered):
    """The API answered and refused the request.

    A parameter it does not take, a record that does not exist (404), a page that no longer exists
    (410), a body that is too large (413). `status` says which, and the body says why.
    """

    status: int

    def __init__(
        self,
        message: str,
        *,
        status: int,
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


class NoAnswerError(_Answered):
    """The request got no answer, after every attempt the client was allowed.

    This never means "no results": an empty search answers with an empty Bundle or list, not with
    this error.
    """

    reason: NoAnswerReason
    status: int | None
    """The last status the API answered with, when it answered at all."""
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
        self.reason = reason
        self.retry_after = retry_after
        self.detail = detail
