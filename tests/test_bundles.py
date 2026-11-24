"""matches() and resolve(): a page's matches, and what a reference names in a Bundle, with no request."""

from __future__ import annotations

from typing import Any

import pytest

import haau3
from haau3 import AsyncHaau3, Haau3, Haau3Error

from .helpers import Recorder, async_client, sync_client

BASE = "https://api.haau3.com/v1/fhir/r4"

# A pharmacy search as the API answers one: the matched service first, then what _include brought, each
# at its RESTful address, the service naming the others by relative reference.
SERVICE: Any = {
    "resourceType": "HealthcareService",
    "id": "HealthcareService-pharmacy-1234567893",
    "name": "EXAMPLE PHARMACY",
    "providedBy": {"reference": "Organization/Organization-1234567893"},
    "location": [{"reference": "Location/Location-pharmacy-1234567893"}],
}
ORGANIZATION: Any = {"resourceType": "Organization", "id": "Organization-1234567893", "name": "EXAMPLE PHARMACY"}
STORE: Any = {"resourceType": "Location", "id": "Location-pharmacy-1234567893", "address": {"city": "MIAMI"}}


def page() -> Any:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {"fullUrl": f"{BASE}/HealthcareService/{SERVICE['id']}", "resource": SERVICE, "search": {"mode": "match"}},
            {
                "fullUrl": f"{BASE}/Organization/{ORGANIZATION['id']}",
                "resource": ORGANIZATION,
                "search": {"mode": "include"},
            },
            {"fullUrl": f"{BASE}/Location/{STORE['id']}", "resource": STORE, "search": {"mode": "include"}},
            {"resource": {"resourceType": "OperationOutcome", "issue": []}, "search": {"mode": "outcome"}},
        ],
    }


@pytest.fixture
def recorder() -> Recorder:
    """A transport that fails any request, and records it: resolve() and matches() send nothing."""
    return Recorder(AssertionError("resolve() and matches() send nothing"))


@pytest.fixture
def client(recorder: Recorder) -> Haau3:
    return sync_client(recorder)


# --- matches() -------------------------------------------------------------------------------------


def test_matches_gives_the_pages_matches_of_a_type_and_no_included_resource_or_outcome(
    client: Haau3, recorder: Recorder
) -> None:
    assert [r.get("id") for r in client.matches(page(), "HealthcareService")] == [SERVICE["id"]]
    assert client.matches(page(), "Organization") == []
    assert recorder.requests == []


def test_matches_gives_every_match_without_a_type_an_entry_that_says_no_mode_among_them(client: Haau3) -> None:
    bundle: Any = page()
    bundle["entry"].append({"resource": {"resourceType": "Organization", "id": "no-mode"}})
    assert [f"{r['resourceType']}/{r.get('id')}" for r in client.matches(bundle)] == [
        f"HealthcareService/{SERVICE['id']}",
        "Organization/no-mode",
    ]


def test_matches_gives_the_resources_the_page_holds_not_copies(client: Haau3) -> None:
    bundle: Any = page()
    assert client.matches(bundle, "HealthcareService")[0] is bundle["entry"][0]["resource"]


# --- resolve() -------------------------------------------------------------------------------------


def test_resolve_finds_what_a_relative_reference_names_at_the_address_of_the_entry_holding_it(
    client: Haau3, recorder: Recorder
) -> None:
    bundle: Any = page()
    [found] = client.matches(bundle, "HealthcareService")
    assert client.resolve(bundle, found.get("providedBy"), from_=found, type="Organization") is ORGANIZATION
    assert client.resolve(bundle, found.get("location", [])[0], from_=found, type="Location") is STORE
    assert recorder.requests == []


def test_resolve_finds_it_without_from_as_the_one_entry_whose_address_ends_in_that_type_and_id(client: Haau3) -> None:
    assert client.resolve(page(), "Organization/Organization-1234567893") is ORGANIZATION
    # The control: an id the page does not hold.
    assert client.resolve(page(), "Organization/Organization-not-on-this-page") is None


