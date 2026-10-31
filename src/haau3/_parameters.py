"""Reading a Parameters answer and a structured Dosage, and building a `$structure-dosage` request.

`parameters()` and `parameter()` give a Parameters answer's parts by name, as FHIR's own SDKs read one
(Firely's `Get` and `GetSingleValue`, HAPI's `ParametersUtil`). `review_flags()` reads the review flags
and versions `$structure-dosage` writes on each Dosage it answers with. `structure_dosage_request()`
builds the request that operation takes.

Plain functions, with no client: they send nothing and need no key, so they read a stored answer and
build a request anywhere. The clients' methods of the same names call them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, final

from ._errors import Haau3Error

if TYPE_CHECKING:
    from .fhir import CodeableConcept, Dosage, Extension, Parameters, ParametersParameter

RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
"""RxNorm's code system, for a medication's coding."""

NDC_SYSTEM = "http://hl7.org/fhir/sid/ndc"
"""The NDC's code system, for a medication's coding."""

# The extensions Structured Sig writes on each Dosage, as its implementation guide at
# https://fhir.haau3.com names them.
_PROFILE_BASE = "https://haau3.com/fhir/StructureDefinition"
_REVIEW_FLAG = f"{_PROFILE_BASE}/structured-sig-review-flag"
_ENGINE_VERSION = f"{_PROFILE_BASE}/structured-sig-engine-version"
_VOCABULARY_VERSION = f"{_PROFILE_BASE}/structured-sig-vocabulary-version"
_RXNORM_RELEASE = f"{_PROFILE_BASE}/structured-sig-rxnorm-release"


@final
@dataclass(frozen=True)
class ReviewFlag:
    """One thing a person should check about a Dosage."""

    code: str
    """The flag's code, from `https://haau3.com/fhir/CodeSystem/structured-sig-review-flag`."""
    display: str | None
    """The flag's display, such as "Route and dose form differ"."""
    words: str | None
    """The words of the directions the flag is about, exactly as written, when it is about words."""


@final
@dataclass(frozen=True)
class DosageReview:
    """What `review_flags()` reads from a Dosage."""

    flags: tuple[ReviewFlag, ...]
    """Every flag on the Dosage, in the order the answer gives them. Empty when nothing needs checking."""
    engine_version: str | None
    """How the directions were read, in the form `YYYY-MM-DD.vN`."""
    vocabulary_version: str | None
    """The codes the directions were coded with, in the form `YYYY-MM-DD.vN`."""
    rxnorm_release: str | None
    """The RxNorm release the medication's code was looked up in, when it carried one."""


def structure_dosage_request(text: str, *, medication: CodeableConcept | None = None) -> Parameters:
    """The Parameters `$structure-dosage` takes, from the directions and, optionally, the medication.

    A `text` part, the directions exactly as written (at most 1,000 characters), and a `medication`
    part when there is one: the medication's codings, RxNorm or NDC, as a CodeableConcept. With an
    RxNorm code, the product's dose form checks the dose's unit and the route, and supplies either
    when the directions name none. It checks nothing; the API refuses what it does not accept, with
    its reason.

    ```python
    answer = client.structure_dosage(
        structure_dosage_request(
            "Instill 2 drops in the affected ear twice daily",
            medication={"coding": [{"system": RXNORM_SYSTEM, "code": "1923428"}]},
        )
    )
    ```
    """
    parameter: list[ParametersParameter] = [{"name": "text", "valueString": text}]
    if medication is not None:
        parameter.append({"name": "medication", "valueCodeableConcept": medication})
    return {"resourceType": "Parameters", "parameter": parameter}


def parameters(answer: Parameters, name: str) -> list[ParametersParameter]:
    """Every part of a Parameters answer with that name, in order.

    FHIR operations answer this way: `$lookup`'s `property` parts, `$structure-dosage`'s `dosage`
    part. Each part holds its value in the `value[x]` its operation names, so read `valueDosage`,
    `valueString` and so on from it. Empty when there is none.
    """
    return [part for part in answer.get("parameter", []) if part.get("name") == name]


def parameter(answer: Parameters, name: str) -> ParametersParameter | None:
    """The one part of a Parameters answer with that name, or None when there is none.

    Raises a `Haau3Error` when there are two, since which one is meant is then ambiguous;
    `parameters()` reads a name that repeats.
    """
    found = parameters(answer, name)
    if len(found) > 1:
        raise Haau3Error(
            f"The Parameters holds {len(found)} parts named {name}, so which one is meant is ambiguous. "
            "Read them with parameters()."
        )
    return found[0] if found else None


def _value(extensions: Sequence[Extension], url: str) -> str | None:
    for extension in extensions:
        if extension.get("url") == url:
            return extension.get("valueString")
    return None


def review_flags(dosage: Dosage) -> DosageReview:
    """The review flags on a Dosage `$structure-dosage` answered with, and the versions it names.

    Each flag is something a person should check: its code, its display, and the words of the
    directions it is about, when it is about words. The versions say how the directions were read and
    coded, and which RxNorm release the medication was looked up in.
    """
    extensions = dosage.get("extension", [])
    flags: list[ReviewFlag] = []
    for extension in extensions:
        if extension.get("url") != _REVIEW_FLAG:
            continue
        parts = extension.get("extension", [])
        coding = next((part.get("valueCoding") for part in parts if part.get("url") == "code"), None)
        # A flag is its code; one without a code says nothing a caller could act on.
        if coding is None or "code" not in coding:
            continue
        flags.append(ReviewFlag(code=coding["code"], display=coding.get("display"), words=_value(parts, "words")))
    return DosageReview(
        flags=tuple(flags),
        engine_version=_value(extensions, _ENGINE_VERSION),
        vocabulary_version=_value(extensions, _VOCABULARY_VERSION),
        rxnorm_release=_value(extensions, _RXNORM_RELEASE),
    )
