"""What an answer means: the answer itself, or the error it is. Pure, and shared by both clients.

A body is held in a `Hidden` throughout. Only small helpers look inside one, and each returns a fact
that carries no value from the body (a resource type that looks like one, a count, a status) or a
new `Hidden`, so no frame that can raise holds a body in a plain local variable.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, TypeAlias, Union, cast

from ._body import Hidden
from ._errors import API_ERROR_SYSTEM, AuthError, ForbiddenError, NoAnswerError, NoAnswerReason, RefusedError
from ._json import as_dict, as_list

if TYPE_CHECKING:
    from ._components import Problem
    from .fhir import OperationOutcome

LONGEST_WAIT_SECONDS = 60
"""The longest `Retry-After` the client waits out. A longer one fails at once and says when."""

_JSON = re.compile("json", re.I)
_DIGITS = re.compile(r"\d+", re.A)
_PLAUSIBLE_TYPE = re.compile(r"[A-Z][A-Za-z]{0,63}", re.A)

Verdict: TypeAlias = Union[Hidden[object], AuthError, ForbiddenError, RefusedError, NoAnswerError]

# Why the API refused a request with a 403, in the words of each message: the reasons the API names
# by a code of its own. Any other 403, or one without a code, keeps the general sentence.
_FORBIDDEN_REASONS = {
    "test-key-outside-sandbox": "this is a test key, which works only in the Sandbox",
    "live-key-in-sandbox": "this is a live key, which does not work in the Sandbox",
    "phi-agreement-required": (
        "its organization may not send patient data, and not every resource in this request is labelled as test data"
    ),
}
_FORBIDDEN_GENERAL = "the API key was accepted, and its organization may not make this request"


def standard_phrase(status: int) -> str:
    """A status's standard text (RFC 9110), or "" for one with none."""
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def api_error_code(outcome: Hidden[OperationOutcome] | None, problem: Hidden[Problem] | None) -> str | None:
    """The API's own code for why it refused a request, when it names one.

    The `api-error` coding of an OperationOutcome's first issue that has one, or a problem's `code`.
    A code, never words, so it is safe to log.
    """
    if problem is not None:
        value = as_dict(problem.reveal())
        code = value.get("code") if value is not None else None
        return code if isinstance(code, str) else None
    if outcome is None:
        return None
    value = as_dict(outcome.reveal())
    for issue in (as_list(value.get("issue")) if value is not None else None) or []:
        entry = as_dict(issue)
        details = as_dict(entry.get("details")) if entry is not None else None
        for coding in (as_list(details.get("coding")) if details is not None else None) or []:
            c = as_dict(coding)
            code = c.get("code") if c is not None else None
            if c is not None and c.get("system") == API_ERROR_SYSTEM and isinstance(code, str):
                return code
    return None


def parse_body(content: Hidden[bytes], content_type: str | None) -> Hidden[object] | None:
    """The body as JSON when its type says JSON and it parses, else None. JSON `null` is `Hidden(None)`."""
    if not content_type or not _JSON.search(content_type):
        return None
    try:
        return Hidden(json.loads(content.reveal()))
    except (ValueError, UnicodeDecodeError):
        # A JSONDecodeError holds the whole text; it ends here, with this frame.
        return None


def resource_type(body: Hidden[object]) -> object:
    """The body's `resourceType`, whatever it is, or None."""
    value = as_dict(body.reveal())
    return value.get("resourceType") if value is not None else None


def _with_article(type_: str) -> str:
    return f"{'an' if type_[:1] in 'AEIOU' else 'a'} {type_}"


def describe_answer(body: Hidden[object]) -> str:
    """How an answer is named in a message: its resource type when it has a plausible one, never a value."""
    type_ = resource_type(body)
    if isinstance(type_, str) and _PLAUSIBLE_TYPE.fullmatch(type_):
        return _with_article(type_)
    return "something that is not a FHIR resource"


def read_error_body(body: Hidden[object] | None) -> tuple[Hidden[OperationOutcome] | None, Hidden[Problem] | None]:
    """An error body as the SDK reads it: an OperationOutcome under the FHIR base, a problem elsewhere."""
    if body is None:
        return None, None
    value = as_dict(body.reveal())
    if value is None:
        return None, None
    if value.get("resourceType") == "OperationOutcome":
        return cast("Hidden[OperationOutcome]", body), None
    status = value.get("status")
    if isinstance(value.get("type"), str) and isinstance(status, int) and not isinstance(status, bool):
        return None, cast("Hidden[Problem]", body)
    return None, None


def retry_after_seconds(value: str | None) -> int | None:
    """Seconds from a `Retry-After` header, when it holds a number of seconds. An HTTP date is not read."""
    if value is None or not _DIGITS.fullmatch(value.strip()):
        return None
    return int(value.strip())


def _random() -> float:
    return random.random()


def backoff(attempt: int) -> float:
    """Seconds to wait after a failed attempt: about 0.5, then 1, 2 and so on, never past 8, with jitter."""
    ceiling = min(8.0, 0.5 * 2.0 ** (attempt - 1))
    return ceiling * (0.5 + _random() * 0.5)


def wait_after_answer(attempt: int, max_retries: int, status: int, retry_after: str | None) -> float | None:
    """Seconds to wait before retrying an answer, or None to keep it. Only a 429 or a 5xx is retried."""
    if not (status == 429 or status >= 500) or attempt > max_retries:
        return None
    wait = retry_after_seconds(retry_after)
    if wait is None:
        return backoff(attempt)
    return float(wait) if wait <= LONGEST_WAIT_SECONDS else None


