"""What a regeneration PR says and does, over oasdiff 1.33.0's real reports (tests/fixtures/oasdiff/,
each run with openapi/oasdiff-levels.txt over two versions of the spec):

- preview-2-to-3: a path removed and another added;
- descriptions-only: two versions that differ only in descriptions;
- additions: an optional property added;
- method-renamed: preview.3 edited to rename an operation and drop an optional response property;
- security-scheme-removed: a security scheme removed.
"""

from __future__ import annotations

import json
import re

import pytest
from regeneration import MAX_BODY, Change, Regeneration, decide

from .helpers import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "oasdiff"


def fixture(name: str) -> list[Change]:
    return [Change.from_json(c) for c in json.loads((FIXTURES / f"{name}.json").read_text())]


def regeneration(changes: list[Change], generated_changed: bool = True) -> Regeneration:
    return Regeneration(
        from_version="1.0.0-preview.2",
        to_version="1.0.0-preview.3",
        etag='W/"4l7nD8I7mwiHJI4hZ8QHmqfFP1T"',
        spec_url="https://api.haau3.com/openapi.json",
        changes=changes,
        generated_changed=generated_changed,
    )


def test_breaking_when_a_path_is_removed() -> None:
    d = decide(regeneration(fixture("preview-2-to-3")))
    assert (d.title, d.breaking, d.releases) == ("feat!: the API's spec at 1.0.0-preview.3", True, True)
    assert "releases a minor version" in d.body
    assert "`GET /v1/scheduling/actors`: api path removed without deprecation" in d.body
    assert "deleted the `query` request parameter `actorKey`" in d.body


def test_the_three_raised_rules_and_a_warning_count_as_breaking() -> None:
    renamed = fixture("method-renamed")
    assert [(c.id, c.level) for c in renamed] == [
        ("api-operation-id-removed", 3),
        ("response-optional-property-removed", 3),
    ]
    assert decide(regeneration(renamed)).title.startswith("feat!: ")
    assert [c.level for c in fixture("preview-2-to-3") if c.id == "api-schema-removed"] == [3, 3]
    warning = [c for c in fixture("preview-2-to-3") if c.id == "request-parameter-removed"]
    assert [c.level for c in warning] == [2]
    assert decide(regeneration(warning)).breaking


def test_additions_are_a_feature_and_descriptions_a_fix() -> None:
    d = decide(regeneration(fixture("additions")))
    assert (d.title, d.breaking, d.releases) == ("feat: the API's spec at 1.0.0-preview.3", False, True)
    assert "releases a patch version" in d.body
    assert "can break callers (" not in d.body
    assert fixture("descriptions-only") == []
    assert decide(regeneration([])).title == "fix: the API's spec at 1.0.0-preview.3"


def test_nothing_is_released_when_the_generated_code_did_not_change() -> None:
    quiet = decide(regeneration([], generated_changed=False))
    assert (quiet.title, quiet.breaking, quiet.releases) == ("chore: the API's spec at 1.0.0-preview.3", False, False)
    broke = decide(regeneration(fixture("preview-2-to-3"), generated_changed=False))
    assert (broke.title, broke.breaking, broke.releases) == ("chore: the API's spec at 1.0.0-preview.3", True, False)
    assert "releases nothing" in broke.body


def test_a_removed_security_scheme_is_not_breaking() -> None:
    changes = fixture("security-scheme-removed")
    assert sorted(c.id for c in changes) == ["api-global-security-removed", "api-security-component-removed"]
    assert not decide(regeneration(changes)).breaking


def test_the_body_names_both_versions_the_etag_and_the_source() -> None:
    body = decide(regeneration(fixture("additions"))).body
    assert "from `1.0.0-preview.2` to `1.0.0-preview.3`" in body
    assert 'ETag `W/"4l7nD8I7mwiHJI4hZ8QHmqfFP1T"`' in body
    assert "https://api.haau3.com/openapi.json" in body


def test_the_levels_file() -> None:
    assert (ROOT / "openapi" / "oasdiff-levels.txt").read_text().strip().split("\n") == [
        "api-operation-id-removed err",
        "response-optional-property-removed err",
        "api-schema-removed err",
        "api-version-decreased none",
        "api-version-not-bumped none",
        "api-major-version-not-bumped none",
    ]


BODIES = [
    decide(regeneration(fixture(name), generated)).body
    for name in ("preview-2-to-3", "additions", "descriptions-only", "method-renamed", "security-scheme-removed")
    for generated in (True, False)
]


@pytest.mark.parametrize("body", BODIES)
def test_the_body_never_reads_as_a_conventional_commit(body: str) -> None:
    # release-please splits a commit body at any paragraph that starts like this.
    assert not re.search(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.*\))?!?: ", body, re.M)
    assert not re.search(r"BREAKING[ -]CHANGE|Release-As", body, re.I)
    assert "—" not in body


def test_the_body_fits_githubs_limit() -> None:
    many = [
        Change(
            id="response-property-type-changed" if i % 2 else "endpoint-added",
            text=f"the response's `property{i}` changed {'in a long way ' * 20}",
            level=3 if i % 2 else 1,
            operation="GET",
            path=f"/v1/fhir/r4/Resource{i}",
        )
        for i in range(5000)
    ]
    body = decide(regeneration(many)).body
    assert len(body) <= MAX_BODY + 100
    assert "Push the fix to this branch" in body
    assert "Changes that can break callers (2500)" in body
