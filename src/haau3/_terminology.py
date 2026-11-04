"""Reading a terminology answer, typed: `$lookup` and `$validate-code`.

`lookup_result()` and `validation_result()` read the answers of the terminology operations as FHIR's
own SDKs give them in Java (HAPI's `LookupCodeResult` and `CodeValidationResult`, LinuxForHealth's
`LookupOutcome`, Firely's `ValidateCodeResult`). `designations()` finds a lookup's names by use and
language.

Plain functions, with no client, as the readers in `_parameters` are. A part a reader does not know is
kept, verbatim, in the `other` tuple of the level it was found at, so nothing a server sent is lost
and nothing is guessed at.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias, Union, cast, final

from ._errors import Haau3Error
from ._json import as_dict

if TYPE_CHECKING:
    from .fhir import Coding, Parameters, ParametersParameter


@final
@dataclass(frozen=True)
class DesignationUse:
    """How a designation is used: a code from a code system, saying what the name is for."""

    system: str
    """The code system the use's code is from."""
    code: str
    """The use's code."""
    display: str | None = None
    """The code's display, when the answer gives one."""


PREFERRED_FOR_LANGUAGE_USE = DesignationUse(
    system="http://terminology.hl7.org/CodeSystem/hl7TermMaintInfra",
    code="preferredForLanguage",
    display="Preferred For Language",
)
"""The use of a name preferred for its language among several, HL7 Terminology's `preferredForLanguage`.

Every answer from a code system haau3 holds names the display again with this use.
"""

SYNONYM_USE = DesignationUse(
    system="http://fhir.org/packages/fhir.tx.support/CodeSystem/designation-usage",
    code="synonym",
    display="Synonym",
)
"""The use of a variant name that is also fit for display, the FHIR terminology ecosystem's `synonym`.

ICD-10-CM's short title has this use, where it differs from the display.
"""


@final
@dataclass(frozen=True)
class Designation:
    """One name for a code, as `lookup_result()` reads it."""

    language: str | None
    """The name's language, such as `en`, when the answer says."""
    use: DesignationUse | None
    """What the name is for, when the answer says. A name with no use is fit for display."""
    value: str
    """The name."""
    other: tuple[ParametersParameter, ...]
    """The designation's parts this reader does not read, such as R5's `additionalUse`, verbatim."""


@final
@dataclass(frozen=True)
class TextProperty:
    """A property whose value is a `code`, a `string` or a `dateTime`, which `type` names."""

    code: str
    """The property's code, in the code system's own words, such as `TTY`."""
    type: Literal["code", "string", "dateTime"]
    """The FHIR data type of the value."""
    value: str
    """The property's value."""
    description: str | None
    """The property's description, when the answer gives one."""
    subproperties: tuple[Property, ...]
    """The property's subproperties, in answer order. Empty when there are none."""
    other: tuple[ParametersParameter, ...]
    """The property's parts this reader does not read, verbatim."""


@final
@dataclass(frozen=True)
class IntegerProperty:
    """A property whose value is an `integer`."""

    code: str
    """The property's code, in the code system's own words."""
    type: Literal["integer"]
    """The FHIR data type of the value."""
    value: int
    """The property's value."""
    description: str | None
    """The property's description, when the answer gives one."""
    subproperties: tuple[Property, ...]
    """The property's subproperties, in answer order. Empty when there are none."""
    other: tuple[ParametersParameter, ...]
    """The property's parts this reader does not read, verbatim."""


@final
@dataclass(frozen=True)
class DecimalProperty:
    """A property whose value is a `decimal`."""

    code: str
    """The property's code, in the code system's own words."""
    type: Literal["decimal"]
    """The FHIR data type of the value."""
    value: float
    """The property's value."""
    description: str | None
    """The property's description, when the answer gives one."""
    subproperties: tuple[Property, ...]
    """The property's subproperties, in answer order. Empty when there are none."""
    other: tuple[ParametersParameter, ...]
    """The property's parts this reader does not read, verbatim."""