def wait_after_failure(attempt: int, max_retries: int) -> float | None:
    """Seconds to wait before retrying after a timeout or a network failure, or None when no attempts are left."""
    return backoff(attempt) if attempt <= max_retries else None


def _phrase(error: BaseException) -> str | None:
    """The system's own words for one error, when it is one the system names; never an address."""
    if isinstance(error, ssl.SSLCertVerificationError):
        return (
            f"certificate verify failed: {error.verify_message}"
            if error.verify_message
            else "certificate verify failed"
        )
    if isinstance(error, ssl.SSLError):
        return f"TLS failed: {error.reason}" if error.reason else "TLS failed"
    if isinstance(error, socket.gaierror):
        return error.strerror if isinstance(error.strerror, str) and error.strerror else "name not resolved"
    if isinstance(error, OSError) and isinstance(error.errno, int) and error.errno > 0:
        return os.strerror(error.errno)
    return None


def detail(error: BaseException) -> str:
    """What failed, safe to log: the error's class and, when one is found, the system's phrase for its cause.

    `"ConnectError: Connection refused"`, `"ConnectError: certificate verify failed: unable to get local
    issuer certificate"`, `"ReadTimeout"`. Never `str(error)`, which can name the URL or the address.
    """
    seen: set[int] = set()
    queue: list[BaseException] = [error]
    phrase = None
    while queue and phrase is None and len(seen) < 32:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        phrase = _phrase(current)
        # An exception group (trio's, anyio's) holds the causes it gathered.
        queue += [e for e in getattr(current, "exceptions", ()) if isinstance(e, BaseException)]
        queue += [e for e in (current.__cause__, current.__context__) if e is not None]
    name = type(error).__name__
    return f"{name}: {phrase}" if phrase else name


@dataclass(frozen=True)
class Context:
    """What came with an answer, for `judge`."""

    request_id: str | None
    correlation_id: str | None
    attempts: int
    duration_ms: int
    missing: str
    """How a success with no body is described: "a body that is not JSON", "no resource"."""
    retry_after: int | None = None
    expects: Sequence[str] = ()
    """The FHIR resource types a success may be. Empty checks no type."""
    entry: bool = False
    """An answer inside a batch: the key is checked once for the whole batch, so a 401 or 403 on one
    entry is a refusal of that entry, not of the key."""
    reason_phrase: str = ""
    """The status's text the answer came with; empty means the standard one."""


def _no_answer_reason(status: int) -> NoAnswerReason:
    if status == 429:
        return "rate-limited"
    if status in (502, 503):
        return "unavailable"
    if status == 504:
        return "timeout"
    return "server-error"


def judge(name: str, status: int, body: Hidden[object] | None, context: Context) -> Verdict:
    """What one answer means. A single call and each entry of a batch are judged alike.

    `name` says which request it was, in words that carry no value a caller sent: the method and the
    operation's path with its `{id}` left in. `body` is None when the answer was not JSON.
    """
    c = context
    phrase = c.reason_phrase or standard_phrase(status)

    def no_answer(message: str, reason: NoAnswerReason, retry_after: int | None = None) -> NoAnswerError:
        outcome, problem = read_error_body(body) if status >= 400 else (None, None)
        return NoAnswerError(
            message,
            reason=reason,
            status=status,
            reason_phrase=phrase,
            retry_after=retry_after,
            attempts=c.attempts,
            duration_ms=c.duration_ms,
            request_id=c.request_id,
            correlation_id=c.correlation_id,
            outcome=outcome,
            problem=problem,
        )

    if 200 <= status < 300:
        if body is None:
            return no_answer(f"{name} answered {status} with {c.missing}.", "unreadable")
        if c.expects and resource_type(body) not in c.expects:
            wanted = " or ".join(_with_article(t) for t in c.expects)
            return no_answer(f"{name} answered {describe_answer(body)} where {wanted} was expected.", "unreadable")
        return body
    if status == 429 or status >= 500:
        reason = _no_answer_reason(status)
        tries = f"{c.attempts} attempt{'' if c.attempts == 1 else 's'}"
        return no_answer(f"{name} got no answer ({reason}, {status}) after {tries}.", reason, c.retry_after)
    if status < 400:
        return no_answer(f"{name} answered {status}, which is not an answer.", "unreadable")
    outcome, problem = read_error_body(body)
    if status == 403 and not c.entry:
        code = api_error_code(outcome, problem)
        why = _FORBIDDEN_REASONS.get(code, _FORBIDDEN_GENERAL) if code is not None else _FORBIDDEN_GENERAL
        return ForbiddenError(
            f"{name} was refused: {why} ({status}).",
            status=status,
            reason_phrase=phrase,
            attempts=c.attempts,
            duration_ms=c.duration_ms,
            request_id=c.request_id,
            correlation_id=c.correlation_id,
            outcome=outcome,
            problem=problem,
            code=code,
        )
    kind = AuthError if status == 401 and not c.entry else RefusedError
    message = (
        f"{name} was refused: the API key was not accepted ({status})."
        if kind is AuthError
        else f"{name} was refused ({status})."
    )
    return kind(
        message,
        status=status,
        reason_phrase=phrase,
        attempts=c.attempts,
        duration_ms=c.duration_ms,
        request_id=c.request_id,
        correlation_id=c.correlation_id,
        outcome=outcome,
        problem=problem,
    )