def test_resolve_refuses_to_choose_between_two_addresses_ending_in_the_same_type_and_id_unless_from_says_which(
    client: Haau3,
) -> None:
    elsewhere: Any = {"resourceType": "Organization", "id": ORGANIZATION["id"], "name": "ANOTHER SERVER'S COPY"}
    bundle: Any = page()
    bundle["entry"].append(
        {"fullUrl": f"https://fhir.example.org/r4/Organization/{ORGANIZATION['id']}", "resource": elsewhere}
    )
    with pytest.raises(Haau3Error, match="ambiguous"):
        client.resolve(bundle, SERVICE["providedBy"])
    assert client.resolve(bundle, SERVICE["providedBy"], from_=SERVICE) is ORGANIZATION


def test_resolve_matches_an_absolute_reference_to_the_entry_with_that_full_url_a_urn_uuid_among_them(
    client: Haau3,
) -> None:
    header: Any = {
        "resourceType": "MessageHeader",
        "focus": [{"reference": "urn:uuid:5f0c4a52-0d1e-4b7a-9c2f-3e4d5a6b7c8d"}],
    }
    claim: Any = {"resourceType": "Claim", "id": "c"}
    message: Any = {
        "resourceType": "Bundle",
        "type": "message",
        "entry": [
            {"fullUrl": "urn:uuid:1a2b3c4d-0000-4000-8000-000000000001", "resource": header},
            {"fullUrl": "urn:uuid:5f0c4a52-0d1e-4b7a-9c2f-3e4d5a6b7c8d", "resource": claim},
        ],
    }
    assert client.resolve(message, header["focus"][0], type="Claim") is claim
    assert client.resolve(page(), f"{BASE}/Location/{STORE['id']}") is STORE
    assert client.resolve(message, "urn:uuid:00000000-0000-4000-8000-00000000ffff") is None


def test_resolve_gives_a_relative_reference_no_meaning_from_an_entry_at_a_urn_uuid_as_fhir_says(client: Haau3) -> None:
    holder: Any = {"resourceType": "Claim", "provider": {"reference": "Organization/Organization-1234567893"}}
    bundle: Any = page()
    bundle["entry"].append({"fullUrl": "urn:uuid:1a2b3c4d-0000-4000-8000-000000000002", "resource": holder})
    assert client.resolve(bundle, holder["provider"], from_=holder) is None
    # The control: the same reference without from_ finds the one RESTful entry.
    assert client.resolve(bundle, holder["provider"]) is ORGANIZATION


def test_resolve_refuses_a_from_that_is_not_an_entry_of_the_bundle_even_one_equal_to_an_entry(client: Haau3) -> None:
    with pytest.raises(Haau3Error, match="not an entry of this Bundle"):
        copied: Any = dict(SERVICE)
        client.resolve(page(), SERVICE["providedBy"], from_=copied)


def test_resolve_finds_a_contained_resource_in_from_and_refuses_to_guess_without_it(client: Haau3) -> None:
    pharmacy: Any = {"resourceType": "Organization", "id": "pharmacy"}
    holder: Any = {"resourceType": "Claim", "contained": [pharmacy], "provider": {"reference": "#pharmacy"}}
    bundle: Any = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": holder}]}
    provider: Any = holder["provider"]
    assert client.resolve(bundle, provider, from_=holder) is pharmacy
    assert client.resolve(bundle, "#", from_=holder) is holder
    assert client.resolve(bundle, "#elsewhere", from_=holder) is None
    with pytest.raises(Haau3Error, match="pass from_"):
        client.resolve(bundle, provider)


def test_resolve_matches_a_version_to_meta_version_id(client: Haau3) -> None:
    v1: Any = {"resourceType": "Organization", "id": "o", "meta": {"versionId": "1"}}
    v2: Any = {"resourceType": "Organization", "id": "o", "meta": {"versionId": "2"}}
    history: Any = {
        "resourceType": "Bundle",
        "type": "history",
        "entry": [
            {"fullUrl": f"{BASE}/Organization/o", "resource": v2},
            {"fullUrl": f"{BASE}/Organization/o", "resource": v1},
            # A deleted version holds no resource, and names nothing.
            {"fullUrl": f"{BASE}/Organization/o", "request": {"method": "DELETE", "url": "Organization/o"}},
        ],
    }
    assert client.resolve(history, f"{BASE}/Organization/o/_history/1") is v1
    assert client.resolve(history, "Organization/o/_history/2") is v2
    assert client.resolve(history, "Organization/o/_history/3") is None
    # The control: with no version, both versions answer, and which is meant is ambiguous.
    with pytest.raises(Haau3Error, match="ambiguous"):
        client.resolve(history, f"{BASE}/Organization/o")