@final
@dataclass(frozen=True)
class BooleanProperty:
    """A property whose value is a `boolean`, such as ICD-10-CM's `notSelectable`."""

    code: str
    """The property's code, in the code system's own words."""
    type: Literal["boolean"]
    """The FHIR data type of the value."""
    value: bool
    """The property's value."""
    description: str | None
    """The property's description, when the answer gives one."""
    subproperties: tuple[Property, ...]
    """The property's subproperties, in answer order. Empty when there are none."""
    other: tuple[ParametersParameter, ...]
    """The property's parts this reader does not read, verbatim."""


@final
@dataclass(frozen=True)
class CodingProperty:
    """A property whose value is a `Coding`, such as a related concept."""

    code: str
    """The property's code, in the code system's own words."""
    type: Literal["Coding"]
    """The FHIR data type of the value."""
    value: Coding
    """The property's value, as the answer gives it."""
    description: str | None
    """The property's description, when the answer gives one."""
    subproperties: tuple[Property, ...]
    """The property's subproperties, in answer order. Empty when there are none."""
    other: tuple[ParametersParameter, ...]
    """The property's parts this reader does not read, verbatim."""


Property: TypeAlias = Union[TextProperty, IntegerProperty, DecimalProperty, BooleanProperty, CodingProperty]
"""One property of a code. `type` names the FHIR data type of its value, so checking it narrows `value`.

```python
for prop in haau3.lookup_result(answer).properties:
    if prop.type == "Coding":
        print(prop.code, prop.value.get("code"))
```
"""


@final
@dataclass(frozen=True)
class LookupResult:
    """What `lookup_result()` reads from a `$lookup` answer."""

    name: str | None
    """The code system's name, such as `ICD-10-CM`."""
    code: str | None
    """The code."""
    system: str | None
    """The code system."""
    version: str | None
    """The code system's version the answer is from."""
    display: str | None
    """The code's display."""
    abstract: bool | None
    """Whether the code is abstract, not for use, such as an ICD-10-CM header."""
    warning: str | None
    """What the answer warns of: for a code system haau3 holds, that another language was asked for
    and the answer is in English."""
    designations: tuple[Designation, ...]
    """Every designation, in answer order. `designations()` finds one by use and language."""
    properties: tuple[Property, ...]
    """Every property, in answer order."""
    other: tuple[ParametersParameter, ...]
    """The answer's parts this reader does not read, verbatim."""


@final
@dataclass(frozen=True)
class ValidationResult:
    """What `validation_result()` reads from a `$validate-code` answer."""

    result: bool | None
    """Whether the code is valid, with the display when one was sent."""
    display: str | None
    """The code's display, given even when a display sent did not match it."""
    message: str | None
    """What the server says about the answer, in its words."""
    other: tuple[ParametersParameter, ...]
    """The answer's parts this reader does not read, verbatim."""


# The `value[x]` a part can hold its text in.
_STRING_VALUES = ("valueString", "valueCode", "valueUri", "valueCanonical")


def _value_keys(part: Mapping[str, object]) -> list[str]:
    """The `value[x]` keys a part carries, ignoring one whose value is absent."""
    return [key for key, value in part.items() if key.startswith("value") and value is not None]


def _text(part: Mapping[str, object]) -> str | None:
    keys = _value_keys(part)
    value = part[keys[0]] if len(keys) == 1 and keys[0] in _STRING_VALUES else None
    return value if isinstance(value, str) else None


def _boolean(part: Mapping[str, object]) -> bool | None:
    keys = _value_keys(part)
    value = part.get("valueBoolean") if keys == ["valueBoolean"] else None
    return value if isinstance(value, bool) else None


def _use(part: Mapping[str, object]) -> DesignationUse | None:
    keys = _value_keys(part)
    coding = as_dict(part.get("valueCoding")) if keys == ["valueCoding"] else None
    if coding is None:
        return None
    system, code, display = coding.get("system"), coding.get("code"), coding.get("display")
    if not isinstance(system, str) or not isinstance(code, str):
        return None
    return DesignationUse(system=system, code=code, display=display if isinstance(display, str) else None)


