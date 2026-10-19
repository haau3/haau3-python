"""A request is written byte for byte as the TypeScript SDK writes it, and a wire name maps to one Python name."""

from __future__ import annotations

import json
import shutil
import subprocess

import naming
import pytest

from haau3._encode import encode_path_value, encode_query, url_on_base
from haau3._errors import Haau3Error

SAMPLES = [
    "Blue Cross & co",
    "a~b*c-d_e.f",
    "1992700001,1992700002",
    "name:contains",
    "x|y/z?w#v",
    "http://loinc.org",
    "café ünïcode",
    "emoji 🙂",
    "lone \ud800 surrogate",
    "!'()*",
    "+plus%percent",
    "",
]


def test_query_values_go_out_as_url_search_params_writes_them() -> None:
    assert encode_query([("name:contains", "Blue Cross & co")]) == "name%3Acontains=Blue+Cross+%26+co"
    assert encode_query([("q", "a~b*c")]) == "q=a%7Eb*c"
    assert encode_query([("npi", "1,2")]) == "npi=1%2C2"
    assert encode_query([("q", "x|y")]) == "q=x%7Cy"


def test_true_false_none_lists_and_numbers() -> None:
    assert encode_query([("isVirtual", True), ("includePast", False)]) == "isVirtual=true&includePast=false"
    assert encode_query([("state", None), ("limit", 10)]) == "limit=10"
    assert encode_query([("npi", ["1", "2"]), ("x", ("a",))]) == "npi=1&npi=2&x=a"
    assert encode_query([("one", "abc")]) == "one=abc"  # a str is one value, never its characters
    with pytest.raises(TypeError):
        encode_query([("bad", 1.5)])  # type: ignore[list-item]


def test_path_values_go_out_as_encode_uri_component_writes_them() -> None:
    assert encode_path_value("a/b c") == "a%2Fb%20c"
    assert encode_path_value("!'()*~-_.") == "!'()*~-_."
    assert encode_path_value("H2406-013-000") == "H2406-013-000"


node = shutil.which("node")


@pytest.mark.skipif(node is None, reason="node is not installed")
def test_byte_for_byte_with_javascript() -> None:
    script = (
        "const s = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(s.map((v) => [new URLSearchParams([['k', v]]).toString(),"
        " (() => { try { return encodeURIComponent(v); } catch { return null; } })()])));"
    )
    out = subprocess.run([node, "-e", script], input=json.dumps(SAMPLES), capture_output=True, text=True, check=True)  # type: ignore[list-item]
    for sample, (query, path) in zip(SAMPLES, json.loads(out.stdout), strict=True):
        assert encode_query([("k", sample)]) == query, sample
        if path is not None:  # encodeURIComponent throws on a lone surrogate, where this writes U+FFFD
            assert encode_path_value(sample) == path, sample


def test_a_url_on_another_host_is_refused() -> None:
    base = "https://api.haau3.com"
    assert url_on_base(base, "/v1/scheduling/slots", "a=1") == "https://api.haau3.com/v1/scheduling/slots?a=1"
    assert url_on_base(base, "https://API.haau3.com:443/v1/x") == "https://API.haau3.com:443/v1/x"
    assert url_on_base(base + "/proxy", "/v1/x") == "https://api.haau3.com/proxy/v1/x"
    assert url_on_base(base, "/v1/x?y=1", "z=2") == "https://api.haau3.com/v1/x?y=1&z=2"
    for elsewhere in (
        "https://example.com/v1/x",
        "//example.com/v1/x",
        "http://api.haau3.com/v1/x",
        "https://api.haau3.com:8443/x",
    ):
        with pytest.raises(Haau3Error, match="Refusing to send the API key to"):
            url_on_base(base, elsewhere)


@pytest.mark.parametrize(
    ("wire", "python"),
    [
        ("_count", "count"),
        ("_include", "include"),
        ("_revinclude", "revinclude"),
        ("_id", "id"),
        ("name:contains", "name_contains"),
        ("address-city", "address_city"),
        ("coverage-area-state", "coverage_area_state"),
        ("serviceTypeCode", "service_type_code"),
        ("providerLocationKey", "provider_location_key"),
        ("displayLanguage", "display_language"),
        ("isVirtual", "is_virtual"),
        ("new-patient-from-network", "new_patient_from_network"),
    ],
)
def test_wire_names_map_to_python_names(wire: str, python: str) -> None:
    assert naming.python_name(wire) == python


def test_a_name_python_cannot_take_is_refused() -> None:
    with pytest.raises(ValueError, match="no Python name"):
        naming.python_name("class")
    with pytest.raises(ValueError, match="no Python name"):
        naming.python_name("a.b")


def test_operation_ids_and_docs_slugs() -> None:
    assert naming.method_name("checkInsurancePlanNetwork") == "check_insurance_plan_network"
    assert naming.method_name("fhirBatch") == "fhir_batch"
    assert naming.slug("Check providers against a plan’s network") == "check-providers-against-a-plans-network"
    urls = naming.DocsUrls()
    assert urls("Terminology", "Look up a code") == "https://docs.haau3.com/api-reference/terminology/look-up-a-code"
    assert urls("Terminology", "Look up a code") == "https://docs.haau3.com/api-reference/terminology/look-up-a-code-1"
