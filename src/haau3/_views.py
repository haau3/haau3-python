"""The slot and schedule views: each Slot or Schedule a search page matched, with the resources the page
holds for it, side by side, each the FHIR resource the API served.

Read with FHIR's rules for references in a Bundle (`_bundles.resolve_in`), page by page, with no
request: FHIR adds included resources to each page. Nothing is replaced in place, so each resource
stays a FHIR resource. Plain functions, with no client; the clients' methods of the same names call
them, and the search helpers (`search_slot_with_includes`, `search_schedule_with_includes`, generated)
send the includes these read.

A value of another shape than FHIR gives it (a `physicalType` that is not an object, a code that is
not a string) is read as absent, as TypeScript's optional chaining reads it, so one malformed record
never fails a page.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Final, Literal, TypeVar, cast, final

from ._bundles import matches_in, resolve_in
from ._errors import Haau3Error

# The FHIR types are imported when the module runs, not only for type checkers, so a framework that
# reads the views' annotations at runtime (pydantic, and FastAPI through it) can resolve them.
from .fhir import (
    Bundle,
    HealthcareService,
    Location,
    Practitioner,
    PractitionerRole,
    Reference,
    Resource,
    Schedule,
    Slot,
)

# Where SMART Scheduling Links 1.0.0 publishes its extensions. The views read these URLs only.
_SMART_SCHEDULING_LINKS = "http://hl7.org/fhir/uv/smart-scheduling-links/StructureDefinition/"
_BOOKING_DEEP_LINK = f"{_SMART_SCHEDULING_LINKS}booking-deep-link"
_BOOKING_PHONE = f"{_SMART_SCHEDULING_LINKS}booking-phone"
_PATIENT_TYPE = f"{_SMART_SCHEDULING_LINKS}patient-type"

# HL7's code system for whom a provider accepts.
_ACCEPTING_PATIENTS = "http://terminology.hl7.org/CodeSystem/accepting-patients"

# HL7's code system for a Location's physical type, in which `vi` is a virtual location.
_LOCATION_PHYSICAL_TYPE = "http://terminology.hl7.org/CodeSystem/location-physical-type"

PatientType = Literal["newpt", "existptonly", "existptfam", "nopt"]
"""Whom a Slot accepts, as HL7's `accepting-patients` code system names it: `newpt` new and existing
patients, `existptonly` existing patients only, `existptfam` existing patients and their families,
`nopt` none. The API never serves a `nopt` Slot."""

_PATIENT_TYPES: Final[dict[str, PatientType]] = {code: code for code in ("newpt", "existptonly", "existptfam", "nopt")}


@dataclass(frozen=True)
class ScheduleView:
    """One Schedule of a search page with the resources the page holds for it, side by side, each as the
    FHIR resource it is: the Locations it reaches and its provider, however its publisher named it.

    Each tuple keeps the order the Schedule names them in, holds each resource once, and is empty when
    the page holds none: a Schedule may name a Location and no provider. Views compare by value, and
    cannot be hashed, since the resources they hold are dicts.
    """

    __hash__: ClassVar[None] = None  # type: ignore[assignment]  # pyright: ignore[reportIncompatibleMethodOverride]

    schedule: Schedule
    """The Schedule."""
    locations: tuple[Location, ...]
    """Every Location the Schedule reaches: its Location actors, then the Locations of each
    HealthcareService it names. A PractitionerRole's own locations are not followed."""
    practitioner_roles: tuple[PractitionerRole, ...]
    """The PractitionerRoles the Schedule names."""
    practitioners: tuple[Practitioner, ...]
    """The Practitioners the Schedule names, then the Practitioner behind each of its PractitionerRoles."""
    healthcare_services: tuple[HealthcareService, ...]
    """The HealthcareServices the Schedule names."""
    virtual: bool
    """Whether the place is virtual: True when any of `locations` has the physical type `vi`
    (`http://terminology.hl7.org/CodeSystem/location-physical-type`), and False otherwise. A Location
    is physical unless it says it is virtual, whether it states another physical type or none, and a
    place that is both is two Locations, as SMART Scheduling Links models it."""