_Reader = Callable[[Mapping[str, object]], object]


def _read_parts(
    where: str,
    parts: Sequence[ParametersParameter],
    readers: Mapping[str, _Reader],
    each: Callable[[ParametersParameter], bool] = lambda _part: False,
) -> tuple[dict[str, object], tuple[ParametersParameter, ...]]:
    """One level's parts, in order.

    Each part named by `readers` is read by its reader, and `each` takes the repeating parts it knows.
    A part neither reads goes to `other`, in place. Two parts with the name of one FHIR allows once are
    ambiguous, and raise.
    """
    for name in readers:
        count = sum(1 for part in parts if part.get("name") == name)
        if count > 1:
            hint = " Read them with parameters()." if where == "The Parameters" else ""
            raise Haau3Error(f"{where} holds {count} parts named {name}, so which one is meant is ambiguous.{hint}")
    values: dict[str, object] = {}
    other: list[ParametersParameter] = []
    for part in parts:
        part_name = part.get("name")
        if part_name is not None and part_name in readers:
            value = readers[part_name](part)
            if value is None:
                other.append(part)
            else:
                values[part_name] = value
        elif not each(part):
            other.append(part)
    return values, tuple(other)


def _str(values: Mapping[str, object], name: str) -> str | None:
    value = values.get(name)
    return value if isinstance(value, str) else None


def _designation(part: ParametersParameter) -> Designation | None:
    values, other = _read_parts("A designation", part.get("part", []), {"language": _text, "use": _use, "value": _text})
    value = _str(values, "value")
    if value is None:
        return None
    use = values.get("use")
    return Designation(
        language=_str(values, "language"),
        use=use if isinstance(use, DesignationUse) else None,
        value=value,
        other=other,
    )


@dataclass(frozen=True)
class _Typed:
    kind: str
    value: object


def _property_value(part: Mapping[str, object]) -> _Typed | None:
    """The property's FHIR data type and value, from the one `value[x]` it carries."""
    keys = _value_keys(part)
    if len(keys) != 1:
        return None
    key, value = keys[0], part[keys[0]]
    if key in ("valueCode", "valueString", "valueDateTime") and isinstance(value, str):
        return _Typed({"valueCode": "code", "valueString": "string", "valueDateTime": "dateTime"}[key], value)
    if key == "valueInteger" and isinstance(value, int) and not isinstance(value, bool):
        return _Typed("integer", value)
    if key == "valueDecimal" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return _Typed("decimal", float(value))
    if key == "valueBoolean" and isinstance(value, bool):
        return _Typed("boolean", value)
    coding = as_dict(value) if key == "valueCoding" else None
    if coding is not None:
        return _Typed("Coding", coding)
    return None


def _property(part: ParametersParameter, where: str = "A property") -> Property | None:
    subproperties: list[Property] = []

    def subproperty(sub: ParametersParameter) -> bool:
        read = _property(sub, "A subproperty") if sub.get("name") == "subproperty" else None
        if read is not None:
            subproperties.append(read)
        return read is not None

    values, other = _read_parts(
        where,
        part.get("part", []),
        {"code": _text, "value": _property_value, "description": _text},
        subproperty,
    )
    code, typed = _str(values, "code"), values.get("value")
    if code is None or not isinstance(typed, _Typed):
        return None
    kind, value = typed.kind, typed.value
    description, subs = _str(values, "description"), tuple(subproperties)
    if kind == "integer" and isinstance(value, int):
        return IntegerProperty(code, "integer", value, description, subs, other)
    if kind == "decimal" and isinstance(value, float):
        return DecimalProperty(code, "decimal", value, description, subs, other)
    if kind == "boolean" and isinstance(value, bool):
        return BooleanProperty(code, "boolean", value, description, subs, other)
    if kind == "Coding":
        return CodingProperty(code, "Coding", cast("Coding", value), description, subs, other)
    if isinstance(value, str):
        text: Literal["code", "string", "dateTime"] = (
            "code" if kind == "code" else "dateTime" if kind == "dateTime" else "string"
        )
        return TextProperty(code, text, value, description, subs, other)
    return None


