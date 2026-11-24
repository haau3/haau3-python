"""The slot and schedule views: each matched Slot or Schedule with the resources the page holds for it,
side by side as FHIR resources, read with FHIR's reference rules and no request; and the two search
helpers, which send every include their search serves and nothing else."""

from __future__ import annotations

import dataclasses
from typing import Any

import generate
import pytest

import haau3
from haau3 import Haau3Error, ScheduleView, SlotView, schedule_views, slot_views

from .helpers import (
    REQUEST_ID,
    SPEC,
    Client,
    Recorder,
    answer,
    async_client,
    example_answer,
    spec_operation,
    sync_client,
)

BASE = "https://api.haau3.com/v1/fhir/r4"
SSL = "http://hl7.org/fhir/uv/smart-scheduling-links/StructureDefinition/"
# SMART's own registry, where the extensions were published before the HL7 guide.
EARLIER = "http://fhir-registry.smarthealthit.org/StructureDefinition/"
PHYSICAL_TYPE = "http://terminology.hl7.org/CodeSystem/location-physical-type"
ACCEPTING = "http://terminology.hl7.org/CodeSystem/accepting-patients"
EMPTY_SEARCH: Any = {"resourceType": "Bundle", "type": "searchset", "total": 0}


def page(matches: list[Any], included: list[Any] | None = None) -> Any:
    """A search page: the matches, then what the includes brought, each at its address on the base."""

    def entry(resource: Any, mode: str) -> Any:
        return {
            "fullUrl": f"{BASE}/{resource['resourceType']}/{resource['id']}",
            "resource": resource,
            "search": {"mode": mode},
        }

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(matches),
        "entry": [entry(r, "match") for r in matches] + [entry(r, "include") for r in included or []],
    }


def location(id: str, physical_type: dict[str, str] | None = None) -> Any:
    found: Any = {"resourceType": "Location", "id": id, "name": f"Place {id}"}
    if physical_type is not None:
        found["physicalType"] = {"coding": [physical_type]}
    return found


def practitioner(id: str) -> Any:
    return {"resourceType": "Practitioner", "id": id, "name": [{"text": f"Clinician {id}"}]}


def role(id: str, practitioner_id: str) -> Any:
    return {
        "resourceType": "PractitionerRole",
        "id": id,
        "practitioner": {"reference": f"Practitioner/{practitioner_id}"},
    }


def service(id: str, *location_ids: str) -> Any:
    return {
        "resourceType": "HealthcareService",
        "id": id,
        "location": [{"reference": f"Location/{i}"} for i in location_ids],
    }


def schedule(id: str, *actors: str) -> Any:
    return {"resourceType": "Schedule", "id": id, "actor": [{"reference": a} for a in actors]}


def slot(id: str, schedule_id: str, extension: list[Any] | None = None) -> Any:
    found: Any = {
        "resourceType": "Slot",
        "id": id,
        "schedule": {"reference": f"Schedule/{schedule_id}"},
        "status": "free",
        "start": "2027-01-04T09:00:00-05:00",
        "end": "2027-01-04T09:30:00-05:00",
    }
    if extension:
        found["extension"] = extension
    return found


def spec_answer(path: str) -> Any:
    return example_answer(spec_operation("GET", path))


def no_requests() -> Recorder:
    return Recorder(AssertionError("a view sends nothing"))


# --- the spec's own example answers ----------------------------------------------------------------


def test_the_slot_searchs_example_gives_its_one_slot_with_its_schedule_location_role_and_practitioner() -> None:
    answer_ = spec_answer("/v1/fhir/r4/Slot")
    [view] = slot_views(answer_)
    assert view.slot["resourceType"] == "Slot"
    assert view.schedule["resourceType"] == "Schedule"
    assert view.slot["schedule"].get("reference") == f"Schedule/{view.schedule.get('id')}"
    assert [place.get("name") for place in view.locations] == ["haau3 Cambridge Imaging Center"]
    assert len(view.practitioner_roles) == 1
    assert [n.get("text") for p in view.practitioners for n in p.get("name", [])] == ["Dr. Wei Example"]
    assert view.healthcare_services == ()
    # The guide's own `ph` is not `vi`, so the place is physical.
    assert view.virtual is False
    assert view.patient_type == "existptonly"
    assert (view.booking_link or "").startswith("https://docs.haau3.com/")
    assert view.booking_phone == "+1-617-555-0100"
    # Each is the resource the page holds, not a copy.
    held = [e["resource"] for e in answer_["entry"]]
    for resource in [view.slot, view.schedule, *view.locations, *view.practitioner_roles, *view.practitioners]:
        assert any(resource is h for h in held)


