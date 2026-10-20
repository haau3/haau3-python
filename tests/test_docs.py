"""The README and api.md name only what exists, and api.md lists every operation once."""

from __future__ import annotations

import re

import naming

import haau3
from haau3 import Haau3, calls

from .helpers import ROOT, SPEC, operations

README = (ROOT / "README.md").read_text()
API = (ROOT / "api.md").read_text()
OPERATIONS = sorted(operations())


def test_every_method_the_readme_calls_exists() -> None:
    called = set(re.findall(r"\bclient\.(?:with_response\.)?(\w+)\(", README))
    assert len(called) > 10
    assert called <= {n for n in dir(Haau3) if not n.startswith("_")}
    for name in re.findall(r"\bcalls\.(\w+)\(", README):
        assert hasattr(calls, name), name
    for name in re.findall(r"(?<![\w./])haau3\.([A-Za-z_]+)\b", README):
        assert hasattr(haau3, name), name


def test_the_readme_calls_one_operation_of_each_service() -> None:
    tags = {op["tags"][0] for _, _, _, op in OPERATIONS} - {"FHIR base"}
    by_method = {naming.method_name(i): op["tags"][0] for i, _, _, op in OPERATIONS}
    shown = {by_method[m] for m in re.findall(r"\bclient\.(\w+)\(", README) if m in by_method}
    assert tags <= shown


def test_the_readme_opens_with_the_preview_sentence_and_the_agent_section() -> None:
    assert (
        "The haau3 platform API is in preview: it may change as we build, usually to add features or improve performance."
        in README
    )
    assert "https://docs.haau3.com/llms.txt" in README
    assert "claude mcp add --transport http haau3 https://docs.haau3.com/mcp" in README


def test_the_readme_says_what_is_checked_and_no_more() -> None:
    assert "The check is of the resource type, not of every element inside it" in README
    assert "always the type it says" not in README


def test_api_md_lists_every_operation_once_with_its_docs_page() -> None:
    rows = re.findall(
        r"^\| \[`(\w+)\(.*?\)`\]\((https://docs\.haau3\.com/api-reference/[\w/-]+)\) \| `(GET|POST) (\S+)` \|",
        API,
        re.M,
    )
    assert sorted(r[0] for r in rows) == sorted(naming.method_name(o[0]) for o in OPERATIONS)
    assert len({r[1] for r in rows}) == len(rows)
    assert {(r[2], r[3]) for r in rows} == {(o[1], o[2]) for o in OPERATIONS}
    assert f"(`{SPEC['info']['version']}`)" in API
    refused = sorted(naming.method_name(o[0]) for o in OPERATIONS if o[3].get("x-haau3-batchable") is False)
    names = ["fhir_batch", *refused]
    excluded = ", ".join(f"`{n}`" for n in names[:-1]) + f" and `{names[-1]}`"
    batchable = [
        o for o in OPERATIONS if o[2].startswith("/v1/fhir/r4/") and o[3].get("x-haau3-batchable") is not False
    ]
    assert f"except {excluded}: {len(batchable)} of the {len(OPERATIONS)}" in " ".join(API.split())


def test_no_em_dash_in_what_this_repository_writes() -> None:
    # api.md is generated from the spec's own words, and published beside the README.
    for path in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "SECURITY.md", "api.md"):
        text = (ROOT / path).read_text()
        assert "\u2014" not in text and "\u2013" not in text, path