def lookup_result(answer: Parameters) -> LookupResult:
    """A `CodeSystem/$lookup` answer, typed, with no request.

    The code, its system and version, its display, whether it is abstract, every designation and every
    property. A part this reader does not know stays in `other`. Raises a `Haau3Error` when a part FHIR
    allows once comes twice.

    ```python
    answer = client.code_system_lookup(system="http://hl7.org/fhir/sid/icd-10-cm", code="E08.37X1")
    lookup = haau3.lookup_result(answer)
    print(lookup.display, haau3.designations(lookup, use=haau3.SYNONYM_USE)[0].value)
    ```
    """
    found: list[Designation] = []
    properties: list[Property] = []

    def each(part: ParametersParameter) -> bool:
        if part.get("name") == "designation":
            designation = _designation(part)
            if designation is not None:
                found.append(designation)
            return designation is not None
        if part.get("name") == "property":
            prop = _property(part)
            if prop is not None:
                properties.append(prop)
            return prop is not None
        return False

    readers: dict[str, _Reader] = {name: _text for name in ("name", "code", "system", "version", "display", "warning")}
    readers["abstract"] = _boolean
    values, other = _read_parts("The Parameters", answer.get("parameter", []), readers, each)
    abstract = values.get("abstract")
    return LookupResult(
        name=_str(values, "name"),
        code=_str(values, "code"),
        system=_str(values, "system"),
        version=_str(values, "version"),
        display=_str(values, "display"),
        abstract=abstract if isinstance(abstract, bool) else None,
        warning=_str(values, "warning"),
        designations=tuple(found),
        properties=tuple(properties),
        other=other,
    )


def validation_result(answer: Parameters) -> ValidationResult:
    """A `$validate-code` answer, from a code system or a value set, typed, with no request.

    Whether the code is valid, its display, and the server's message. A display that did not match
    answers `result` False with the code's display beside it. Raises a `Haau3Error` when a part FHIR
    allows once comes twice.

    ```python
    answer = client.code_system_validate_code(url="http://loinc.org", code="8867-4", display="Pulse")
    check = haau3.validation_result(answer)
    ```
    """
    values, other = _read_parts(
        "The Parameters", answer.get("parameter", []), {"result": _boolean, "display": _text, "message": _text}
    )
    result = values.get("result")
    return ValidationResult(
        result=result if isinstance(result, bool) else None,
        display=_str(values, "display"),
        message=_str(values, "message"),
        other=other,
    )


def _language_matches(tag: str, language_range: str) -> bool:
    """RFC 4647's basic filtering: a range matches a tag equal to it, or one that starts with it and a
    hyphen, ignoring case; `*` matches every tag."""
    tag, language_range = tag.lower(), language_range.lower()
    return language_range == "*" or tag == language_range or tag.startswith(f"{language_range}-")


def designations(
    lookup: LookupResult, *, use: DesignationUse | None = None, language: str | None = None
) -> list[Designation]:
    """The designations of a lookup with a use and in a language, in answer order, with no request.

    A use matches by its system and code together. A language matches as RFC 4647's basic filtering
    says, so `en` finds `en` and `en-US`. A field left out matches every designation; a designation
    that names no use, or no language, is found only when that field is left out.

    ```python
    short_title = haau3.designations(haau3.lookup_result(answer), use=haau3.SYNONYM_USE, language="en")
    ```
    """
    found: list[Designation] = []
    for designation in lookup.designations:
        if use is not None and (
            designation.use is None or (designation.use.system, designation.use.code) != (use.system, use.code)
        ):
            continue
        if language is not None and (
            designation.language is None or not _language_matches(designation.language, language)
        ):
            continue
        found.append(designation)
    return found
