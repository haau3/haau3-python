"""A customer's code, type-checked against the built package under mypy and pyright, both strict.

Never run: CI reads it with both checkers, on Python 3.10, against the wheel. Each line carrying both
`# type: ignore[...]` and `# pyright: ignore[...]` is a mistake a type checker must catch, as
TypeScript's @ts-expect-error is: mypy's `warn_unused_ignores` and pyright's
`reportUnnecessaryTypeIgnoreComment` fail the check if it is not caught.
"""

from __future__ import annotations

from typing import Literal  # noqa: F401  (named in the assert_type strings below)

from typing_extensions import assert_type

import haau3
from haau3 import (
    AsyncHaau3,
    AuthError,
    BatchError,
    BatchOk,
    BatchResult,
    ForbiddenError,
    Haau3,
    Haau3Error,
    NoAnswerError,
    RawResponse,
    RefusedError,
    Response,
    calls,
    fhir,
    types,
)


def sync_tour(client: Haau3, records: types.ReconcileMedicationsBody) -> None:
    plan = client.read_insurance_plan("H2406-013-000")
    assert_type(plan, fhir.InsurancePlan)
    assert_type(plan.get("name"), "str | None")
    assert_type(plan["resourceType"], "Literal['InsurancePlan']")

    found = client.search_slots(state="MA", is_virtual=True, limit=10)
    assert_type(found, types.SearchSlotsResponse)
    assert_type(found["total"], int)
    assert_type(found["slots"][0]["start"], str)

    response = client.with_response.read_insurance_plan(
        "H2406-013-000", correlation_id="0b6f2d4e-8c1a-4e3b-9f5d-2a7c6e1b4d90"
    )
    assert_type(response, Response[fhir.InsurancePlan])
    assert_type(response.request_id, "str | None")
    assert_type(response.duration_ms, int)

    metadata = client.fhir_metadata()
    assert_type(metadata, "fhir.CapabilityStatement | fhir.TerminologyCapabilities")
    reconciled = client.reconcile_medications(records)
    assert_type(reconciled, fhir.Bundle)
    # Test data: what goes in is what comes out, so a call takes it as it takes the original.
    marked = haau3.mark_as_test_data(records)
    assert_type(marked, types.ReconcileMedicationsBody)
    assert_type(client.reconcile_medications(marked), fhir.Bundle)
    haau3.mark_as_test_data("not a resource")  # type: ignore[type-var]  # pyright: ignore[reportArgumentType]
    # Structured Sig: the builder gives what structure_dosage takes, and the readers need no client.
    structured = client.structure_dosage(
        haau3.structure_dosage_request(
            "Take 1 tablet daily", medication={"coding": [{"system": haau3.RXNORM_SYSTEM, "code": "197361"}]}
        )
    )
    assert_type(structured, fhir.Parameters)
    assert_type(haau3.parameter(structured, "dosage"), "fhir.ParametersParameter | None")
    assert_type(haau3.review_flags({"text": "Take 1 tablet daily"}), haau3.DosageReview)
    haau3.structure_dosage_request("Take 1 tablet daily", {"coding": []})  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
    # A record written inline is checked as the resource it is: a message's MessageHeader, and
    # MedList's records.
    client.process_message(
        {
            "resourceType": "Bundle",
            "type": "message",
            "entry": [
                {
                    "resource": {
                        "resourceType": "MessageHeader",
                        "eventCoding": {"code": "rtpbc-request-non-phi"},
                        "source": {"endpoint": "urn:example"},
                    }
                },
            ],
        }
    )
    misspelt: fhir.MessageHeader = {"resourceType": "MessageHeader", "source": {"endpoint": "e"}, "eventCodin": {}}  # type: ignore[typeddict-unknown-key]  # pyright: ignore[reportAssignmentType]
    incomplete: fhir.Claim = {"resourceType": "Claim", "status": "active"}  # type: ignore[typeddict-item]  # pyright: ignore[reportAssignmentType]
    _ = (misspelt, incomplete)
    client.reconcile_medications(
        {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {
                    "resource": {
                        "resourceType": "MedicationRequest",
                        "status": "active",
                        "intent": "order",
                        "subject": {"reference": "Patient/p"},
                    }
                }
            ],
        }
    )
    network = client.check_insurance_plan_network("H3359-038-000", npi=["1518929058"])
    assert_type(network, fhir.Parameters)

    plan_result, counties = client.batch(
        calls.read_insurance_plan("H2406-013-000"), calls.list_insurance_plan_counties(state="NC")
    )
    assert_type(plan_result, BatchResult[fhir.InsurancePlan])
    if isinstance(plan_result, BatchOk):
        assert_type(plan_result.data, fhir.InsurancePlan)
    else:
        assert_type(plan_result.error, "RefusedError | NoAnswerError")
    if plan_result.ok is True:
        assert_type(plan_result, BatchOk[fhir.InsurancePlan])
    match counties:
        case BatchOk(data=parameters):
            assert_type(parameters, fhir.Parameters)
        case BatchError(error=error):
            assert_type(error, "RefusedError | NoAnswerError")
    many = client.batch(*[calls.read_practitioner(i) for i in ("a", "b")])
    assert_type(many, "tuple[BatchResult[fhir.Practitioner], ...]")
    with_meta = client.with_response.batch(calls.read_location("x"))
    assert_type(with_meta.data, "tuple[BatchResult[fhir.Location]]")

    query: types.SearchInsurancePlanQuery = {"name_contains": "Humana", "count": 10}
    first = client.search_insurance_plan(**query)
    for page in client.pages(first):
        assert_type(page, fhir.Bundle)
        for entry in page.get("entry", []):
            resource = entry.get("resource")
            if resource is not None and resource["resourceType"] == "InsurancePlan":
                assert_type(resource, fhir.InsurancePlan)
    # resources() yields each found resource, narrowed by its resourceType with no cast.
    for each in client.resources(first):
        assert_type(each, fhir.Resource)
        if each["resourceType"] == "InsurancePlan":
            assert_type(each, fhir.InsurancePlan)

    raw = client.request("GET", "/v1/scheduling/publishers", query={"includePast": True})
    assert_type(raw, RawResponse)
    assert_type(raw.body, object)

    try:
        client.read_questionnaire("44249-1")
    except RefusedError as refused:
        assert_type(refused.status, int)
        assert_type(refused.outcome, "fhir.OperationOutcome | None")
    except NoAnswerError as unanswered:
        assert_type(unanswered.status, "int | None")
        assert_type(unanswered.reason, haau3.NoAnswerReason)
    except Haau3Error as failure:
        assert_type(failure.to_dict(), haau3.ErrorDict)
        assert_type(failure.request_id, "str | None")

    # Mistakes a type checker must catch.
    _ = calls.reconcile_medications  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
    _ = calls.search_slots  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
    client.check_insurance_plan_network("H3359-038-000")  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
    client.search_slots("MA")  # type: ignore[call-arg, arg-type]  # pyright: ignore[reportCallIssue]
    client.search_slots(status="maybe")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    client.read_insurance_plan(123)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    # pyright alone refuses a key FHIR leaves optional; mypy lets it through, so read it with .get().
    _ = plan["name"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    client.search_actors()  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    client.batch({"method": "GET", "url": "x"})  # type: ignore[call-overload]  # pyright: ignore[reportCallIssue, reportArgumentType]


async def async_tour(client: AsyncHaau3) -> None:
    plan = await client.read_insurance_plan("H2406-013-000")
    assert_type(plan, fhir.InsurancePlan)
    response = await client.with_response.search_practitioner(identifier="1093209884")
    assert_type(response, Response[fhir.Bundle])
    (only,) = await client.batch(calls.read_organization("x"))
    assert_type(only, BatchResult[fhir.Organization])
    async for page in client.pages(response.data):
        assert_type(page, fhir.Bundle)
    async for each in client.resources(response.data):
        assert_type(each, fhir.Resource)
    async with AsyncHaau3() as scoped:
        assert_type(await scoped.list_publishers(), types.ListPublishersResponse)
    _ = client.read_insurance_plan("x").get("name")  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]


def context_manager() -> None:
    with Haau3() as client:
        assert_type(client, Haau3)
    assert_type(haau3.__version__, str)
    assert_type(haau3.SPEC_VERSION, str)


def a_403_is_not_an_auth_error(forbidden: ForbiddenError, auth: AuthError) -> Haau3Error:
    assert_type(forbidden.code, "str | None")
    assert_type(forbidden.status, int)
    wrong: AuthError = forbidden  # type: ignore[assignment]  # pyright: ignore[reportAssignmentType]
    return wrong if auth.status == 401 else forbidden