def test_the_schedule_searchs_example_gives_its_one_schedule_with_its_location_role_and_practitioner() -> None:
    [view] = schedule_views(spec_answer("/v1/fhir/r4/Schedule"))
    assert view.schedule["resourceType"] == "Schedule"
    assert [place.get("name") for place in view.locations] == ["haau3 Cambridge Imaging Center"]
    assert [n.get("text") for p in view.practitioners for n in p.get("name", [])] == ["Dr. Wei Example"]
    assert view.virtual is False


# --- what a Schedule reaches -----------------------------------------------------------------------


def test_takes_its_location_actors_then_each_services_locations_each_once_whatever_order_it_names_them_in() -> None:
    s = schedule("s", "HealthcareService/svc", "Location/a")
    [view] = schedule_views(page([s], [location("a"), service("svc", "b", "a"), location("b")]))
    assert [place.get("id") for place in view.locations] == ["a", "b"]
    assert [h.get("id") for h in view.healthcare_services] == ["svc"]


def test_leaves_out_an_actor_of_a_type_the_view_does_not_list_held_in_the_page() -> None:
    device = {"resourceType": "Device", "id": "d"}
    [view] = schedule_views(page([schedule("s", "Device/d", "Location/a")], [device, location("a")]))
    assert [place.get("id") for place in view.locations] == ["a"]
    assert (view.practitioner_roles, view.practitioners, view.healthcare_services) == ((), (), ())


def test_leaves_out_a_target_of_another_type_than_its_element_allows_and_keeps_the_rest_of_the_page() -> None:
    organization = {"resourceType": "Organization", "id": "o"}
    r = {"resourceType": "PractitionerRole", "id": "r", "practitioner": {"reference": "Organization/o"}}
    views = schedule_views(
        page(
            [schedule("s", "PractitionerRole/r", "Location/a"), schedule("t", "Location/a")],
            [r, organization, location("a")],
        )
    )
    assert [v.schedule.get("id") for v in views] == ["s", "t"]
    assert [x.get("id") for x in views[0].practitioner_roles] == ["r"]
    assert views[0].practitioners == ()


def test_resolves_a_relative_reference_against_the_base_of_the_entry_that_holds_it() -> None:
    other = "https://other.example/fhir"
    two: Any = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {"fullUrl": f"{other}/Schedule/s", "resource": schedule("s", "Location/a"), "search": {"mode": "match"}},
            {
                "fullUrl": f"{BASE}/Location/a",
                "resource": {**location("a"), "name": "on the API's base"},
                "search": {"mode": "include"},
            },
            {
                "fullUrl": f"{other}/Location/a",
                "resource": {**location("a"), "name": "on the other base"},
                "search": {"mode": "include"},
            },
        ],
    }
    assert [place.get("name") for place in schedule_views(two)[0].locations] == ["on the other base"]


def test_does_not_follow_a_practitioner_roles_own_locations() -> None:
    r = {**role("r", "p"), "location": [{"reference": "Location/elsewhere"}]}
    [view] = schedule_views(page([schedule("s", "PractitionerRole/r")], [r, practitioner("p"), location("elsewhere")]))
    assert view.locations == ()
    assert [p.get("id") for p in view.practitioners] == ["p"]


def test_gives_empty_tuples_for_a_schedule_that_names_no_provider() -> None:
    [view] = schedule_views(page([schedule("s", "Location/a")], [location("a")]))
    assert [place.get("id") for place in view.locations] == ["a"]
    assert (view.practitioner_roles, view.practitioners, view.healthcare_services) == ((), (), ())


