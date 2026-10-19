"""One operation of the spec, as the generated methods describe it to the client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, final

T = TypeVar("T")


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

    @property
    def label(self) -> str:
        """How a message names the request: `GET /v1/fhir/r4/InsurancePlan/{id}`, never a value sent."""
        return f"{self.method} {self.path}"
