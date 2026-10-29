"""The code samples in openapi/code-samples.json, one per operation, each run as it is written: a program
of its own, with HAAU3_BASE_URL pointing at a stand-in API that answers only the operation's example
request, exactly as the spec writes it, and refuses anything else.

Against the production API too, on a real key, when asked, as tests/test_live.py is:

    HAAU3_LIVE_TEST=1 HAAU3_API_KEY=haau3_sk_... uv run pytest tests/test_samples.py

There each sample runs with no address set, as a customer runs it, one at a time. What it must do is
set by the key's mode, read from its prefix: a live key (`haau3_sk_live_`) is answered everywhere but the
Sandbox and refused there, a test key (`haau3_sk_test_`) the reverse, each refusal a ForbiddenError
whose message gives its reason. The live key comes from an organization with no agreement on file, which
Pharmacy Benefits refuses, since it is not yet open. A key with neither prefix works on both bases, and
its run leaves Pharmacy Benefits out. CI sets neither variable."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import pytest

from .helpers import KEY, ROOT, answers_fhir, example_answer, example_body, operations

OVERLAY: dict[str, Any] = json.loads((ROOT / "openapi" / "code-samples.json").read_text(encoding="utf-8"))
OPERATIONS = list(operations())


def _target(method: str, path: str) -> str:
    return f"$.paths['{path}'].{method.lower()}"


def _sample(method: str, path: str) -> str:
    action = next(a for a in OVERLAY["actions"] if a["target"] == _target(method, path))
    return str(action["update"]["x-codeSamples"][0]["source"])


def _key(method: str, path: str, query: str) -> str:
    """A request's method, path and query, in a form two requests can be compared by."""
    values: dict[str, list[str]] = {}
    for name, value in parse_qsl(query, keep_blank_values=True):
        values.setdefault(name, []).append(value)
    return f"{method} {unquote(path)} {json.dumps(sorted(values.items()))}"


def _example_key(method: str, path: str, op: dict[str, Any]) -> str:
    filled = path
    query: list[tuple[str, str]] = []
    for p in op.get("parameters", []):
        if "$ref" in p or "example" not in p:
            continue
        if p["in"] == "path":
            filled = filled.replace("{" + p["name"] + "}", str(p["example"]))
        elif p["in"] == "query":
            for value in p["example"] if isinstance(p["example"], list) else [p["example"]]:
                query.append((p["name"], str(value).lower() if isinstance(value, bool) else str(value)))
    from urllib.parse import urlencode

    return _key(method, filled, urlencode(query))


def _batch_keys(bundle: dict[str, Any]) -> list[str]:
    keys = []
    for entry in bundle["entry"]:
        url = urlsplit(urljoin("https://base.invalid/v1/fhir/r4/", entry["request"]["url"]))
        keys.append(_key(entry["request"]["method"], url.path, url.query))
    return keys


def _expected_output(op_id: str, op: dict[str, Any]) -> str:
    """What a sample prints for its example answer, by the rule the generator writes."""
    answer = example_answer(op)
    if op_id == "fhirBatch":
        return "\n".join(str(int(re.match(r"\d+", e["response"]["status"]).group())) for e in answer["entry"])  # type: ignore[union-attr]
    if op_id == "fhirMetadata" or answer.get("resourceType") == "CapabilityStatement":
        return str(answer["resourceType"])
    if answer.get("resourceType") == "Bundle":
        return str(len(answer.get("entry", [])))
    if answer.get("resourceType") == "Parameters":
        return "\n".join(p["name"] for p in answer["parameter"])
    if "resourceType" in answer:
        return str(answer["id"])
    if "total" in answer:
        return str(answer["total"])
    return str(len(next(v for v in answer.values() if isinstance(v, list))))