def test_lists_a_practitioner_actor_first_then_the_one_behind_each_role_and_one_named_twice_once() -> None:
    s = schedule("s", "PractitionerRole/r", "Practitioner/q", "Practitioner/p")
    [view] = schedule_views(page([s], [role("r", "p"), practitioner("p"), practitioner("q")]))
    assert [p.get("id") for p in view.practitioners] == ["q", "p"]


def test_keeps_two_equal_resources_at_two_addresses_as_two() -> None:
    """Once means the same resource, by identity: two entries with equal contents are two Locations."""
    same: Any = {"resourceType": "Location", "name": "Same name"}
    bundle: Any = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "fullUrl": f"{BASE}/Schedule/s",
                "resource": schedule("s", "Location/a", "Location/b"),
                "search": {"mode": "match"},
            },
            {"fullUrl": f"{BASE}/Location/a", "resource": dict(same), "search": {"mode": "include"}},
            {"fullUrl": f"{BASE}/Location/b", "resource": dict(same), "search": {"mode": "include"}},
        ],
    }
    [view] = schedule_views(bundle)
    assert len(view.locations) == 2
    assert view.locations[0] is not view.locations[1]
    # The control: the same Location named twice is held once.
    [twice] = schedule_views(page([schedule("s", "Location/a", "Location/a")], [location("a")]))
    assert len(twice.locations) == 1


def test_leaves_out_a_resource_the_page_does_not_hold_and_asks_the_api_for_nothing() -> None:
    recorder = no_requests()
    client = sync_client(recorder)
    [view] = client.schedule_views(
        page([schedule("s", "Location/a", "PractitionerRole/gone", "Device/d")], [location("a")])
    )
    assert [place.get("id") for place in view.locations] == ["a"]
    assert view.practitioner_roles == ()
    assert recorder.requests == []


def test_raises_when_the_page_holds_two_entries_a_reference_could_name_as_resolve_does() -> None:
    twice: Any = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {"fullUrl": f"{BASE}/Schedule/s", "resource": schedule("s", "Location/a"), "search": {"mode": "match"}},
            {"fullUrl": f"{BASE}/Location/a", "resource": location("a"), "search": {"mode": "include"}},
            {"fullUrl": f"{BASE}/Location/a", "resource": location("a"), "search": {"mode": "include"}},
        ],
    }
    with pytest.raises(Haau3Error, match="matches 2 entries"):
        schedule_views(twice)


# --- contained resources ---------------------------------------------------------------------------


def test_contained_resources_resolve_from_the_entry_that_contains_them_and_what_they_name_too() -> None:
    contained_role: Any = {
        "resourceType": "PractitionerRole",
        "id": "r",
        "practitioner": {"reference": "Practitioner/p"},
    }
    contained_service: Any = {"resourceType": "HealthcareService", "id": "h", "location": [{"reference": "Location/b"}]}
    s = {**schedule("s", "#r", "#h", "Location/a"), "contained": [contained_role, contained_service]}
    [view] = schedule_views(page([s], [location("a"), location("b"), practitioner("p")]))
    assert view.practitioner_roles == (contained_role,)
    assert view.practitioner_roles[0] is contained_role
    assert [p.get("id") for p in view.practitioners] == ["p"]
    assert view.healthcare_services[0] is contained_service
    assert [place.get("id") for place in view.locations] == ["a", "b"]


def test_a_slot_gets_its_contained_schedule_and_what_that_schedule_names() -> None:
    contained_schedule: Any = schedule("s", "Location/a")
    x = {**slot("x", "s"), "schedule": {"reference": "#s"}, "contained": [contained_schedule]}
    [view] = slot_views(page([x], [location("a")]))
    assert view.schedule is contained_schedule
    assert [place.get("id") for place in view.locations] == ["a"]


