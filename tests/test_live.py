"""The production API, on a real key. Runs only with HAAU3_LIVE_TEST=1 and a key in HAAU3_API_KEY.

The calls here are the API's services outside the Sandbox, so they skip on a test key (`haau3_sk_test_`),
whose calls tests/test_samples.py makes. The patient-data cases need a live key (`haau3_sk_live_`) from
an organization with no agreement on file, which is refused real patient data."""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from haau3 import AsyncHaau3, BatchError, BatchOk, ForbiddenError, Haau3, RefusedError, calls, mark_as_test_data

from .helpers import example_body, spec_operation

KEY = os.environ.get("HAAU3_API_KEY", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("HAAU3_LIVE_TEST") == "1" and KEY),
        reason="set HAAU3_LIVE_TEST=1 and HAAU3_API_KEY to call the production API",
    ),
]

not_on_a_test_key = pytest.mark.skipif(
    KEY.startswith("haau3_sk_test_"), reason="a test key works only in the Sandbox: tests/test_samples.py calls it"
)
on_a_live_key = pytest.mark.skipif(
    not KEY.startswith("haau3_sk_live_"), reason="needs a live key from an organization with no agreement on file"
)


def _without_test_labels(value: Any) -> Any:
    """A copy with every `HTEST` label taken off, so made-up records read as real patient data."""
    if isinstance(value, list):
        return [_without_test_labels(v) for v in value]
    if not isinstance(value, dict):
        return value
    copy = {k: _without_test_labels(v) for k, v in value.items()}
    meta = copy.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("security"), list):
        security = [c for c in meta["security"] if c.get("code") != "HTEST"]
        meta = {**meta, "security": security}
        if not security:
            del meta["security"]
        if meta:
            copy["meta"] = meta
        else:
            del copy["meta"]
    return copy


# MedList's example request from the spec: made-up records, here with their test-data labels off.
RECORDS = _without_test_labels(example_body(spec_operation("POST", "/v1/fhir/r4/$reconcile-medications")))


@on_a_live_key
def test_made_up_records_that_mark_as_test_data_labels_are_answered() -> None:
    assert "HTEST" not in str(RECORDS)
    with Haau3() as client:
        listed = client.reconcile_medications(mark_as_test_data(RECORDS))
        assert listed["resourceType"] == "Bundle"


@on_a_live_key
def test_the_same_records_unlabelled_are_refused_naming_the_agreement() -> None:
    with Haau3() as client, pytest.raises(ForbiddenError) as raised:
        client.reconcile_medications(RECORDS)
    assert raised.value.code == "phi-agreement-required"


@not_on_a_test_key
def test_every_kind_of_call_against_production() -> None:
    with Haau3() as client:
        found = client.search_slots(state="MA", limit=3)
        assert isinstance(found["total"], int)
        plan = client.read_insurance_plan("H2406-013-000")
        assert plan["resourceType"] == "InsurancePlan"
        glucose = client.code_system_lookup(system="http://loinc.org", code="2345-7")
        assert glucose["resourceType"] == "Parameters"
        first = client.search_insurance_plan(coverage_area_state="NC", count=2)
        pages = []
        for page in client.pages(first):
            pages.append(page)
            if len(pages) == 2:
                break
        assert len(pages) == 2
        plan_result, missing = client.batch(
            calls.read_insurance_plan("H2406-013-000"), calls.read_insurance_plan("no-such-plan")
        )
        assert isinstance(plan_result, BatchOk)
        assert isinstance(missing, BatchError)
        assert isinstance(missing.error, RefusedError)
        job = str(uuid.uuid4())
        response = client.with_response.read_insurance_plan("H2406-013-000", correlation_id=job)
        assert response.correlation_id == job
        assert response.request_id
        with pytest.raises(RefusedError) as raised:
            client.read_insurance_plan("no-such-plan")
        assert raised.value.status == 404
        assert raised.value.request_id


@not_on_a_test_key
@pytest.mark.anyio
async def test_the_async_client_against_production(anyio_backend: str) -> None:
    async with AsyncHaau3() as client:
        plan = await client.read_insurance_plan("H2406-013-000")
        assert plan["resourceType"] == "InsurancePlan"
        publishers = await client.list_publishers()
        assert isinstance(publishers["publishers"], list)
