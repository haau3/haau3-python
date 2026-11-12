"""The Sandbox, on a test key or with no key at all. Runs only with HAAU3_LIVE_TEST=1:

    HAAU3_LIVE_TEST=1 HAAU3_API_KEY=haau3_sk_test_... uv run pytest tests/test_live_sandbox.py
    HAAU3_LIVE_TEST=1 uv run pytest tests/test_live_sandbox.py

With no key the Sandbox allows a few requests an hour from one address, so these are three calls."""

from __future__ import annotations

import os

import pytest

from haau3 import Haau3

from .helpers import example_body, spec_operation

KEY = os.environ.get("HAAU3_API_KEY", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("HAAU3_LIVE_TEST") == "1" and (not KEY or KEY.startswith("haau3_sk_test_"))),
        reason="set HAAU3_LIVE_TEST=1, with a test key in HAAU3_API_KEY or none, to call the Sandbox",
    ),
]


def test_the_sandbox_answers_on_the_host_its_key_or_environment_picks() -> None:
    with Haau3() if KEY else Haau3(environment="sandbox") as client:
        assert client.environment == "sandbox"
        assert client.base_url == "https://sandbox.haau3.com"
        statement = client.fhir_metadata()
        assert statement["resourceType"] == "CapabilityStatement"
        assert statement.get("implementation", {}).get("url") == "https://sandbox.haau3.com/v1/fhir/r4"
        with pytest.warns(DeprecationWarning, match=r"^Call `fhir_metadata` on the Sandbox's own host"):
            assert client.sandbox_metadata() == statement
        answer = client.process_message(example_body(spec_operation("POST", "/v1/fhir/r4/$process-message")))
        assert answer["resourceType"] == "Bundle"
        assert answer.get("type") == "message"