def test_a_contained_schedules_own_contained_reference_resolves_from_the_slot_that_holds_it() -> None:
    """A Schedule contained in a Slot names `#r`, which is the Slot's: the container is the entry."""
    contained_role: Any = {
        "resourceType": "PractitionerRole",
        "id": "r",
        "practitioner": {"reference": "Practitioner/p"},
    }
    contained_schedule: Any = schedule("s", "#r")
    x = {**slot("x", "s"), "schedule": {"reference": "#s"}, "contained": [contained_schedule, contained_role]}
    [view] = slot_views(page([x], [practitioner("p")]))
    assert view.practitioner_roles[0] is contained_role
    assert [p.get("id") for p in view.practitioners] == ["p"]


# --- whether the place is virtual ------------------------------------------------------------------


def _virtual_of(*locations: Any) -> bool:
    return schedule_views(page([schedule("s", *[f"Location/{loc['id']}" for loc in locations])], list(locations)))[
        0
    ].virtual


def test_virtual_is_true_when_any_location_is_vi_and_false_otherwise_another_type_none_stated_or_no_location() -> None:
    assert _virtual_of(location("a", {"system": PHYSICAL_TYPE, "code": "vi"})) is True
    assert (
        _virtual_of(
            location("a", {"system": PHYSICAL_TYPE, "code": "bu"}),
            location("b", {"system": PHYSICAL_TYPE, "code": "vi"}),
        )
        is True
    )
    assert _virtual_of(location("a", {"system": PHYSICAL_TYPE, "code": "bu"})) is False
    # A Location is physical unless it says it is virtual.
    assert _virtual_of(location("a")) is False
    assert _virtual_of() is False


def test_virtual_reads_vi_only_in_hl7s_physical_type_code_system() -> None:
    """The control: `vi` in another code system says nothing."""
    assert _virtual_of(location("a", {"system": "http://example.org/other", "code": "vi"})) is False


# --- what a Slot carries ---------------------------------------------------------------------------


def _view_of(extension: list[Any]) -> SlotView:
    return slot_views(page([slot("x", "s", extension)], [schedule("s")]))[0]


def test_reads_the_booking_link_phone_and_patient_type_from_smart_scheduling_links_1_0_0s_extensions() -> None:
    view = _view_of(
        [
            {"url": f"{SSL}booking-deep-link", "valueUrl": "https://book.example/x"},
            {"url": f"{SSL}booking-phone", "valueContactPoint": {"system": "phone", "value": "+1-555-0100"}},
            {"url": f"{SSL}patient-type", "valueCoding": {"system": ACCEPTING, "code": "newpt"}},
        ]
    )
    assert (view.booking_link, view.booking_phone, view.patient_type) == (
        "https://book.example/x",
        "+1-555-0100",
        "newpt",
    )


def test_reads_none_under_smarts_earlier_registry_urls_which_1_0_0_replaced() -> None:
    view = _view_of(
        [
            {"url": f"{EARLIER}booking-deep-link", "valueUrl": "https://book.example/x"},
            {"url": f"{EARLIER}booking-phone", "valueString": "+1-555-0100"},
        ]
    )
    assert (view.booking_link, view.booking_phone) == (None, None)
    # The resource still carries both, as the API served it.
    assert len(view.slot.get("extension", [])) == 2


def test_reads_no_patient_type_from_another_code_system_nor_a_code_hl7s_does_not_have() -> None:
    assert (
        _view_of(
            [{"url": f"{SSL}patient-type", "valueCoding": {"system": "http://example.org/other", "code": "newpt"}}]
        ).patient_type
        is None
    )
    assert (
        _view_of([{"url": f"{SSL}patient-type", "valueCoding": {"system": ACCEPTING, "code": "somept"}}]).patient_type
        is None
    )
    # The control: each of HL7's four is read, nopt included.
    for code in ("newpt", "existptonly", "existptfam", "nopt"):
        assert (
            _view_of([{"url": f"{SSL}patient-type", "valueCoding": {"system": ACCEPTING, "code": code}}]).patient_type
            == code
        )


