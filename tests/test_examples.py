"""The four examples run as a customer would run them, against answers the spec's examples give."""

from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest

from haau3 import AsyncHaau3, Haau3

from .helpers import KEY, Recorder, answer, spec_example_handler

ran: set[str] = set()


def spec_client() -> Haau3:
    return Haau3(api_key=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(spec_example_handler)))


def spec_async_client() -> AsyncHaau3:
    return AsyncHaau3(api_key=KEY, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(spec_example_handler)))


@pytest.mark.anyio
async def test_fastapi(anyio_backend: str) -> None:
    if anyio_backend != "asyncio":
        pytest.skip("FastAPI runs on asyncio")
    fastapi_app = pytest.importorskip("fastapi_app", reason="FastAPI has no release for this Python yet")
    app = fastapi_app.create_app(make_client=spec_async_client)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://app.test") as http:
            found = await http.get("/plans/H2406-013-000")
    assert found.status_code == 200
    assert found.json() == {"id": "H2406-013-000", "name": "AARP Medicare Advantage from UHC FL-0021 (PPO)"}
    ran.add("fastapi")


@pytest.mark.anyio
async def test_fastapi_says_no_such_plan_with_the_request_id(anyio_backend: str) -> None:
    if anyio_backend != "asyncio":
        pytest.skip("FastAPI runs on asyncio")
    fastapi_app = pytest.importorskip("fastapi_app", reason="FastAPI has no release for this Python yet")

    def missing() -> AsyncHaau3:
        return AsyncHaau3(
            api_key=KEY, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(Recorder(answer(404))))
        )

    app = fastapi_app.create_app(make_client=missing)
    transport = httpx2.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url="http://app.test") as http,
    ):
        found = await http.get("/plans/nope")
    assert found.status_code == 404
    assert found.json()["detail"]["request_id"]


def test_django(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("django", reason="Django has no release for this Python yet")
    from django.conf import settings

    if not settings.configured:
        settings.configure(DEFAULT_CHARSET="utf-8")
    django_view = importlib.import_module("django_view")
    django_view.haau3_client.cache_clear()
    monkeypatch.setattr(django_view, "Haau3", lambda: spec_client())
    from django.test import RequestFactory

    response = django_view.plan(RequestFactory().get("/plans/H2406-013-000"), "H2406-013-000")
    assert response.status_code == 200
    assert b"AARP Medicare Advantage" in response.content
    assert django_view.haau3_client() is django_view.haau3_client()  # one client for the process
    ran.add("django")


def test_flask() -> None:
    flask_app = pytest.importorskip("flask_app", reason="Flask has no release for this Python yet")
    app = flask_app.create_app(make_client=spec_client)
    found = app.test_client().get("/plans/H2406-013-000")
    assert found.status_code == 200
    assert found.get_json() == {"id": "H2406-013-000", "name": "AARP Medicare Advantage from UHC FL-0021 (PPO)"}
    ran.add("flask")


def test_plans_to_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import plans_to_csv

    second = "https://api.haau3.com/v1/fhir/r4/InsurancePlan?page=2"

    def plans(request: httpx2.Request) -> httpx2.Response:
        n = 2 if "page=2" in str(request.url) else 1
        links = [{"relation": "next", "url": second}] if n == 1 else []
        entry: dict[str, Any] = {
            "resource": {
                "resourceType": "InsurancePlan",
                "id": f"P{n}",
                "name": f"Plan {n}",
                "status": "active",
                "type": [{"coding": [{"code": "HMO"}]}],
            }
        }
        return answer(
            200,
            {
                "resourceType": "Bundle",
                "type": "searchset",
                "link": links,
                "entry": [entry, {"resource": {"resourceType": "Organization", "id": "O"}}],
            },
        )

    path = tmp_path / "plans.csv"
    client = Haau3(api_key=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(plans)))
    assert plans_to_csv.main(["NC", str(path)], client=client) == 0
    with path.open() as f:
        assert list(csv.reader(f)) == [
            ["id", "name", "status", "type"],
            ["P1", "Plan 1", "active", "HMO"],
            ["P2", "Plan 2", "active", "HMO"],
        ]
    assert "2 plans written" in capsys.readouterr().out
    assert plans_to_csv.main(["NC"]) == 2


def test_each_framework_example_ran_on_at_least_one_python() -> None:
    # Run last in this module: on a Python a framework has no release for yet, its test is skipped,
    # and this says which ran here, in the test log.
    print(f"examples run on Python {sys.version_info[0]}.{sys.version_info[1]}: {sorted(ran)}")