@final
@dataclass(frozen=True)
class SlotView(ScheduleView):
    """One matched Slot of a search page with its Schedule and everything a `ScheduleView` holds, side by
    side as FHIR resources, and three values read from the Slot's SMART Scheduling Links 1.0.0
    extensions."""

    # A dataclass that is frozen and compares by value would get a hash over its fields again.
    __hash__: ClassVar[None] = None

    slot: Slot
    """The Slot."""
    booking_link: str | None
    """The Slot's booking link: its `booking-deep-link` extension's URL."""
    booking_phone: str | None
    """The phone number to book the Slot by: its `booking-phone` extension's value."""
    patient_type: PatientType | None
    """Whom the Slot accepts, from its `patient-type` extension, as a code of HL7's
    `http://terminology.hl7.org/CodeSystem/accepting-patients`. None when the Slot carries none, or one
    from another code system, or a code that system does not have."""


def _objects(value: object) -> list[Mapping[str, object]]:
    """The JSON objects of a JSON array; none when the value is not an array."""
    if not isinstance(value, list):
        return []
    return [cast("Mapping[str, object]", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _object(value: object) -> Mapping[str, object]:
    """The value as a JSON object; an empty one when it is not one."""
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class _Holders:
    """The Bundle entry each resource found so far is held in: itself, or for a contained resource the
    one containing it, which a reference from inside it resolves from. Keyed by identity."""

    def __init__(self) -> None:
        self._by_id: dict[int, Resource] = {}
        self._keep: list[Resource] = []  # The keys are ids, so the resources they name stay alive.

    def get(self, resource: Resource) -> Resource:
        return self._by_id.get(id(resource), resource)

    def note(self, resource: Resource, holder: Resource) -> None:
        if id(resource) not in self._by_id:
            self._by_id[id(resource)] = holder
            self._keep.append(resource)


R = TypeVar("R")
T = TypeVar("T")


def _follow(
    page: Bundle, holders: _Holders, reference: object, start: Resource, types: Sequence[str]
) -> Resource | None:
    """The resource a reference names, of one of `types`, found from `start` by FHIR's rules for a
    Bundle, with no request. A contained resource resolves from the entry that contains it, as FHIR
    says. A resource of another type than the element allows is left out, as one the page does not
    hold is, and so is a reference that is not a Reference object."""
    if not isinstance(reference, dict):
        return None
    given = cast("Mapping[str, object]", reference)
    holder = holders.get(start)
    found = resolve_in(page, cast("Reference", given), from_=holder)
    if found is None or _object(found).get("resourceType") not in types:
        return None
    contained = (_text(given.get("reference")) or "").startswith("#")
    holders.note(found, holder if contained else found)
    return found


def _once(items: list[R], item: R) -> None:
    """Adds an item not already there, by identity: two equal resources at two addresses stay two."""
    if not any(existing is item for existing in items):
        items.append(item)


_ACTOR_TYPES = ("Location", "PractitionerRole", "Practitioner", "HealthcareService")


def _reached(page: Bundle, holders: _Holders, schedule: Schedule) -> ScheduleView:
    """What a Schedule names and reaches in one page, found by FHIR's reference rules, with no request."""
    locations: list[Location] = []
    roles: list[PractitionerRole] = []
    practitioners: list[Practitioner] = []
    services: list[HealthcareService] = []
    start = cast("Resource", schedule)
    for actor in _objects(_object(schedule).get("actor")):
        found = _follow(page, holders, actor, start, _ACTOR_TYPES)
        if found is None:
            continue
        kind = _object(found).get("resourceType")
        if kind == "Location":
            _once(locations, cast("Location", found))
        elif kind == "PractitionerRole":
            _once(roles, cast("PractitionerRole", found))
        elif kind == "Practitioner":
            _once(practitioners, cast("Practitioner", found))
        else:
            _once(services, cast("HealthcareService", found))
    for role in roles:
        practitioner = _follow(
            page, holders, _object(role).get("practitioner"), cast("Resource", role), ("Practitioner",)
        )
        if practitioner is not None:
            _once(practitioners, cast("Practitioner", practitioner))
    for service in services:
        for reference in _objects(_object(service).get("location")):
            location = _follow(page, holders, reference, cast("Resource", service), ("Location",))
            if location is not None:
                _once(locations, cast("Location", location))
    return ScheduleView(
        schedule=schedule,
        locations=tuple(locations),
        practitioner_roles=tuple(roles),
        practitioners=tuple(practitioners),
        healthcare_services=tuple(services),
        virtual=any(_is_virtual(location) for location in locations),
    )


def _is_virtual(location: Location) -> bool:
    """Whether a Location says it is virtual: its physical type is HL7's `vi`. Otherwise it is physical."""
    codings = _objects(_object(_object(location).get("physicalType")).get("coding"))
    return any(c.get("system") == _LOCATION_PHYSICAL_TYPE and c.get("code") == "vi" for c in codings)


def _extension_value(slot: Slot, url: str, read: Callable[[Mapping[str, object]], T | None]) -> T | None:
    """The value of the first extension under `url` that carries the value it is read for."""
    for extension in _objects(_object(slot).get("extension")):
        if extension.get("url") != url:
            continue
        value = read(extension)
        if value is not None:
            return value
    return None


def _booking_link(extension: Mapping[str, object]) -> str | None:
    return _text(extension.get("valueUrl"))


def _booking_phone(extension: Mapping[str, object]) -> str | None:
    return _text(_object(extension.get("valueContactPoint")).get("value"))


def _patient_type(extension: Mapping[str, object]) -> PatientType | None:
    coding = _object(extension.get("valueCoding"))
    if coding.get("system") != _ACCEPTING_PATIENTS:
        return None
    code = _text(coding.get("code"))
    return _PATIENT_TYPES.get(code) if code is not None else None


def schedule_views(page: Bundle) -> list[ScheduleView]:
    """Each Schedule a search page matched, in page order, with its Locations and provider, with no request.

    Matches are entries whose search mode is `match` or says none, and of type Schedule. A resource the
    page does not hold is left out, and so is one of another type than its reference's element allows.
    Raises a `Haau3Error` only when two entries could each be the one a reference names.
    """
    holders = _Holders()
    return [_reached(page, holders, cast("Schedule", s)) for s in matches_in(page, "Schedule")]


def slot_views(page: Bundle) -> list[SlotView]:
    """Each Slot a search page matched, in page order, with its Schedule, Locations and provider, with no request.

    Matches are entries whose search mode is `match` or says none, and of type Slot. Raises a
    `Haau3Error` when a Slot names a Schedule the page does not hold, which means the page was searched
    without its includes (every Slot names one), and when two entries could each be the one a
    reference names. Anything else the page does not hold, or of another type than its element allows,
    is left out.
    """
    holders = _Holders()
    views: list[SlotView] = []
    for matched in matches_in(page, "Slot"):
        slot = cast("Slot", matched)
        schedule = _follow(page, holders, _object(slot).get("schedule"), matched, ("Schedule",))
        if schedule is None:
            raise Haau3Error(
                "A Slot in this page names a Schedule the page does not hold. Every Slot names one, and a "
                "search with its includes holds it: search with search_slot_with_includes, or send "
                'include="Slot:schedule".'
            )
        reached = _reached(page, holders, cast("Schedule", schedule))
        views.append(
            SlotView(
                schedule=reached.schedule,
                locations=reached.locations,
                practitioner_roles=reached.practitioner_roles,
                practitioners=reached.practitioners,
                healthcare_services=reached.healthcare_services,
                virtual=reached.virtual,
                slot=slot,
                booking_link=_extension_value(slot, _BOOKING_DEEP_LINK, _booking_link),
                booking_phone=_extension_value(slot, _BOOKING_PHONE, _booking_phone),
                patient_type=_extension_value(slot, _PATIENT_TYPE, _patient_type),
            )
        )
    return views