def test_reads_the_first_extension_of_its_url_that_carries_the_value_it_is_read_for() -> None:
    view = _view_of(
        [
            {"url": f"{SSL}booking-deep-link", "valueString": "not a url value"},
            {"url": f"{SSL}booking-deep-link", "valueUrl": "https://book.example/second"},
            {"url": f"{SSL}booking-phone", "valueString": "+1-555-0199"},
            {"url": f"{SSL}booking-phone", "valueContactPoint": {"system": "phone", "value": "+1-555-0100"}},
        ]
    )
    assert (view.booking_link, view.booking_phone) == ("https://book.example/second", "+1-555-0100")


def test_carries_none_when_the_slot_has_no_extensions() -> None:
    view = _view_of([])
    assert (view.booking_link, view.booking_phone, view.patient_type) == (None, None, None)


# --- which entries are views -----------------------------------------------------------------------


def test_only_matches_of_the_views_own_type_never_an_included_entry_or_another_type() -> None:
    s = schedule("s", "Location/a")
    bundle = page([slot("x", "s"), location("m")], [s, location("a"), slot("included", "s")])
    assert [v.slot.get("id") for v in slot_views(bundle)] == ["x"]
    assert schedule_views(bundle) == []


def test_none_for_an_empty_page() -> None:
    assert slot_views(EMPTY_SEARCH) == []
    assert schedule_views(EMPTY_SEARCH) == []


def test_raises_naming_the_fix_when_a_slots_schedule_is_not_in_the_page() -> None:
    with pytest.raises(Haau3Error) as raised:
        slot_views(page([slot("x", "s")]))
    assert 'search with search_slot_with_includes, or send include="Slot:schedule".' in raised.value.message
    # A Slot whose schedule names a resource of another type holds no Schedule either.
    elsewhere = {**slot("x", "s"), "schedule": {"reference": "Location/a"}}
    with pytest.raises(Haau3Error, match="names a Schedule the page does not hold"):
        slot_views(page([elsewhere], [location("a")]))
    # The control: the same page with its Schedule included gives its view.
    assert len(slot_views(page([slot("x", "s")], [schedule("s")]))) == 1


def test_the_clients_views_are_what_the_plain_functions_give() -> None:
    recorder = no_requests()
    bundle = page([slot("x", "s")], [schedule("s", "Location/a"), location("a")])
    schedules = page([schedule("s", "Location/a")], [location("a")])
    for client in (sync_client(recorder), async_client(recorder)):
        assert client.slot_views(bundle) == slot_views(bundle)
        assert client.schedule_views(schedules) == schedule_views(schedules)
    assert recorder.requests == []


def test_a_slot_view_is_a_frozen_schedule_view() -> None:
    [view] = slot_views(page([slot("x", "s")], [schedule("s", "Location/a"), location("a")]))
    assert isinstance(view, ScheduleView)
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.virtual = True  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]


# --- the search helpers ----------------------------------------------------------------------------


def _query(recorder: Recorder) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, value in recorder.query():
        found.setdefault(name, []).append(value)
    return found


@pytest.mark.anyio
async def test_search_slot_with_includes_sends_the_callers_parameters_and_every_include_and_nothing_else(
    kind: str,
) -> None:
    recorder = Recorder(answer(200, EMPTY_SEARCH))
    assert (
        await Client(kind, recorder)("search_slot_with_includes", location_address_state="MA", count=10) == EMPTY_SEARCH
    )
    assert recorder.last.url.path == "/v1/fhir/r4/Slot"
    assert _query(recorder) == {
        "location-address-state": ["MA"],
        "_include": ["Slot:schedule"],
        "_include:iterate": ["Schedule:actor", "PractitionerRole:practitioner", "HealthcareService:location"],
        "_count": ["10"],
    }


@pytest.mark.anyio
async def test_search_schedule_with_includes_does_the_same_for_the_schedule_search(kind: str) -> None:
    recorder = Recorder(answer(200, EMPTY_SEARCH))
    await Client(kind, recorder)("search_schedule_with_includes", location_address_city="Boston", page="next-page")
    assert recorder.last.url.path == "/v1/fhir/r4/Schedule"
    assert _query(recorder) == {
        "location-address-city": ["Boston"],
        "_include": ["Schedule:actor"],
        "_include:iterate": ["PractitionerRole:practitioner", "HealthcareService:location"],
        "page": ["next-page"],
    }


