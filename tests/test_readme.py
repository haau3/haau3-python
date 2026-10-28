"""The README's Python blocks run, against answers the spec's examples give."""

from __future__ import annotations

import uuid
from typing import Any

import httpx2
import pytest
import readme_blocks

from .helpers import KEY, ROOT, SPEC, spec_example_handler


@pytest.fixture
def every_client_answers_from_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any client the README makes, its own or one it passes, answers as the spec's examples do."""
    monkeypatch.setenv("HAAU3_API_KEY", KEY)
    real_sync, real_async = httpx2.Client, httpx2.AsyncClient

    class SpecClient(real_sync):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx2.MockTransport(spec_example_handler))
            super().__init__(*args, **kwargs)

    class SpecAsyncClient(real_async):  # type: ignore[misc,valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx2.MockTransport(spec_example_handler))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx2, "Client", SpecClient)
    monkeypatch.setattr(httpx2, "AsyncClient", SpecAsyncClient)


def _arguments() -> dict[str, Any]:
    reconcile = SPEC["paths"]["/v1/fhir/r4/$reconcile-medications"]["post"]["requestBody"]["content"][
        "application/fhir+json"
    ]
    records = next(iter(reconcile["examples"].values()))["value"]
    check = SPEC["paths"]["/v1/fhir/r4/$process-message"]["post"]["requestBody"]["content"]["application/fhir+json"]
    message = next(iter(check["examples"].values()))["value"]
    sandbox = SPEC["paths"]["/v1/sandbox/fhir/r4/$process-message"]["post"]["requestBody"]["content"][
        "application/fhir+json"
    ]
    sandbox_message = next(iter(sandbox["examples"].values()))["value"]
    return {
        "plan_id": "H2406-013-000",
        "records": records,
        "message": message,
        "sandbox_message": sandbox_message,
        "codes": ["2345-7", "2160-0"],
        "job_id": str(uuid.uuid4()),
        "access_token": "eyJhbGciOiJub25lIn0.e30.example",
    }


def _module() -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(readme_blocks.module_source((ROOT / "README.md").read_text()), "README.md", "exec"), namespace)
    return namespace


def test_the_readme_has_blocks_to_run() -> None:
    found = readme_blocks.blocks((ROOT / "README.md").read_text())
    assert sum(b.run for b in found) >= 10
    assert any(b.awaits for b in found)
    assert [b.run for b in found].count(False) == 1


@pytest.mark.usefixtures("every_client_answers_from_the_spec")
def test_the_readmes_sync_blocks_run(capsys: pytest.CaptureFixture[str]) -> None:
    _module()["readme_sync"](**_arguments())
    printed = capsys.readouterr().out
    assert "AARP Medicare Advantage from UHC FL-0021 (PPO)" in printed


@pytest.mark.anyio
@pytest.mark.usefixtures("every_client_answers_from_the_spec")
async def test_the_readmes_async_blocks_run() -> None:
    await _module()["readme_async"](**_arguments())
