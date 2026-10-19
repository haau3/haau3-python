"""The production API, on a real key. Runs only with HAAU3_LIVE_TEST=1 and a key in HAAU3_API_KEY."""

from __future__ import annotations

import os
import uuid

import pytest

from haau3 import AsyncHaau3, BatchError, BatchOk, Haau3, RefusedError, calls

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("HAAU3_LIVE_TEST") == "1" and os.environ.get("HAAU3_API_KEY")),
        reason="set HAAU3_LIVE_TEST=1 and HAAU3_API_KEY to call the production API",
    ),
]


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


@pytest.mark.anyio
async def test_the_async_client_against_production(anyio_backend: str) -> None:
    async with AsyncHaau3() as client:
        plan = await client.read_insurance_plan("H2406-013-000")
        assert plan["resourceType"] == "InsurancePlan"
        publishers = await client.list_publishers()
        assert isinstance(publishers["publishers"], list)