@pytest.fixture(scope="module")
def stand_in() -> Iterator[str]:
    answers: dict[str, tuple[Any, bool, Any]] = {}
    batch_op: dict[str, Any] = {}
    for op_id, method, path, op in OPERATIONS:
        if op_id == "fhirBatch":
            batch_op = op
            continue
        body = example_body(op) if "requestBody" in op else None
        answers[_example_key(method, path, op)] = (example_answer(op), answers_fhir(op), body)
    batch_keys = _batch_keys(example_body(batch_op))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: Any, fhir: bool) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/fhir+json" if fhir else "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, why: str) -> None:
            outcome = {
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "invalid", "diagnostics": why}],
            }
            self._send(400, outcome, True)

        def _answer(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            sent = json.loads(self.rfile.read(length)) if length else None
            url = urlsplit(self.path)
            found = answers.get(_key(self.command, url.path, url.query))
            if self.headers.get("authorization") != f"Bearer {KEY}":
                self._refuse("no key")
            elif self.command == "POST" and url.path == "/v1/fhir/r4":
                if isinstance(sent, dict) and _batch_keys(sent) == batch_keys:
                    self._send(200, example_answer(batch_op), True)
                else:
                    self._refuse("not the example batch")
            elif found is None:
                self._refuse("not an example request")
            elif self.command == "POST" and sent != found[2]:
                self._refuse("not the example body")
            else:
                self._send(200, found[0], found[1])

        do_GET = _answer
        do_POST = _answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _run(
    source: str, base_url: str | None, files: dict[str, str] | None = None, key: str = KEY
) -> subprocess.CompletedProcess[str]:
    """Runs one sample as a program of its own, as a customer would. With no base URL, on the API itself."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sample.py").write_text(source)
        for name, text in (files or {}).items():
            (Path(tmp) / name).write_text(text)
        env = {"PATH": os.environ.get("PATH", ""), "HAAU3_API_KEY": key}
        if base_url is not None:
            env["HAAU3_BASE_URL"] = base_url
        return subprocess.run(
            [sys.executable, "sample.py"], cwd=tmp, env=env, capture_output=True, text=True, timeout=60, check=False
        )


def test_the_overlay_is_for_the_spec_it_was_generated_from() -> None:
    from .helpers import SPEC

    assert OVERLAY["overlay"] == "1.2.0"
    assert OVERLAY["info"]["version"] == SPEC["info"]["version"]
    assert OVERLAY["extends"] == "https://api.haau3.com/openapi.json"


def test_it_adds_one_python_sample_to_every_operation_in_the_specs_order() -> None:
    assert [a["target"] for a in OVERLAY["actions"]] == [_target(m, p) for _, m, p, _ in OPERATIONS]
    for action in OVERLAY["actions"]:
        assert list(action["update"]) == ["x-codeSamples"]
        [sample] = action["update"]["x-codeSamples"]
        assert sample["lang"] == "python"
        assert sample["label"] == "Python"


def test_every_sample_reads_the_key_from_the_environment_and_sets_no_address() -> None:
    for _, method, path, _ in OPERATIONS:
        source = _sample(method, path)
        assert "client = Haau3()  # reads HAAU3_API_KEY" in source
        assert not re.search(r"api_key|base_url|haau3_sk_", source)


@pytest.mark.parametrize(("op_id", "method", "path", "op"), OPERATIONS, ids=[o[0] for o in OPERATIONS])
def test_each_sample_sends_its_example_request_and_prints_what_came_back(
    stand_in: str, op_id: str, method: str, path: str, op: dict[str, Any]
) -> None:
    source = _sample(method, path)
    files = {"request.json": json.dumps(example_body(op))} if 'open("request.json")' in source else None
    result = _run(source, stand_in, files)
    assert result.stderr == ""
    assert result.returncode == 0
    assert result.stdout.rstrip("\n") == _expected_output(op_id, op)


def test_a_sample_that_sends_anything_else_is_refused(stand_in: str) -> None:
    """The stand-in's control: one sample with another id fails."""
    source = _sample("GET", "/v1/fhir/r4/InsurancePlan/{id}")
    changed = re.sub(r'read_insurance_plan\("[^"]+"\)', 'read_insurance_plan("H0000-000-000")', source)
    assert changed != source
    result = _run(changed, stand_in)
    assert result.returncode != 0
    assert "RefusedError" in result.stderr


LIVE = os.environ.get("HAAU3_LIVE_TEST") == "1" and bool(os.environ.get("HAAU3_API_KEY"))
LIVE_KEY = os.environ.get("HAAU3_API_KEY", "")
KEY_MODE = (
    "live" if LIVE_KEY.startswith("haau3_sk_live_") else "test" if LIVE_KEY.startswith("haau3_sk_test_") else "either"
)


def _on_this_key(op_id: str, path: str) -> tuple[str, str | None] | None:
    """What a sample must do on this key: ("answers", None), or ("refused", its code or None); None when not run."""
    sandbox = path.startswith("/v1/sandbox/")
    if KEY_MODE == "test" and not sandbox:
        return ("refused", "test-key-outside-sandbox")
    if KEY_MODE == "live" and sandbox:
        return ("refused", "live-key-in-sandbox")
    if op_id == "processMessage":
        return ("refused", None) if KEY_MODE == "live" else None
    return ("answers", None)


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set HAAU3_LIVE_TEST=1 and HAAU3_API_KEY to call the production API")
@pytest.mark.parametrize(("op_id", "method", "path", "op"), OPERATIONS, ids=[o[0] for o in OPERATIONS])
def test_each_sample_against_production(op_id: str, method: str, path: str, op: dict[str, Any]) -> None:
    from haau3._judge import _FORBIDDEN_GENERAL, _FORBIDDEN_REASONS

    expected = _on_this_key(op_id, path)
    if expected is None:
        pytest.skip("a key with neither prefix may belong to an organization Pharmacy Benefits is open to")
    source = _sample(method, path)
    files = {"request.json": json.dumps(example_body(op))} if 'open("request.json")' in source else None
    result = _run(source, None, files, LIVE_KEY)
    outcome, code = expected
    if outcome == "answers":
        assert result.stderr == ""
        assert result.returncode == 0
        assert result.stdout.strip() != ""
        # A search that finds nothing still answers: its example found something, so this must too.
        if _expected_output(op_id, op) != "0":
            assert result.stdout.strip() != "0"
    else:
        assert result.returncode != 0
        assert "ForbiddenError" in result.stderr
        assert (_FORBIDDEN_REASONS[code] if code else _FORBIDDEN_GENERAL) in result.stderr