@pytest.mark.anyio
async def test_a_helpers_request_is_the_search_with_those_includes(kind: str) -> None:
    helped, plain = Recorder(answer(200, EMPTY_SEARCH)), Recorder(answer(200, EMPTY_SEARCH))
    await Client(kind, helped)(
        "search_slot_with_includes", provider_identifier="http://hl7.org/fhir/sid/us-npi|2900000227", count=1
    )
    include, iterate = generate.WITH_INCLUDES["searchSlot"]
    await Client(kind, plain)(
        "search_slot",
        provider_identifier="http://hl7.org/fhir/sid/us-npi|2900000227",
        include=list(include),
        include_iterate=list(iterate),
        count=1,
    )
    assert str(helped.last.url) == str(plain.last.url)


@pytest.mark.anyio
async def test_the_helpers_pass_a_calls_options_on_as_the_search_does(kind: str) -> None:
    recorder = Recorder(answer(200, EMPTY_SEARCH))
    client = Client(kind, recorder)
    await client("search_slot_with_includes", extra_headers={"x-extra": "slot"})
    await client("search_schedule_with_includes", extra_headers={"x-extra": "schedule"})
    assert [r.headers["x-extra"] for r in recorder.requests] == ["slot", "schedule"]


@pytest.mark.anyio
async def test_the_helpers_answer_through_with_response_as_the_search_does(kind: str) -> None:
    response = await Client(kind, Recorder(answer(200, EMPTY_SEARCH))).with_response("search_slot_with_includes")
    assert (response.data, response.status, response.request_id) == (EMPTY_SEARCH, 200, REQUEST_ID)
    schedules = await Client(kind, Recorder(answer(200, EMPTY_SEARCH))).with_response("search_schedule_with_includes")
    assert schedules.status == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "keyword", "value"),
    [
        ("search_slot_with_includes", "include", "Slot:schedule"),
        ("search_slot_with_includes", "include_iterate", ["Schedule:actor"]),
        ("search_schedule_with_includes", "include", "Schedule:actor"),
        ("search_slot_with_includes", "include", None),
        ("search_slot_with_includes", "include_iterate", []),
    ],
)
async def test_python_refuses_an_include_passed_to_a_helper_and_nothing_is_sent(
    kind: str, method: str, keyword: str, value: object
) -> None:
    recorder = Recorder(answer(200, EMPTY_SEARCH))
    error = await Client(kind, recorder).raises(method, **{keyword: value})
    assert isinstance(error, TypeError)
    assert f"unexpected keyword argument '{keyword}'" in str(error)
    assert recorder.requests == []


def _served(op_id: str) -> list[str]:
    """Every include the pinned spec says a search serves, from its two include parameters' enums."""
    op = next(o["get"] for o in SPEC["paths"].values() if o.get("get", {}).get("operationId") == op_id)
    names: set[str] = set()
    for p in op["parameters"]:
        if p.get("name") in ("_include", "_include:iterate"):
            schema = p["schema"]
            names.update(schema.get("items", {}).get("enum") or schema.get("enum") or [])
    return sorted(names)


def test_each_helper_sends_every_include_its_search_serves_as_the_spec_lists_them() -> None:
    for op_id, (include, iterate) in generate.WITH_INCLUDES.items():
        assert sorted({*include, *iterate}) == _served(op_id), op_id
    assert len(_served("searchSlot")) == 4


def test_each_helper_takes_its_searchs_keywords_but_the_includes() -> None:
    import inspect

    for helper, search in (
        (haau3.Haau3.search_slot_with_includes, haau3.Haau3.search_slot),
        (haau3.AsyncHaau3.search_schedule_with_includes, haau3.AsyncHaau3.search_schedule),
    ):
        taken = set(inspect.signature(helper).parameters)
        assert taken == set(inspect.signature(search).parameters) - {"include", "include_iterate"}
