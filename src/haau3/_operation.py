"""One operation of the spec, as the generated methods describe it to the client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, final

T = TypeVar("T")


@final
@dataclass(frozen=True)
class DeclaredHeader:
    """A header parameter an operation declares, sent from the call's keyword of its name in snake_case."""

    wire: str
    """The header, as the spec names it: `Payer-Authorization`."""
    option: str
    """The keyword it is sent from: `payer_authorization`."""
    max_length: int | None = None
    pattern: str | None = None
    """The spec's pattern, an ECMAScript regular expression the generator checked Python reads alike."""


@final
@dataclass(frozen=True)
class Operation(Generic[T]):
    """An operation: its method, its path in the spec, and the FHIR resource types it answers with.

    `T` is what it answers with; it is never stored, only carried for the type checker.
    """

    method: Literal["GET", "POST"]
    path: str
    """The path in the spec, such as `/v1/fhir/r4/InsurancePlan/{id}`."""
    expects: tuple[str, ...] = ()
    """The FHIR resource types a success may be. Empty checks no type (Scheduling's answers)."""
    headers: tuple[DeclaredHeader, ...] = ()
    """The header parameters it declares, each sent from a keyword of the call's own."""

    @property
    def label(self) -> str:
        """How a message names the request: `GET /v1/fhir/r4/InsurancePlan/{id}`, never a value sent."""
        return f"{self.method} {self.path}"