def test_resolve_refuses_a_resource_of_another_type_than_the_one_asked_for(client: Haau3) -> None:
    with pytest.raises(Haau3Error) as raised:
        client.resolve(page(), SERVICE["providedBy"], type="Location")
    assert raised.value.message == "The reference names a resource of type Organization, not Location."


def test_resolve_is_none_for_no_reference_an_identifier_alone_and_a_reference_fhir_gives_no_meaning(
    client: Haau3,
) -> None:
    assert client.resolve(page(), None) is None
    by_identifier: Any = {"identifier": {"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567893"}}
    assert client.resolve(page(), by_identifier) is None
    assert client.resolve(page(), "not a reference") is None
    assert client.resolve(page(), "") is None


def test_resolve_never_names_the_reference_in_an_error_so_a_message_is_safe_to_log(client: Haau3) -> None:
    bundle: Any = page()
    bundle["entry"].append(
        {"fullUrl": f"https://fhir.example.org/r4/Organization/{ORGANIZATION['id']}", "resource": dict(ORGANIZATION)}
    )
    with pytest.raises(Haau3Error) as raised:
        client.resolve(bundle, SERVICE["providedBy"])
    assert "ambiguous" in raised.value.message
    assert "1234567893" not in raised.value.message


# --- the same, as plain functions and on the async client -------------------------------------------


def test_the_plain_functions_and_the_async_clients_methods_give_the_same(recorder: Recorder) -> None:
    async_haau3: AsyncHaau3 = async_client(recorder)
    bundle: Any = page()
    for reader in (haau3, async_haau3):
        assert reader.matches(bundle, "HealthcareService") == [SERVICE]
        assert reader.resolve(bundle, SERVICE["providedBy"], from_=SERVICE, type="Organization") is ORGANIZATION
    assert recorder.requests == []


def test_resolve_matches_a_reference_as_a_whole_string(client: Haau3) -> None:
    """`$` in Python also matches before a final newline; a reference with one names nothing."""
    assert client.resolve(page(), "Organization/Organization-1234567893\n") is None
    assert client.resolve(page(), f"{BASE}/Location/{STORE['id']}\n") is None
    assert client.resolve(page(), f"{BASE}/Location/{STORE['id']}/_history/1\n") is None
    # The control: the same reference without the newline.
    assert client.resolve(page(), "Organization/Organization-1234567893") is ORGANIZATION
    # A versioned absolute reference, where a prefix would name a version the page holds.
    v1: Any = {"resourceType": "Organization", "id": "o", "meta": {"versionId": "1"}}
    history: Any = {
        "resourceType": "Bundle",
        "type": "history",
        "entry": [{"fullUrl": f"{BASE}/Organization/o", "resource": v1}],
    }
    assert client.resolve(history, f"{BASE}/Organization/o/_history/1\n") is None
    assert client.resolve(history, f"{BASE}/Organization/o/_history/1") is v1
    # And an entry whose address has one is at no RESTful address.
    odd: Any = {"resourceType": "Bundle", "entry": [{"fullUrl": f"{BASE}/Organization/o\n", "resource": v1}]}
    assert client.resolve(odd, "Organization/o") is None


def test_matches_and_resolve_pass_over_what_is_not_a_fhir_shape(client: Haau3) -> None:
    bundle = page()
    bundle["entry"] += [None, {"resource": "not a resource"}, {"resource": ORGANIZATION, "search": None}]
    assert [r.get("id") for r in client.matches(bundle)] == [SERVICE["id"], ORGANIZATION["id"]]
    odd: Any = {"resourceType": "Claim", "contained": None, "provider": {"reference": "#x"}}
    bundle["entry"].append({"resource": odd})
    assert client.resolve(bundle, odd["provider"], from_=odd) is None
    not_a_reference: Any = ["Organization/Organization-1234567893"]
    assert client.resolve(bundle, not_a_reference) is None
    no_entries: Any = {"resourceType": "Bundle", "entry": None}
    assert client.matches(no_entries) == []
